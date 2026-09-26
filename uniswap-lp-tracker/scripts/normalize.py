#!/usr/bin/env python3
"""將官方 pool_info 原始回應（真實快照 + 離線 fixture）正規化成儀表板可用的 rows。

不造數規則：TVL_USD／24h/7d volume／fee APR／歷史變化／錢包 position
在純 pool_info 資料下一律不可得，輸出固定為 null 並附中文原因（*_note）。
"""
from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

from common import DATA_DIR, ENDPOINT, FIXTURES_DIR, build_token_whitelist, fee_raw_to_pct, load_config
from fetch_pool_info import build_queries

TVL_NOTE = "資料源未提供（官方 pool_info 只回 poolLiquidity 原始流動性單位，非 USD TVL；需 Uniswap 官方 Subgraph 的 totalValueLockedUSD 補齊，本次未實作/未驗證 Subgraph endpoint）"
VOLUME_NOTE = "資料源未提供（pool_info 不含成交量；需 Subgraph poolDayDatas.volumeUSD，本次未實作）"
FEE_APR_NOTE = "資料源未提供（需 Subgraph feesUSD + TVL 才能算；公式為業界通用非官方，本次未取得輸入資料故不計算）"
INCOME_CHANGE_NOTE = "資料源未提供（需歷史時序，Liquidity API 不提供，需 Subgraph poolDayDatas 逐日資料）"
POSITION_NOTE = "本頁不顯示個別錢包 LP 部位或未領手續費；如需查詢特定錢包需另行使用 Subgraph positions 或 RPC view 函式（本頁未實作，Liquidity API 無唯讀 position 端點）"


def base_row(chain_id: int, chain_name: str) -> dict:
    return {
        "id": None,
        "status": "pending",
        "chain_id": chain_id,
        "chain_name": chain_name,
        "protocol": None,
        "pool_address": None,
        "token_a_symbol": None,
        "token_b_symbol": None,
        "pair_label": None,
        "token_address_a": None,
        "token_address_b": None,
        "fee_tier_raw": None,
        "fee_tier_pct": None,
        "tick_spacing": None,
        "current_tick": None,
        "pool_liquidity_raw": None,
        "tvl_usd": None,
        "tvl_usd_note": TVL_NOTE,
        "volume_24h_usd": None,
        "volume_7d_usd": None,
        "volume_note": VOLUME_NOTE,
        "fee_apr_24h_pct": None,
        "fee_apr_7d_pct": None,
        "fee_apr_note": FEE_APR_NOTE,
        "income_change_direction": None,
        "income_change_note": INCOME_CHANGE_NOTE,
        "wallet_position_note": POSITION_NOTE,
        "snapshot_time": None,
        "source": None,
        "pending_note": None,
        "limitations": [],
    }


def pool_obj_to_row(pool_obj: dict, source_label: str, snapshot_time: str, whitelist: dict, config: dict) -> dict:
    chain_id = pool_obj.get("chainId")
    chain_name = None
    for chain in config["chains"]:
        if chain["chain_id"] == chain_id:
            chain_name = chain["chain_name"]
    if chain_name is None:
        for ref in config.get("special_pool_references", []):
            if ref["chain_id"] == chain_id:
                chain_name = ref["chain_name"]
    chain_name = chain_name or f"chain-{chain_id}"

    row = base_row(chain_id, chain_name)
    tok_a = whitelist.get(chain_id, {}).get((pool_obj.get("tokenAddressA") or "").lower())
    tok_b = whitelist.get(chain_id, {}).get((pool_obj.get("tokenAddressB") or "").lower())
    untrusted = []
    if pool_obj.get("tokenAddressA") and not tok_a:
        untrusted.append(pool_obj["tokenAddressA"])
    if pool_obj.get("tokenAddressB") and not tok_b:
        untrusted.append(pool_obj["tokenAddressB"])

    fee_raw = pool_obj.get("fee")
    sym_a = (tok_a or {}).get("symbol") or "UNKNOWN"
    sym_b = (tok_b or {}).get("symbol") or "UNKNOWN"
    row.update({
        "status": "untrusted_token" if untrusted else "live",
        "protocol": pool_obj.get("poolProtocol"),
        "pool_address": pool_obj.get("poolReferenceIdentifier"),
        "token_a_symbol": sym_a,
        "token_b_symbol": sym_b,
        "pair_label": f"{sym_a}/{sym_b}",
        "token_address_a": pool_obj.get("tokenAddressA"),
        "token_address_b": pool_obj.get("tokenAddressB"),
        "fee_tier_raw": fee_raw,
        "fee_tier_pct": fee_raw_to_pct(fee_raw),
        "tick_spacing": pool_obj.get("tickSpacing"),
        "current_tick": pool_obj.get("currentTick"),
        "pool_liquidity_raw": pool_obj.get("poolLiquidity"),
        "snapshot_time": snapshot_time,
        "source": source_label,
        "id": f"{chain_id}-{pool_obj.get('poolProtocol')}-{pool_obj.get('poolReferenceIdentifier') or (pool_obj.get('tokenAddressA'), pool_obj.get('tokenAddressB'), fee_raw)}",
    })
    if untrusted:
        row["limitations"].append(
            f"含不在白名單內的 token 位址（{', '.join(untrusted)}），已排除數值顯示，需人工複核"
        )
        row["tvl_usd_note"] = "已停用：token 位址不在白名單"
    return row


