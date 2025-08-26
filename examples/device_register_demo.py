# -*- coding: utf-8 -*-

"""
设备注册示例
参考API文档：https://joyinside.jdcloud.com/docs/#/zh-cn/device/device_register
"""

import json
import time
import uuid
import requests

from config import *
from joy_inside_py import auth
from joy_inside_py.api_config import URL_AUTH_GET_TOKEN, URL_DEVICE_REGISTER


def register_bot():
    authorization = get_vendor_token()
    headers = {"Authorization": "Bearer " + authorization}
    params = {
        "vendorId": VENDOR_ID,
        "appId": APP_ID,
        "deviceId": "TEST_DEVICE_SN",
        "type": "APP_ROBOT",
        "name": "测试"
    }

    res = requests.post(URL_DEVICE_REGISTER, headers=headers, json=params)
    if res.status_code != 200:
        print("请求异常", res.status_code)
        return None

    res_json = json.loads(res.text)
    return res_json["data"]


def get_vendor_token():
    params = {"accessKeyId": ACCESS_KEY, "accessTimestamp": str(int(round(time.time() * 1000))),
              "accessNonce": str(uuid.uuid4()), "accessVersion": ACCESS_VERSION}
    params['accessSign'] = auth.generate_sign(ACCESS_VERSION, params["accessTimestamp"], params["accessNonce"],
                                              ACCESS_KEY, ACCESS_KEY_SECRET)
    params["vendorId"] = VENDOR_ID
    res = requests.post(URL_AUTH_GET_TOKEN, json=params)
    if res.status_code != 200:
        print("请求异常", res.status_code)
        return None

    res_json = json.loads(res.text)
    return res_json["accessToken"]


if __name__ == '__main__':
    print(register_bot())

