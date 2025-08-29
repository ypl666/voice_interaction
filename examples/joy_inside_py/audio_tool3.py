# -*- coding: utf-8 -*-
"""
麦克风推流（JSON + base64），支持半双工：
- gate_can_send(): 对方说话时返回 False → 我方暂停推流
- request_interrupt(): 我在对方说话时开口 → 先发 CLIENT_INTERRUPT 再继续
依赖: sounddevice, numpy
"""

import time
import queue
import json
import base64
import numpy as np
import uuid

from joy_inside_py.api_config import BYTES_PER_FRAME, FRAME_MS

# 采样参数
SR = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # PCM16
FRAME_SAMPLES = max(1, BYTES_PER_FRAME // (SAMPLE_WIDTH * CHANNELS))

# 语音检测 & 结束判定
ENERGY_THRESH = 0.01     # 开口门限
SILENCE_MS = 700         # 判定"说完"的静音时间
INTERRUPT_DEBOUNCE_MS = 800  # 打断节流（避免狂发）

try:
    import sounddevice as sd
except Exception as e:
    sd = None
    print("[send_audio] sounddevice 导入失败：", e)


def _float32_to_pcm16_bytes(block: np.ndarray) -> bytes:
    block = np.clip(block, -1.0, 1.0)
    int16 = (block * 32767.0).astype(np.int16)
    raw = int16.tobytes()
    if len(raw) > BYTES_PER_FRAME:
        raw = raw[:BYTES_PER_FRAME]
    elif len(raw) < BYTES_PER_FRAME:
        raw = raw + b"\x00" * (BYTES_PER_FRAME - len(raw))
    return raw


def _rms(block: np.ndarray) -> float:
    return float(np.sqrt((block.astype(np.float32) ** 2).mean() + 1e-12))


def _json_audio_frame(uid: str, index: int, b64: str) -> str:
    return json.dumps({
        "mid": str(uuid.uuid4()),
        "contentType": "AUDIO",
        "content": {
            "audioBase64": b64,
            "index": index
        },
        "uid": uid
    }, ensure_ascii=False)


def _json_client_finish() -> str:
    return json.dumps({"contentType": "CLIENT_AUDIO_FINISH"})


def _json_client_start(uid: str) -> str:
    # 有些服务端在 needManualCall=true 模式下建议显式声明开始
    return json.dumps({"mid": str(uuid.uuid4()), "contentType": "CLIENT_AUDIO_START", "uid": uid})


def send_audio(ws,
               uid: str,
               gate_can_send=None,        # -> bool，None 表示永远允许发送
               request_interrupt=None,    # -> callable()，我方在对方说话时开口；可为 None
               audio_callback=None        # -> callable(bytes) -> bytes，音频处理回调；可为 None
               ):
    """
    半双工推流主循环：
    - gate_can_send() 为 False → 不推帧；若此时能量 > 阈值，且提供了 request_interrupt()，则打断一次
    - 进入"说话"状态后持续推帧，静音 >= SILENCE_MS 时只发一次 CLIENT_AUDIO_FINISH
    - audio_callback() 可用于处理音频数据，如检测语音结束
    """
    if sd is None:
        print("[send_audio] 未检测到 sounddevice，无法采集麦克风。")
        return

    q = queue.Queue(maxsize=50)

    def _cb(indata, frames, time_info, status):
        if status:
            pass
        try:
            q.put_nowait(indata.copy())
        except queue.Full:
            pass

    with sd.InputStream(samplerate=SR, channels=CHANNELS, blocksize=FRAME_SAMPLES,
                        dtype='float32', callback=_cb):
        print(f"[send_audio] 推流开始：{SR}Hz, {CHANNELS}ch, 帧≈{FRAME_MS}ms（{BYTES_PER_FRAME}B/帧）")

        talking = False
        last_voice_ts = time.time()
        index = 0
        last_interrupt_ts = 0.0

        frame_interval = FRAME_MS / 1000.0
        last_sent = time.time()

        while True:
            try:
                block = q.get(timeout=1.0)
            except queue.Empty:
                continue

            energy = _rms(block)
            now = time.time()

            # 如果有音频回调，处理音频数据
            pcm = _float32_to_pcm16_bytes(block)
            if audio_callback:
                try:
                    audio_callback(pcm)
                except Exception as e:
                    print("[AUDIO_CALLBACK][ERR]", e)

            # —— 半双工门控：对方在讲，我方先别发；若我确实开口可请求打断 ——
            can_send = True if gate_can_send is None else bool(gate_can_send())
            if not can_send:
                if energy > ENERGY_THRESH and request_interrupt is not None:
                    if (now - last_interrupt_ts) * 1000 >= INTERRUPT_DEBOUNCE_MS:
                        request_interrupt()
                        last_interrupt_ts = now
                time.sleep(0.01)
                continue  # 不推流

            # —— 我方开口的起点（从静默进入说话）——
            if not talking and energy > ENERGY_THRESH:
                talking = True
                last_voice_ts = now
                index = 0
                # 可选：声明开始（服务端如有建议）
                start_msg = _json_client_start(uid)
                ws.send(start_msg)
                print("[TX-TEXT]", start_msg)
                print("[send_audio] CLIENT_AUDIO_START")

            if not talking:
                # 还没开口
                time.sleep(0.005)
                continue

            # —— 持续推帧 —— 
            b64 = base64.b64encode(pcm).decode("ascii")
            payload = _json_audio_frame(uid, index, b64)
            ws.send(payload)
            if index % 10 == 0:
                print(f"序号: {index}, 音频字节数: {len(pcm)}")
            index += 1

            if energy > ENERGY_THRESH:
                last_voice_ts = now

            # —— 结束判定：静音超阈值 → 只发一次 FINISH —— 
            if (now - last_voice_ts) * 1000 >= SILENCE_MS:
                ws.send(_json_client_finish())
                print("[send_audio] CLIENT_AUDIO_FINISH")
                talking = False
                # 给服务端一点收尾时间，避免尾部噪声又被当成新一句
                time.sleep(0.15)

            # 节奏对齐
            now2 = time.time()
            sleep_left = frame_interval - (now2 - last_sent)
            if sleep_left > 0:
                time.sleep(sleep_left)
            last_sent = time.time()