def load_fixture_rows(whitelist: dict, config: dict) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(str(FIXTURES_DIR / "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        pools = payload.get("pools", [])
        for pool_obj in pools:
            row = pool_obj_to_row(
                pool_obj,
                source_label=f"offline fixture: {Path(path).name} (anne 實測、無 key，已知 schema fixture)",
                snapshot_time=None,
                whitelist=whitelist,
                config=config,
            )
            row["status"] = "fixture" if row["status"] == "live" else row["status"]
            rows.append(row)
    return rows


def load_live_rows(whitelist: dict, config: dict) -> list[dict]:
    latest_path = DATA_DIR / "latest_raw.json"
    if not latest_path.exists():
        return []
    with open(latest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = []
    for result in payload.get("results", []):
        if result.get("http_status") != 200:
            continue
        response = result.get("response")
        if not isinstance(response, dict):
            continue
        for pool_obj in response.get("pools", []):
            row = pool_obj_to_row(
                pool_obj,
                source_label=f"{ENDPOINT} (live, fetched_at={result.get('fetched_at')})",
                snapshot_time=result.get("fetched_at"),
                whitelist=whitelist,
                config=config,
            )
            rows.append(row)
    return rows


def load_not_found_rows(config: dict) -> list[dict]:
    latest_path = DATA_DIR / "latest_raw.json"
    if not latest_path.exists():
        return []
    with open(latest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = []
    for result in payload.get("results", []):
        status = result.get("http_status")
        if status == 200:
            continue
        row = base_row(result["chain_id"], result["chain_name"])
        row.update({
            "status": "not_found" if status == 404 else "error",
            "protocol": result.get("protocol"),
            "token_a_symbol": result.get("base_symbol"),
            "token_b_symbol": result.get("quote_symbol"),
            "pair_label": f"{result.get('base_symbol')}/{result.get('quote_symbol')}" if result.get("base_symbol") else None,
            "fee_tier_raw": result.get("fee"),
            "fee_tier_pct": fee_raw_to_pct(result.get("fee")),
            "pool_address": result.get("pool_reference_identifier"),
            "snapshot_time": result.get("fetched_at"),
            "source": ENDPOINT,
            "pending_note": f"HTTP {status}：{result.get('response')}",
            "id": f"{result['chain_id']}-{result.get('protocol')}-notfound-{result.get('base_symbol')}-{result.get('quote_symbol')}-{result.get('fee')}",
        })
        rows.append(row)
    return rows


def covered_keys(rows: list[dict]) -> set[tuple]:
    keys = set()
    for r in rows:
        if r["status"] in ("live", "fixture", "untrusted_token"):
            keys.add((r["chain_id"], r["protocol"], r["token_a_symbol"], r["token_b_symbol"], r["fee_tier_raw"]))
    return keys


def build_pending_rows(config: dict, already_covered: set[tuple], already_not_found: set[tuple]) -> list[dict]:
    rows = []
    for q in build_queries(config):
        if q["query_type"] == "poolParameters":
            key = (q["chain_id"], q["protocol"], q["base_symbol"], q["quote_symbol"], q["fee"])
            if key in already_covered or key in already_not_found:
                continue
            row = base_row(q["chain_id"], q["chain_name"])
            row.update({
                "protocol": q["protocol"],
                "token_a_symbol": q["base_symbol"],
                "token_b_symbol": q["quote_symbol"],
                "pair_label": f"{q['base_symbol']}/{q['quote_symbol']}",
                "fee_tier_raw": q["fee"],
                "fee_tier_pct": fee_raw_to_pct(q["fee"]),
                "pending_note": "尚未擷取（需 @anne 帶 UNISWAP_API_KEY 執行 scripts/fetch_pool_info.py）",
                "id": f"{q['chain_id']}-{q['protocol']}-pending-{q['base_symbol']}-{q['quote_symbol']}-{q['fee']}",
            })
            rows.append(row)
        else:
            key = (q["chain_id"], q["protocol"], "REF", q["pool_reference_identifier"], None)
            if key in already_covered:
                continue
            row = base_row(q["chain_id"], q["chain_name"])
            row.update({
                "protocol": q["protocol"],
                "pool_address": q["pool_reference_identifier"],
                "pending_note": "尚未擷取（需 @anne 帶 UNISWAP_API_KEY 執行 scripts/fetch_pool_info.py；protocol 版本未知，將依序嘗試）",
                "id": f"{q['chain_id']}-{q['protocol']}-pending-ref-{q['pool_reference_identifier']}",
            })
            rows.append(row)
    return rows


def main() -> int:
    config = load_config()
    whitelist = build_token_whitelist(config)

    fixture_rows = load_fixture_rows(whitelist, config)
    live_rows = load_live_rows(whitelist, config)
    not_found_rows = load_not_found_rows(config)

    covered = covered_keys(fixture_rows + live_rows)
    not_found_keys = {
        (r["chain_id"], r["protocol"], r["token_a_symbol"], r["token_b_symbol"], r["fee_tier_raw"])
        for r in not_found_rows
        if r["status"] == "not_found"
    }
    pending_rows = build_pending_rows(config, covered, not_found_keys)

    all_rows = live_rows + fixture_rows + not_found_rows + pending_rows

    meta = {
        "total_rows": len(all_rows),
        "live_rows": sum(1 for r in all_rows if r["status"] == "live"),
        "fixture_rows": sum(1 for r in all_rows if r["status"] == "fixture"),
        "pending_rows": sum(1 for r in all_rows if r["status"] == "pending"),
        "not_found_rows": sum(1 for r in all_rows if r["status"] == "not_found"),
        "error_rows": sum(1 for r in all_rows if r["status"] == "error"),
        "untrusted_rows": sum(1 for r in all_rows if r["status"] == "untrusted_token"),
    }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": ENDPOINT,
        "meta": meta,
        "rows": all_rows,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "normalized_latest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"寫入 {out_path}：{meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
