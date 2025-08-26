# -*- coding: utf-8 -*-
import binascii
import hashlib
import hmac


def generate_sign(accessVersion, accessTimestamp, accessNonce, accessKeyId, accessKeySecret):
    params = {
        "accessVersion": accessVersion,
        "accessTimestamp": accessTimestamp,
        "accessNonce": accessNonce,
        "accessKeyId": accessKeyId
    }

    # 将params的key转为小写并排序
    lowerKeyParams = {k.lower(): params[k] for k in params}
    sortedParams = sorted(lowerKeyParams.items(), key=lambda item: item[0])

    # 拼接key=value
    jointParams = '&'.join([f'{k}={str(v)}' for k, v in sortedParams])

    # 计算签名
    h = hmac.new(accessKeySecret.encode('utf-8'), jointParams.encode('utf-8'), digestmod=hashlib.md5)
    return binascii.hexlify(h.digest()).decode('utf-8')
