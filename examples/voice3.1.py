# -*- coding: utf-8 -*-
"""
语音对话示例（半双工 + 逐句播放队列，持久 ffplay，无缝衔接）
"""

import json
import threading
import uuid
import base64
import subprocess
import queue
import os
import signal
import time
import datetime
import struct
import math
import sys
import os

import websocket

from auth_token_demo import get_token
from config import BOT_ID
from joy_inside_py.api_config import URL_VOICE_CHAT
from joy_inside_py.audio_tool3 import send_audio
from joy_inside_py.event_handler import ping

# 清除可能的模块缓存
if 'joy_inside_py.audio_tool3' in sys.modules:
    del sys.modules['joy_inside_py.audio_tool3']

class WebsocketHandler:
    # 按官方示例：sessionId = BOT_ID + UUID
    sessionId = BOT_ID + str(uuid.uuid4())
    requestId = str(uuid.uuid4())
    uid = ""

    def __init__(self):
        # 半双工：对方在说话→暂停我方推流
        self.agent_speaking = threading.Event()
        self.want_interrupt = threading.Event()

        # TTS 逐句缓冲与播放
        self._tts_cur = bytearray()           # 当前句的缓冲
        self._tts_lock = threading.Lock()
        self._tts_queue = queue.Queue(maxsize=200)  # 待播句队列（bytes）

        # 持久 ffplay
        self._ffplay = None
        self._ffplay_lock = threading.Lock()
        self._player_started = False
        self._player_lock = threading.Lock()

        # 供 send_audio 使用的 ws 引用
        self._ws_ref_lock = threading.Lock()
        self._ws_ref = None
        
        # 性能监测
        self.performance_metrics = {
            "user_speech_end_time": None,
            "ai_speech_start_time": None,
            "response_times": []
        }
        self.current_round_id = None
        
        # 音频处理相关
        self.audio_buffer = bytearray()
        self.silence_threshold = 500  # 静音阈值，可根据实际情况调整
        self.silence_duration = 0.5  # 静音持续时间（秒）
        self.silence_samples = 0  # 连续静音样本计数
        self.sample_rate = 16000  # 假设采样率为16kHz
        self.sample_width = 2  # 假设样本宽度为2字节（16位）
        self.channels = 1  # 单声道

    # ---------- 性能监测 ----------
    def _record_user_speech_end(self):
        """记录用户说话结束的时间"""
        self.performance_metrics["user_speech_end_time"] = time.time()
        print(f"[PERF] User speech ended at: {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    def _record_ai_speech_start(self):
        """记录AI开始说话的时间"""
        self.performance_metrics["ai_speech_start_time"] = time.time()
        print(f"[PERF] AI speech started at: {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        
        # 计算响应时间
        if self.performance_metrics["user_speech_end_time"] is not None:
            response_time = self.performance_metrics["ai_speech_start_time"] - self.performance_metrics["user_speech_end_time"]
            self.performance_metrics["response_times"].append(response_time)
            print(f"[PERF] Response time: {response_time:.3f} seconds")
            
            # 打印统计信息
            if len(self.performance_metrics["response_times"]) > 0:
                avg_time = sum(self.performance_metrics["response_times"]) / len(self.performance_metrics["response_times"])
                print(f"[PERF] Average response time: {avg_time:.3f} seconds")
                print(f"[PERF] Total responses: {len(self.performance_metrics['response_times'])}")

    # ---------- 音频处理 ----------
    def _calculate_rms(self, data):
        """计算音频数据的RMS（均方根）值"""
        # 将字节数据转换为16位整数
        count = len(data) // self.sample_width
        format_str = f"{count}h"  # 例如 "1920h" 表示1920个16位整数
        
        try:
            # 将字节数据解包为整数列表
            shorts = struct.unpack(format_str, data)
            
            # 计算平方和
            sum_squares = 0.0
            for sample in shorts:
                # 归一化到[-1, 1]范围
                normalized = sample / 32768.0
                sum_squares += normalized * normalized
                
            # 计算RMS
            rms = math.sqrt(sum_squares / count)
            # 将RMS值转换为0-32767的范围
            return rms * 32767
        except struct.error:
            return 0

    def _process_audio_chunk(self, audio_data):
        """
        处理音频块，检测语音结束
        audio_data: 音频数据字节
        """
        # 计算音频能量
        rms = self._calculate_rms(audio_data)
        
        # 检测是否为静音
        if rms < self.silence_threshold:
            self.silence_samples += len(audio_data) // self.sample_width
        else:
            self.silence_samples = 0
        
        # 如果静音持续时间超过阈值，则认为语音结束
        silence_seconds = self.silence_samples / self.sample_rate
        if silence_seconds >= self.silence_duration:
            # 记录用户说话结束的时间
            if self.performance_metrics["user_speech_end_time"] is None:
                self._record_user_speech_end()
                # 重置静音计数
                self.silence_samples = 0
    
    # ---------- 音频推流门控 / 打断 ----------
    def gate_can_send(self) -> bool:
        return not self.agent_speaking.is_set()

    def request_interrupt(self):
        with self._ws_ref_lock:
            ws = self._ws_ref
        if ws is None:
            return
        if not self.want_interrupt.is_set():
            self.want_interrupt.set()
            # 清空音频队列并停止当前播放
            self._clear_audio_queue()
            # 创建正确的 CLIENT_INTERRUPT 消息格式
            mid = str(uuid.uuid4())
            payload = json.dumps({
                "mid": mid,
                "contentType": "CLIENT_INTERRUPT",
                "uid": self.uid
            })
            try:
                ws.send(payload)
                print("[TX-TEXT]", payload)
                print("[CLIENT_INTERRUPT] sent")
            except Exception as e:
                print("[CLIENT_INTERRUPT][ERR]", e)

    def _clear_audio_queue(self):
        """清空音频队列并停止当前播放"""
        # 清空当前缓冲
        with self._tts_lock:
            self._tts_cur.clear()
        
        # 清空队列
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
                self._tts_queue.task_done()
            except queue.Empty:
                break
        
        # 停止 ffplay 并重新启动
        self._stop_ffplay()
        self._start_ffplay()

    # ---------- 持久播放器 ----------
    def _start_ffplay(self):
        """启动唯一的 ffplay 进程，持续写入 stdin。"""
        with self._ffplay_lock:
            if self._ffplay and self._ffplay.poll() is None:
                return
            try:
                # -autoexit 会在 stdin EOF 才退出；我们不关闭 stdin，保持常驻
                self._ffplay = subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-f", "mp3", "-i", "pipe:0"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print("[FFPLAY] started pid:", self._ffplay.pid)
            except FileNotFoundError:
                print("[FFPLAY][ERR] 未找到 ffplay，请确认已安装并在 PATH 中。")
            except Exception as e:
                print("[FFPLAY][ERR]", e)

    def _stop_ffplay(self):
        with self._ffplay_lock:
            if self._ffplay:
                try:
                    # 不写入 EOF，直接结束进程
                    if os.name == "nt":
                        self._ffplay.terminate()
                    else:
                        os.kill(self._ffplay.pid, signal.SIGTERM)
                except Exception:
                    pass
                try:
                    self._ffplay.wait(timeout=2)
                except Exception:
                    pass
                self._ffplay = None
                print("[FFPLAY] stopped")

    def _player_loop(self):
        """顺序消费句队列，把每句 MP3 直接写入持久 ffplay 的 stdin。"""
        self._start_ffplay()
        while True:
            # 检查是否处于打断状态，如果是则跳过当前音频
            if self.want_interrupt.is_set():
                time.sleep(0.1)  # 短暂休眠以减少CPU占用
                continue
                
            sentence_mp3 = self._tts_queue.get()
            try:
                with self._ffplay_lock:
                    # 如果 ffplay 意外退出，重启它
                    if self._ffplay is None or self._ffplay.poll() is not None:
                        self._start_ffplay()
                    if self._ffplay and self._ffplay.stdin:
                        # 直接写，不关闭，不 flush 强制也可（mp3足够大时自动冲刷）
                        self._ffplay.stdin.write(sentence_mp3)
                        self._ffplay.stdin.flush()
                # 你也可以在两句之间加上极小的停顿（比如 5~15ms），通常不需要
                # time.sleep(0.005)
            except Exception as e:
                print("[PLAYER][ERR]", e)
                # 出错时尝试重启
                self._stop_ffplay()
                self._start_ffplay()
            finally:
                self._tts_queue.task_done()

    def _ensure_player(self):
        with self._player_lock:
            if not self._player_started:
                t = threading.Thread(target=self._player_loop, daemon=True)
                t.start()
                self._player_started = True

    # ---------- WebSocket ----------
    def start(self, uid):
        self.uid = uid
        ws_url = "%s?botId=%s&sessionId=%s&requestId=%s" % (
            URL_VOICE_CHAT, BOT_ID, self.sessionId, self.requestId
        )
        ws = websocket.WebSocketApp(
            ws_url,
            header=[f"Authorization: Bearer " + get_token()],
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        ws.run_forever()

    def on_open(self, ws):
        print("[WS] connected:", ws.url)
        with self._ws_ref_lock:
            self._ws_ref = ws
        # 心跳
        threading.Thread(target=ping, args=(ws, self.uid), daemon=True).start()
        # 播放线程
        self._ensure_player()
        # 采麦推流（半双工）- 直接使用修改后的send_audio函数
        threading.Thread(
            target=send_audio,
            args=(ws, self.uid),
            kwargs={
                "gate_can_send": self.gate_can_send,
                "request_interrupt": self.request_interrupt,
                "audio_callback": self._process_audio_chunk  # 添加音频回调
            },
            daemon=True
        ).start()

    # ---------- TTS 句边界缓冲 ----------
    def _enqueue_prev_sentence_if_any(self):
        with self._tts_lock:
            if self._tts_cur:
                # 入队上一句，避免被覆盖
                self._tts_queue.put(bytes(self._tts_cur))
                self._tts_cur.clear()

    def _finish_current_sentence(self):
        with self._tts_lock:
            if self._tts_cur:
                self._tts_queue.put(bytes(self._tts_cur))
                self._tts_cur.clear()

    # ---------- 消息分发 ----------
    def on_message(self, ws, message):
        if isinstance(message, (bytes, bytearray)):
            # 二进制：TTS mp3 分片
            # 如果处于打断状态，忽略接收到的音频数据
            if self.want_interrupt.is_set():
                return
            with self._tts_lock:
                self._tts_cur.extend(message)
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            print("Received:", message)
            return

        ctype = data.get("contentType")
        body = data.get("content") or data.get("data") or {}

        if ctype == "EVENT":
            ev = body.get("eventType")
            if ev == "TTS_SENTENCE_START":
                # 对方开始说：进入"对方说话"状态
                self.agent_speaking.set()
                self.want_interrupt.clear()  # 清除打断状态
                # 新句开始前，若上一句已积累音频但未 complete，先入队
                self._enqueue_prev_sentence_if_any()
                txt = (body.get("text") or body.get("eventData", {}).get("text") or "").strip()
                if txt:
                    print("[TTS_START]", txt)
                
                # 记录AI开始说话的时间
                self._record_ai_speech_start()
                return

            if ev == "INTERRUPT":
                # 服务器发送的打断事件
                print("[EVENT][INTERRUPT] received, clear audio queue")
                self.want_interrupt.set()
                self._clear_audio_queue()
                return

            if ev in ("TTS_COMPLETE", "TTS_SENTENCE_COMPLETE", "COMPLETE"):
                # 本句（或整个轮次）结束：把当前句入队
                self._finish_current_sentence()
                if ev == "COMPLETE":
                    # 整轮结束：允许我方重新说话
                    self.agent_speaking.clear()
                print("[EVENT]", body)
                return

            print("[EVENT]", body)
            return

        if ctype in ("ASR", "RESULT_ASR", "ASR_PARTIAL"):
            text = body.get("text") or body.get("result") or ""
            if text:
                print("[ASR]", text)
                # 重置用户说话结束时间，避免重复记录
                self.performance_metrics["user_speech_end_time"] = None
            return

        if ctype in ("LLM", "AGENT", "RESULT_TEXT", "TEXT"):
            text = body.get("content") or body.get("text") or ""
            if text:
                print("[LLM]", text)
            return

        if ctype in ("TTS", "RESULT_AUDIO", "AUDIO"):
            # 如果处于打断状态，忽略接收到的音频数据
            if self.want_interrupt.is_set():
                return
            # JSON base64 音频也归入当前句缓冲
            b64 = body.get("audio") or body.get("audioBase64") or body.get("chunk")
            if b64:
                try:
                    chunk = base64.b64decode(b64)
                    with self._tts_lock:
                        self._tts_cur.extend(chunk)
                except Exception as e:
                    print("[TTS][b64-decode][ERR]", e)
            return

        print("[MSG][", ctype, "]", json.dumps(data, ensure_ascii=False))

    def on_error(self, ws, error):
        print("[ERROR]", error)

    def on_close(self, ws, close_status_code, close_msg):
        print("[CLOSED]", close_status_code, close_msg)
        # 打印最终性能统计
        if self.performance_metrics["response_times"]:
            avg_time = sum(self.performance_metrics["response_times"]) / len(self.performance_metrics["response_times"])
            print(f"[PERF] Final average response time: {avg_time:.3f} seconds")
            print(f"[PERF] Total responses measured: {len(self.performance_metrics['response_times'])}")
        self._stop_ffplay()


if __name__ == "__main__":
    userId = "123456"
    handler = WebsocketHandler()
    handler.start(userId)