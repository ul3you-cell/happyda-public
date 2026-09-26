#!/usr/bin/env python3
"""唯讀呼叫 The Graph gateway 的官方 Uniswap v3 subgraph，批次抓取
「池子級」TVL／每日 volume／每日 fees，換算成儀表板要顯示的 TVL／
24h·7d volume／24h·7d fee APR／收入變化方向。

範圍（硬性，不擴大）：
- 只涵蓋 chain_id=1（Ethereum mainnet）——這是目前唯一由 @research 交付、
  逐字抄自官方文件確認過的 v3 subgraph deployment ID
  （common.GRAPH_V3_DEPLOYMENT_ID_BY_CHAIN）。Arbitrum/Base/BNB 的
  deployment ID 尚未交付，本腳本遇到其他 chain_id 一律跳過、不猜 ID。
- 只算「池子」級指標，不是錢包 LP 部位——v3 subgraph 沒有 wallet Position
  entity（@research 已用 schema 原文確認），錢包部位/每日 delta/個別
  wallet APR 走另一條 NFT RPC 路線，不在本腳本範圍。

安全規則（硬性，比照 fetch_pool_info.py / graph_gateway_check.py）：
- key 只從環境變數 GRAPH_API_KEY 讀取（common.require_graph_api_key()），
  絕不 print/log key 本身；輸出/錯誤訊息一律先做 key 遮蔽。
- 只送 pool / poolDayDatas 這兩個唯讀 query，不 mutate 任何鏈上狀態。

用法：
    KEY="$(security find-generic-password -a GRAPH_API_KEY -s uniswap-tracker -w)" \\
      GRAPH_API_KEY="$KEY" python3 scripts/fetch_pool_metrics.py
    unset KEY

輸出：
    data/graph_pool_day_data.json —— {pool_address_lower: {...compute_pool_day_metrics 輸出...}}
    normalize.py 會在下一次執行時讀這個檔案，把 chain_id=1 的池子列補上
    真實 TVL/volume/fee APR（見 normalize.py 的 merge_graph_enrichment）。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from common import DATA_DIR, GRAPH_V3_DEPLOYMENT_ID_BY_CHAIN, compute_pool_day_metrics, require_graph_api_key
from graph_gateway_check import GATEWAY_BASE, REQUEST_HEADERS

# 一次查詢最多帶幾顆池子的 alias（避免單一 query 過大、也降低單次請求的
# 免費 tier 用量衝擊；純工程選擇，非官方限制值）。
BATCH_SIZE = 20
DAY_DATA_LOOKBACK = 8  # 7 天窗口 + 1 天緩衝（當日尚未結束會被排除）


def _pool_alias(idx: int) -> str:
    return f"p{idx}"


def _day_data_alias(idx: int) -> str:
    return f"d{idx}"


def build_batch_query(pool_addresses: list[str]) -> str:
    """組出一個包含多顆池子的 alias 查詢，每顆池子各對應一組
    p{i}: pool(...) + d{i}: poolDayDatas(...)。"""
    parts = []
    for idx, addr in enumerate(pool_addresses):
        addr_l = addr.lower()
        parts.append(
            f'{_pool_alias(idx)}: pool(id: "{addr_l}") {{ id totalValueLockedUSD }}'
        )
        parts.append(
            f'{_day_data_alias(idx)}: poolDayDatas(where: {{ pool: "{addr_l}" }}, '
            f"orderBy: date, orderDirection: desc, first: {DAY_DATA_LOOKBACK}) "
            "{ date volumeUSD feesUSD }"
        )
    return "{ " + " ".join(parts) + " }"


def call_gateway(api_key: str, query: str, deployment_id: str, timeout: float = 20.0) -> dict:
    url = f"{GATEWAY_BASE}/{api_key}/subgraphs/id/{deployment_id}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers=REQUEST_HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_batch_response(body: dict, pool_addresses: list[str]) -> dict[str, dict]:
    """把 alias 化的批次回應拆回 {pool_address_lower: {tvl_usd, day_data}}。
    找不到的池子（官方回 pool: null）保留在輸出裡、tvl_usd=None、
    day_data=[]，交給呼叫端決定要不要標成錯誤，這裡不吞掉、不假裝有資料。"""
    data = body.get("data") or {}
    out = {}
    for idx, addr in enumerate(pool_addresses):
        addr_l = addr.lower()
        pool_obj = data.get(_pool_alias(idx))
        day_data = data.get(_day_data_alias(idx)) or []
        tvl_usd = None
        if pool_obj and pool_obj.get("totalValueLockedUSD") is not None:
            tvl_usd = float(pool_obj["totalValueLockedUSD"])
        out[addr_l] = {"tvl_usd": tvl_usd, "day_data": day_data}
    return out


def _redact(text: str, api_key: str) -> str:
    if not api_key:
        return text
    return text.replace(api_key, "***REDACTED***")


def fetch_pool_addresses_from_normalized(chain_id: int) -> list[str]:
    """讀既有 data/normalized_latest.json，取出指定 chain_id、狀態為
    live/fixture 且有 pool_address 的池子清單——重用 pool_info 那條線已經
    查到的池子，不重新做一次池子探索。"""
    path = DATA_DIR / "normalized_latest.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    addresses = []
    seen = set()
    for row in payload.get("rows", []):
        if row.get("chain_id") != chain_id:
            continue
        if row.get("status") not in ("live", "fixture"):
            continue
        addr = row.get("pool_address")
        if not addr:
            continue
        addr_l = addr.lower()
        if addr_l in seen:
            continue
        seen.add(addr_l)
        addresses.append(addr_l)
    return addresses


def main() -> int:
    api_key = require_graph_api_key()

    chain_id = 1
    deployment_id = GRAPH_V3_DEPLOYMENT_ID_BY_CHAIN.get(chain_id)
    if not deployment_id:
        print(
            f"ERROR: chain_id={chain_id} 沒有已確認的 Graph deployment ID，"
            f" 本腳本不會用猜的 ID 嘗試連線。",
            file=sys.stderr,
        )
        return 2

    pool_addresses = fetch_pool_addresses_from_normalized(chain_id)
    if not pool_addresses:
        print(
            "沒有找到任何 chain_id=1 的 live/fixture 池子（先跑過"
            " normalize.py 產生 data/normalized_latest.json 再執行本腳本）。",
            file=sys.stderr,
        )
        return 1

    now_ts = time.time()
    enrichment: dict[str, dict] = {}
    for start in range(0, len(pool_addresses), BATCH_SIZE):
        batch = pool_addresses[start : start + BATCH_SIZE]
        query = build_batch_query(batch)
        try:
            body = call_gateway(api_key, query, deployment_id)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            cf_ray = e.headers.get("CF-RAY") if e.headers else None
            cf_note = f"（CF-RAY={cf_ray}）" if cf_ray else ""
            print(_redact(f"ERROR: HTTP {e.code}{cf_note}：{raw[:300]}", api_key), file=sys.stderr)
            return 1
        except urllib.error.URLError as e:
            print(_redact(f"ERROR: 連線失敗：{e.reason}", api_key), file=sys.stderr)
            return 1

        if "errors" in body:
            print(
                _redact(f"ERROR: gateway 回錯誤：{json.dumps(body['errors'], ensure_ascii=False)[:300]}", api_key),
                file=sys.stderr,
            )
            return 1

        parsed = parse_batch_response(body, batch)
        for addr_l, raw_metrics in parsed.items():
            enrichment[addr_l] = compute_pool_day_metrics(
                tvl_usd=raw_metrics["tvl_usd"],
                day_data_desc=raw_metrics["day_data"],
                now_ts=now_ts,
            )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "chain_id": chain_id,
        "deployment_id": deployment_id,
        "source": f"{GATEWAY_BASE}/<key>/subgraphs/id/{deployment_id} (poolDayDatas + pool.totalValueLockedUSD)",
        "pools": enrichment,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "graph_pool_day_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"寫入 {out_path}：{len(enrichment)} 顆池子（chain_id={chain_id}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
