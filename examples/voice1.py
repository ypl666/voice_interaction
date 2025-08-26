# -*- coding: utf-8 -*-

"""
语音对话示例（保留原始结构）
- 与原始示例相同地组织 botId/sessionId/requestId
- on_open 启动 ping 线程 + 音频发送
- on_message 解析服务端下行，并自动播放 TTS 音频（若存在）
"""

import threading
import uuid
import json
import tempfile
import subprocess
import os
import websocket

from auth_token_demo import get_token
from config import BOT_ID
from joy_inside_py.api_config import URL_VOICE_CHAT
from joy_inside_py.audio_tool import send_audio
from joy_inside_py.event_handler import ping

# 如果没有 ffmpeg/ffplay，这里会提示但不影响收发
def _play_mp3_bytes(mp3_bytes: bytes):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            f.write(mp3_bytes)
            path = f.name
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
    except FileNotFoundError:
        print("[WARN] 未检测到 ffplay（ffmpeg）。无法播放下行音频。")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


class WebsocketHandler:
    # 与原始示例一致：sessionId 用 BOT_ID + uuid4
    sessionId = BOT_ID + str(uuid.uuid4())
    requestId = str(uuid.uuid4())
    uid = ""

    def start(self, uid):
        self.uid = uid
        # 原始示例的 URL 拼接方式（不带 needManualCall 参数；走自动模式由服务端做 VAD）
        ws_url = "%s?botId=%s&sessionId=%s&requestId=%s" % (
            URL_VOICE_CHAT, BOT_ID, self.sessionId, self.requestId
        )

        headers = [f"Authorization: Bearer " + get_token()]

        ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        ws.run_forever()  # 用原始示例的默认调用

    def on_open(self, ws):
        print("[WS] connected:", ws.url)
        # 按原始示例：启动心跳线程
        threading.Thread(target=ping, args=(ws, self.uid), daemon=True).start()
        # 发送音频（改为麦克风优先；audio_tool 内部已处理）
        threading.Thread(target=send_audio, args=(ws, self.uid), daemon=True).start()

    def on_message(self, ws, message):
        # 1) 二进制：大概率是 MP3/PCM 片段，直接尝试播放
        if isinstance(message, (bytes, bytearray)):
            _play_mp3_bytes(message)
            return

        # 2) 文本：尽量做结构化解析
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            print("Received:", message)
            return

        ctype = data.get("contentType")
        body = data.get("content") or data.get("data") or {}

        if ctype in ("ASR", "RESULT_ASR", "ASR_PARTIAL"):
            txt = body.get("text") or body.get("result") or ""
            print("[ASR]", txt)

        elif ctype in ("LLM", "AGENT", "RESULT_TEXT", "TEXT"):
            txt = body.get("content") or body.get("text") or ""
            print("[LLM]", txt)

        elif ctype in ("TTS", "RESULT_AUDIO", "AUDIO"):
            # 下行可能是 base64 音频
            b64 = body.get("audio") or body.get("audioBase64") or body.get("chunk") or ""
            if b64:
                try:
                    import base64
                    mp3 = base64.b64decode(b64)
                    _play_mp3_bytes(mp3)
                except Exception as e:
                    print("[TTS] 解码失败：", e)
            else:
                # 服务端有时分开发 EVENT+二进制下发，这里只是兜底
                print("[TTS] 无音频字段。")

        elif ctype in ("EVENT", "STATE", "PONG"):
            print("[EVENT]", body)

        else:
            print("[MSG][", ctype, "]", json.dumps(data, ensure_ascii=False))

    def on_error(self, ws, error):
        print("Error:", error)

    def on_close(self, ws, close_status_code, close_msg):
        print("WebSocket closed", close_status_code, close_msg)


if __name__ == "__main__":
    userId = "123456"
    handler = WebsocketHandler()
    handler.start(userId)
