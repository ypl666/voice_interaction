# -*- coding: utf-8 -*-
import json
import time
import uuid
import requests

from config import *
from joy_inside_py import auth
from joy_inside_py.api_config import URL_AUTH_GET_TOKEN, URL_AUTH_REFRESH_TOKEN


def get_token():
    params = {
        "accessKeyId": ACCESS_KEY,
        "accessTimestamp": str(int(round(time.time() * 1000))),
        "accessNonce": str(uuid.uuid4()),
        "accessVersion": ACCESS_VERSION
    }

    params['accessSign'] = auth.generate_sign(ACCESS_VERSION, params["accessTimestamp"], params["accessNonce"],
                                              ACCESS_KEY, ACCESS_KEY_SECRET)
    params["botId"] = BOT_ID
    res = requests.post(URL_AUTH_GET_TOKEN, json=params)
    if res.status_code != 200:
        print("请求异常", res.status_code)
        return None

    print('获取授权结果：', res.text)
    res_json = json.loads(res.text)
    return res_json["accessToken"]


def refresh_token(refreshToken):
    params = {
        "accessKeyId": ACCESS_KEY,
        "refreshToken": refreshToken,
        "botId": BOT_ID
    }

    res = requests.post(URL_AUTH_REFRESH_TOKEN, json=params)
    if res.status_code != 200:
        print("请求异常", res.status_code)
        return None

    print('刷新授权结果：', res.text)
    res_json = json.loads(res.text)
    return res_json["accessToken"]

