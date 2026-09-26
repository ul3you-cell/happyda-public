#!/usr/bin/env python3
"""唯讀呼叫官方 Uniswap Liquidity API `/lp/pool_info`。

安全規則（硬性）：
- API key 只從環境變數 UNISWAP_API_KEY 讀取，絕不 print/log/寫入任何檔案。
- 只送出 poolParameters / poolReferences 查詢；不簽名、不 approve、不 swap。
- 429 時依退避重試；文件未公布 RPM，不自行假設一個數字當作保證值。

用法：
    UNISWAP_API_KEY=xxx python3 fetch_pool_info.py [--limit N]

輸出：
    data/snapshot-<UTC時間戳>.json   本次原始回應（含成功與失敗紀錄）
    data/latest_raw.json             指向最新一次快照的複本
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from itertools import product

from common import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_RETRIES,
    DATA_DIR,
    ENDPOINT,
    REQUEST_MIN_INTERVAL_SECONDS,
    load_config,
)


def build_queries(config: dict) -> list[dict]:
    queries: list[dict] = []
    tick_spacing_by_fee = {int(k): v for k, v in config["tick_spacing_by_fee"].items()}
    hook_default = config["v4_hook_address_default"]

    for chain in config["chains"]:
        cid = chain["chain_id"]
        for protocol, fee, base, quote in product(
            config["protocols"], config["fee_tiers"], chain["base_tokens"], chain["quote_tokens"]
        ):
            body = {
                "protocol": protocol,
                "chainId": cid,
                "poolParameters": {
                    "tokenAddressA": base["address"],
                    "tokenAddressB": quote["address"],
                    "fee": fee,
                },
            }
            if protocol == "V4":
                body["poolParameters"]["tickSpacing"] = tick_spacing_by_fee.get(fee, 60)
                body["poolParameters"]["hookAddress"] = hook_default
            queries.append({
                "query_type": "poolParameters",
                "chain_id": cid,
                "chain_name": chain["chain_name"],
                "protocol": protocol,
                "base_symbol": base["symbol"],
                "quote_symbol": quote["symbol"],
                "fee": fee,
                "body": body,
            })

    for ref in config.get("special_pool_references", []):
        for protocol in ref.get("protocol_guess_order", ["V3", "V4"]):
            body = {
                "protocol": protocol,
                "chainId": ref["chain_id"],
                # 官方 schema 是 poolReferences[0].referenceIdentifier（不是
                # poolReferenceIdentifier）；用錯欄位名時伺服器視為空值，回
                # 400 invalid_argument（t_bf8f4d3e review：anne 實測 Unichain
                # 兩筆皆因此壞掉）。見 tests/test_offline.py 的 regression。
                "poolReferences": [{"referenceIdentifier": ref["pool_reference_identifier"]}],
            }
            queries.append({
                "query_type": "poolReference",
                "chain_id": ref["chain_id"],
                "chain_name": ref["chain_name"],
                "protocol": protocol,
                "pool_reference_identifier": ref["pool_reference_identifier"],
                "body": body,
            })

    return queries


def call_api(body: dict, api_key: str) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "x-agent-info": json.dumps({
                "decision_origin": "human_mediated",
                "integration_name": "happyda-public/uniswap-lp-tracker",
            }),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return -1, str(e.reason)


def fetch_with_backoff(body: dict, api_key: str) -> tuple[int, dict | str]:
    attempt = 0
    while True:
        status, payload = call_api(body, api_key)
        if status != 429 or attempt >= BACKOFF_MAX_RETRIES:
            return status, payload
        sleep_s = BACKOFF_BASE_SECONDS * (2 ** attempt)
        time.sleep(sleep_s)
        attempt += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 筆查詢（測試用）")
    args = parser.parse_args()

    api_key = None
    import os
    api_key = os.environ.get("UNISWAP_API_KEY")
    if not api_key:
        print(
            "ERROR: 未設定環境變數 UNISWAP_API_KEY，本腳本不會用假資料代替。"
            " 請在自己的環境設定後重試（本工具絕不讀取/顯示 key 本身）。",
            file=sys.stderr,
        )
        return 2

    config = load_config()
    queries = build_queries(config)
    if args.limit:
        queries = queries[: args.limit]

    results = []
    for q in queries:
        status, payload = fetch_with_backoff(q["body"], api_key)
        results.append({
            **{k: v for k, v in q.items() if k != "body"},
            "request_body": q["body"],
            "http_status": status,
            "response": payload,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
        time.sleep(REQUEST_MIN_INTERVAL_SECONDS)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = DATA_DIR / f"snapshot-{ts}.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "results": results}, f, ensure_ascii=False, indent=2)

    latest_path = DATA_DIR / "latest_raw.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "results": results}, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r["http_status"] == 200)
    print(f"完成：{len(results)} 筆查詢，{ok} 筆 HTTP 200。快照：{snapshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
