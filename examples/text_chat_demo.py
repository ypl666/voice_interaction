# -*- coding: utf-8 -*-

"""
文本对话示例
参考API文档：https://joyinside.jdcloud.com/docs/#/zh-cn/chat/textChat
"""

import json
import uuid

import requests

from auth_token_demo import get_token
from config import *
from joy_inside_py.api_config import URL_TEXT_CHAT


def chat():
    authorization = get_token()
    headers = {"Authorization": "Bearer " + authorization}
    params = {
        "requestId": str(uuid.uuid4()),
        "botId": BOT_ID,
        "messages": [
            {
                "role": "user",
                "content": "你是joyinside智能助手，请回答我的问题。每次回复不超过50字"
            },
            {
                "role": "assistant",
                "content": "你好，我是joyinside智能助手，很高兴为你服务。请问您有什么问题需要我帮助解答的？"
            },
            {
                "role": "user",
                "content": "给我讲个笑话"
            }
        ]
    }

    res = requests.post(URL_TEXT_CHAT, stream=True, headers=headers, json=params)
    if res.status_code != 200:
        print("请求异常", res.status_code)
        return

    for line_str in res.iter_lines(decode_unicode=True):
        if line_str.startswith('data:'):
            try:
                data = json.loads(line_str[5:].strip())
                finish_reason = data["choices"][0]["finish_reason"]
                if "stop" == finish_reason:
                    print("回复结束")
                    return

                print("回复文本： ", data["choices"][0]["delta"]["content"])
            except json.JSONDecodeError:
                print(f"JSON解析错误: {line_str}")



if __name__ == '__main__':
    chat()
