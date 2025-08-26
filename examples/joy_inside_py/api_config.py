# -*- coding: utf-8 -*-


URL_AUTH_GET_TOKEN = "https://joyinside.jd.com/auth/getToken"
URL_AUTH_REFRESH_TOKEN = "https://joyinside.jd.com/auth/refreshToken"
URL_DEVICE_REGISTER = "https://joyinside.jd.com/device/register"
URL_TEXT_CHAT = "https://joyinside.jd.com/soulmate/chat/v1"
URL_VOICE_CHAT = "wss://joyinside.jd.com/soulmate/voiceChat/v1"


BYTES_PER_MS = 16000 * 2 / 1000  # 16000的采样率，16bits=2bytes， 1000ms
FRAME_MS = 120  # websocket一个数据帧
BYTES_PER_FRAME = int(BYTES_PER_MS * FRAME_MS)  # 一个数据帧的大小
