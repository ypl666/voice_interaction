# -*- coding: utf-8 -*-
"""
麦克风实时推流（若无麦克风依赖，则退回读取 test.pcm 文件）
上行格式严格保持官方示例：

{
  "mid": "<uuid>",
  "contentType": "AUDIO",
  "content": {
    "audioBase64": "<base64 of PCM s16le 16kHz mono>",
    "index": <int>   # 逐帧自增；当退回到文件模式时，最后一帧会用 index=~index 标记结束
  },
  "uid": "<uid>"
}
"""

import base64
import json
import time
import uuid
import threading
import queue
import os

from joy_inside_py.api_config import BYTES_PER_FRAME, FRAME_MS

# 你原来用于文件回放的 PCM
PCM_FILE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "test.pcm")

# 采样参数（与服务端 CFG_BOT_EVENT 里一致：16000Hz, mono, 16bit）
SR = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # int16
FRAME_SAMPLES = max(1, BYTES_PER_FRAME // (SAMPLE_WIDTH * CHANNELS))

# 发送加锁，避免 ping 与音频并发写 socket
_WS_SEND_LOCK = threading.Lock()

def _safe_send(ws, text: str):
    with _WS_SEND_LOCK:
        ws.send(text)

# ---------- 麦克风路径（优先） ----------
try:
    import sounddevice as sd
    import numpy as np
    _HAS_MIC = True
except Exception as e:
    sd = None
    np = None
    _HAS_MIC = False
    print("[send_audio] 未检测到 sounddevice 或 numpy，将退回文件回放模式：", e)

def _bytes_from_block_int16(block) -> bytes:
    """block 为 numpy.int16 的 (N,1) 或 (N,)；裁剪/填充到 BYTES_PER_FRAME。"""
    raw = block.tobytes(order="C")
    if len(raw) > BYTES_PER_FRAME:
        raw = raw[:BYTES_PER_FRAME]
    elif len(raw) < BYTES_PER_FRAME:
        raw = raw + b"\x00" * (BYTES_PER_FRAME - len(raw))
    return raw

def _send_audio_frame(ws, uid, index: int, raw: bytes):
    audio_base64 = base64.b64encode(raw).decode("ascii")
    params = {
        "mid": str(uuid.uuid4()),
        "contentType": "AUDIO",
        "content": {
            "audioBase64": audio_base64,
            "index": index
        },
        "uid": uid
    }
    _safe_send(ws, json.dumps(params, ensure_ascii=False))

def _stream_from_mic(ws, uid: str):
    """实时采集麦克风，自动按 BYTES_PER_FRAME 发送。"""
    q = queue.Queue(maxsize=50)

    def _cb(indata, frames, time_info, status):
        try:
            q.put_nowait(indata.copy())
        except queue.Full:
            pass

    # 直接采集 int16，避免浮点到 int16 的额外转换
    with sd.InputStream(samplerate=SR,
                        channels=CHANNELS,
                        blocksize=FRAME_SAMPLES,
                        dtype='int16',
                        callback=_cb):
        print(f"[send_audio] 推流开始：{SR}Hz, {CHANNELS}ch, 帧≈{FRAME_MS}ms（{BYTES_PER_FRAME}B/帧）")
        index = 0
        frame_interval = FRAME_MS / 1000.0
        last_sent = time.time()

        while True:
            try:
                block = q.get(timeout=1.0)  # numpy.int16
            except queue.Empty:
                continue

            raw = _bytes_from_block_int16(block)
            if index % 10 == 0:
                print(f"序号: {index}, 音频字节数: {len(raw)}")
            _send_audio_frame(ws, uid, index, raw)
            index += 1

            # 以 FRAME_MS 为节奏
            now = time.time()
            sleep_left = frame_interval - (now - last_sent)
            if sleep_left > 0:
                time.sleep(sleep_left)
            last_sent = time.time()

# ---------- 文件模式（回退） ----------
def _stream_from_file(ws, uid: str):
    """与原始示例一致：从 test.pcm 读取，最后一帧 index 取反。"""
    if not os.path.exists(PCM_FILE_PATH):
        print(f"[send_audio] 未找到 {PCM_FILE_PATH}，无法回放。请安装 sounddevice 或放入该文件。")
        return

    with open(PCM_FILE_PATH, 'rb') as f:
        index = 0
        while True:
            data = f.read(BYTES_PER_FRAME)
            if not data:
                break
            if len(data) < BYTES_PER_FRAME:
                # 最后一帧：按官方约定用 取反 标记
                index = ~index
            print(f"序号: {index}, 音频字节数: {len(data)}")
            _send_audio_frame(ws, uid, index, data)
            index += 1
            time.sleep(FRAME_MS / 1000.0)

# ---------- 对外主函数（与原型同名/同签名） ----------
def send_audio(ws, uid):
    """
    与原始示例保持相同签名：send_audio(ws, uid)
    默认优先走麦克风；若依赖不可用或无麦克风，则退回 test.pcm 文件模式。
    """
    try:
        if _HAS_MIC:
            _stream_from_mic(ws, uid)
        else:
            _stream_from_file(ws, uid)
    except Exception as e:
        print("[send_audio] 采集/发送过程中出现错误：", e)

