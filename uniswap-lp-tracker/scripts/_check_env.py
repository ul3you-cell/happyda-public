#!/usr/bin/env python3
"""一次性檢查：載入 dev-claude profile .env 內的 ALCHEMY_API_KEY / GRAPH_API_KEY，
只回報是否存在＋長度，絕不印出值本身。"""
import os

ENV_PATH = "/Users/wangshaoyu/.hermes/profiles/dev-claude/.env"
KEYS = ["ALCHEMY_API_KEY", "GRAPH_API_KEY"]

found = {}
with open(ENV_PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k in KEYS and v:
            found[k] = len(v)

for k in KEYS:
    print(f"{k}: {'FOUND len=' + str(found[k]) if k in found else 'MISSING'}")
