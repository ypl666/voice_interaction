# -*- coding: utf-8 -*-

import json
import time
import uuid


def ping(ws, uid):
    PING = {"mid": str(uuid.uuid4()), "contentType": "PING", "uid": uid}
    while True:
        ws.send(json.dumps(PING))
        time.sleep(10)


def send_event_data(ws, uid, evenType, *args):
    event_param = {
        "mid": str(uuid.uuid4()),
        "contentType": "EVENT",
        "uid": uid,
        "content": {
            "eventType": evenType
        }
    }
    ws.send(json.dumps(event_param))
