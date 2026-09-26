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
        # 白名單以外的 token：所有由官方 API 回傳、會在儀表板上顯示的數值欄位一律清空，
        # 不可讓「已排除數值顯示」淪為空話（USD/volume/APR 本來就是 None，這裡額外清掉
        # fee tier、tick spacing、current tick、raw liquidity 這幾個會顯示的原始數值）。
        for numeric_key in ("fee_tier_raw", "fee_tier_pct", "tick_spacing", "current_tick", "pool_liquidity_raw"):
            row[numeric_key] = None
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


def _canonical_pair(symbol_a: str | None, symbol_b: str | None) -> tuple:
    """token pair 的順序無關 key。官方 API 回應的 tokenAddressA/B 順序不保證
    跟請求送出的 base/quote 順序一致（例如請求 WETH/USDC，回應可能是
    USDC/WETH），若直接用未排序的 (a, b) 比對，會把已查到的池位誤判成
    尚未擷取（t_bf8f4d3e review：anne 實跑 230 筆全部已回應，仍有 61 筆
    卡在 pending）。用排序過的 tuple 讓兩邊順序無關。"""
    if symbol_a is None or symbol_b is None:
        return (symbol_a, symbol_b)
    return tuple(sorted((symbol_a, symbol_b)))


def _pending_key(item: dict) -> tuple:
    """統一的池位識別 key，供 build_pending_rows 的「尚未擷取」判斷、
    load_no_pool_rows 的「已查詢（無池／404／錯誤）」判斷、以及
    covered_keys 的「已有 live/fixture 資料」判斷共用同一套邏輯與同一套
    pair 順序正規化，避免同一組查詢因 token 順序不同或分屬不同函式各自
    組 key，而被重複計入 pending 與其他狀態。"""
    if item.get("query_type") == "poolReference":
        return (item.get("chain_id"), item.get("protocol"), "REF", item.get("pool_reference_identifier"), None)
    a, b = _canonical_pair(item.get("base_symbol"), item.get("quote_symbol"))
    return (
        item.get("chain_id"),
        item.get("protocol"),
        a,
        b,
        item.get("fee"),
    )


def _classify_no_pool_result(result: dict) -> tuple[str, str] | None:
    """回傳 (status, pending_note)；若這筆結果其實查到池位（HTTP 200 且 pools 非空），
    回 None（交給 load_live_rows 處理，這裡不重複計入）。"""
    status_code = result.get("http_status")
    if status_code == 200:
        response = result.get("response")
        pools = response.get("pools", []) if isinstance(response, dict) else []
        if pools:
            return None
        return "empty_response", "官方已回應 HTTP 200，此組合目前查無池位（pools: []）；已查詢過，非尚未擷取"
    if status_code == 404:
        return "not_found", f"HTTP {status_code}：{result.get('response')}"
    return "error", f"HTTP {status_code}：{result.get('response')}"


def load_no_pool_rows(config: dict) -> list[dict]:
    """處理『官方已經回應這筆查詢』但結果不是活躍池位的三種狀況，
    一律不算尚未擷取（pending），避免真實 full update 時把已查無結果的組合誤判為未查：
      - HTTP 200 但 response.pools 是空陣列 -> empty_response（已查，官方確認目前無此池）
      - HTTP 404                          -> not_found（官方確認無此池）
      - 其他非 200                        -> error（查詢本身失敗，非「無此池」）
    """
    latest_path = DATA_DIR / "latest_raw.json"
    if not latest_path.exists():
        return []
    with open(latest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = []
    for result in payload.get("results", []):
        classified = _classify_no_pool_result(result)
        if classified is None:
            continue
        status, pending_note = classified

        row = base_row(result["chain_id"], result["chain_name"])
        row.update({
            "status": status,
            "protocol": result.get("protocol"),
            "token_a_symbol": result.get("base_symbol"),
            "token_b_symbol": result.get("quote_symbol"),
            "pair_label": f"{result.get('base_symbol')}/{result.get('quote_symbol')}" if result.get("base_symbol") else None,
            "fee_tier_raw": result.get("fee"),
            "fee_tier_pct": fee_raw_to_pct(result.get("fee")),
            "pool_address": result.get("pool_reference_identifier"),
            "snapshot_time": result.get("fetched_at"),
            "source": ENDPOINT,
            "pending_note": pending_note,
            "id": (
                f"{result['chain_id']}-{result.get('protocol')}-{status}-"
                f"{result.get('base_symbol')}-{result.get('quote_symbol')}-{result.get('fee')}-"
                f"{result.get('pool_reference_identifier')}"
            ),
        })
        rows.append(row)
    return rows


def no_pool_excluded_keys(config: dict) -> set[tuple]:
    """跟 load_no_pool_rows 讀同一份 latest_raw.json，但回傳的是跟
    build_queries()／build_pending_rows 相同格式的原始查詢 key（chain_id,
    protocol, base_symbol, quote_symbol, fee，或 REF 變體），而不是 normalize
    過的 row（row 用的是 token_a_symbol/fee_tier_raw 等不同欄位名）。
    兩邊欄位名不同會讓 key 永遠比對不到，導致已查詢過的組合又被判成 pending
    ——這是 t_bf8f4d3e review 第 3 點的根因，修正時兩處都要用同一個 _pending_key。
    """
    latest_path = DATA_DIR / "latest_raw.json"
    if not latest_path.exists():
        return set()
    with open(latest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    keys = set()
    for result in payload.get("results", []):
        if _classify_no_pool_result(result) is None:
            continue
        keys.add(_pending_key(result))
    return keys


def _pool_identity(row: dict) -> tuple:
    """同一顆池位的身分識別 key，跟 `_pending_key()` 的「查詢組合」key 不同層次：
    這裡用來判斷 fixture row 與 live row 是不是「同一顆已經上鏈的池」，藉此避免
    fixture 被當成額外一筆重複列出（t_bf8f4d3e review 第三輪：anne 的 Ethereum
    WETH/USDC V3 0.30% fixture 池位地址 0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8，
    跟正式 API 回應的同一顆池同時出現在 rows 裡，造成 total_rows 虛增）。
    優先用 pool_address（同一鏈+協定+池位地址即同一池，比 token pair 更精確，
    同一 pair/fee 理論上也可能有多顆池，例如不同 tickSpacing 的 v4 池）；
    只有在缺 pool_address 時才退回用 chain/protocol/pair/fee 比對。"""
    addr = (row.get("pool_address") or "").lower()
    if addr:
        return ("addr", row.get("chain_id"), row.get("protocol"), addr)
    a, b = _canonical_pair(row.get("token_a_symbol"), row.get("token_b_symbol"))
    return ("pair", row.get("chain_id"), row.get("protocol"), a, b, row.get("fee_tier_raw"))


def dedupe_fixture_rows(fixture_rows: list[dict], live_rows: list[dict]) -> list[dict]:
    """fixture 只是「離線 fallback／測試資料」，不是額外池位；一旦同一顆池已經有
    live 資料，就不能再讓 fixture 重複列出同一池，否則會把已取得一次的資料計成
    兩筆（t_bf8f4d3e review 第三輪：Ethereum WETH/USDC V3 0.30% 的 fixture 跟正式
    API 回應同一顆池同時出現在公開頁面，撐大 total_rows）。反過來說：若真實資料
    完全缺失（例如本次執行環境沒有 UNISWAP_API_KEY），fixture 仍要保留，離線
    smoke 才不會退化。"""
    live_identities = {_pool_identity(r) for r in live_rows if r["status"] == "live"}
    return [r for r in fixture_rows if _pool_identity(r) not in live_identities]


def covered_keys(rows: list[dict]) -> set[tuple]:
    """回傳跟 _pending_key() 相同 shape、相同 pair 順序正規化的 key 集合，
    這樣才能跟 build_pending_rows() 用請求 base/quote 組出的 key 正確比對，
    不受 API 回應 tokenAddressA/B 順序影響（見 _canonical_pair 說明）。"""
    keys = set()
    for r in rows:
        if r["status"] in ("live", "fixture", "untrusted_token"):
            a, b = _canonical_pair(r["token_a_symbol"], r["token_b_symbol"])
            keys.add((r["chain_id"], r["protocol"], a, b, r["fee_tier_raw"]))
    return keys


def load_graph_enrichment() -> dict | None:
    """讀 scripts/fetch_pool_metrics.py 寫出的 data/graph_pool_day_data.json。
    檔案不存在 -> None（維持原本「資料源未提供」的 null+note 行為，向下相容，
    不強迫每次 normalize 都要先跑過 Graph fetch）。"""
    path = DATA_DIR / "graph_pool_day_data.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_graph_enrichment(rows: list[dict], enrichment_payload: dict | None) -> list[dict]:
    """把 The Graph v3 poolDayDatas 算出的 TVL／volume／fee APR／收入變化方向
    疊回既有 rows。只覆蓋：
      - chain_id 跟 enrichment_payload["chain_id"] 一致（目前只有 Ethereum
        mainnet 有已確認的 deployment ID，見 common.GRAPH_V3_DEPLOYMENT_ID_BY_CHAIN）
      - status 為 live/fixture 且有 pool_address 的列
    找不到對應池子資料，或整份 enrichment 檔案缺席，都維持原本
    「資料源未提供」的 null+note，不補假數字、不吞掉原因說明。
    """
    if not enrichment_payload:
        return rows
    target_chain_id = enrichment_payload.get("chain_id")
    pools = enrichment_payload.get("pools", {})
    source_desc = enrichment_payload.get("source", "The Graph v3 subgraph poolDayDatas")
    generated_at = enrichment_payload.get("generated_at")

    for row in rows:
        if row.get("chain_id") != target_chain_id:
            continue
        if row.get("status") not in ("live", "fixture"):
            continue
        addr = row.get("pool_address")
        if not addr:
            continue
        metrics = pools.get(addr.lower())
        if metrics is None:
            row["tvl_usd_note"] = (
                f"{TVL_NOTE}（額外說明：已嘗試查 The Graph，但此池子位址在"
                f" poolDayDatas 回應中查無對應資料，抓取時間 {generated_at}）"
            )
            continue

        row["tvl_usd"] = metrics.get("tvl_usd")
        row["tvl_usd_note"] = None if metrics.get("tvl_usd") is not None else (
            f"資料源已查詢但無回應（The Graph pool.totalValueLockedUSD 為空），抓取時間 {generated_at}"
        )
        row["volume_24h_usd"] = metrics.get("volume_24h_usd")
        row["volume_7d_usd"] = metrics.get("volume_7d_usd")
        row["fee_apr_24h_pct"] = metrics.get("fee_apr_24h_pct")
        row["fee_apr_7d_pct"] = metrics.get("fee_apr_7d_pct")
        row["income_change_direction"] = metrics.get("income_change_direction")

        metrics_note = metrics.get("note")
        volume_note = f"{source_desc}（抓取時間 {generated_at}）"
        fee_apr_note = metrics_note or volume_note
        income_note = metrics_note or volume_note
        row["volume_note"] = volume_note if metrics.get("volume_24h_usd") is not None else metrics_note or volume_note
        row["fee_apr_note"] = fee_apr_note
        row["income_change_note"] = income_note

    return rows


def build_pending_rows(config: dict, already_covered: set[tuple], already_excluded: set[tuple]) -> list[dict]:
    rows = []
    for q in build_queries(config):
        key = _pending_key(q)
        if q["query_type"] == "poolParameters":
            if key in already_covered or key in already_excluded:
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
            if key in already_covered or key in already_excluded:
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
    # fixture 只是離線 fallback／測試資料；同一顆池若已有正式 live 回應，
    # 不能讓 fixture 再重複列出一次（t_bf8f4d3e review 第三輪：Ethereum
    # WETH/USDC V3 0.30% 同時出現 live 與 fixture 兩列，虛增 total_rows）。
    fixture_rows = dedupe_fixture_rows(fixture_rows, live_rows)
    no_pool_rows = load_no_pool_rows(config)

    covered = covered_keys(fixture_rows + live_rows)
    # empty_response／not_found／error 都代表「官方已經回應這筆查詢」，一律不能再被
    # build_pending_rows 標成尚未擷取（見 t_bf8f4d3e review：HTTP 200 空 pools 曾被誤判為 pending）。
    # 用 no_pool_excluded_keys() 而非從 no_pool_rows 反推 key：normalize 過的 row
    # 欄位名（token_a_symbol/fee_tier_raw）跟查詢 key 用的欄位名（base_symbol/fee）不同，
    # 從 row 反推會永遠比對不到。
    excluded_keys = no_pool_excluded_keys(config)
    pending_rows = build_pending_rows(config, covered, excluded_keys)

    all_rows = live_rows + fixture_rows + no_pool_rows + pending_rows

    # 有 scripts/fetch_pool_metrics.py 產生的 Graph 資料就疊上去（目前只有
    # chain_id=1／Ethereum mainnet），沒有就維持原本的「資料源未提供」
    # null+note——不強迫每次 normalize 都要先有 GRAPH_API_KEY。
    graph_enrichment = load_graph_enrichment()
    all_rows = merge_graph_enrichment(all_rows, graph_enrichment)

    meta = {
        "total_rows": len(all_rows),
        "live_rows": sum(1 for r in all_rows if r["status"] == "live"),
        "fixture_rows": sum(1 for r in all_rows if r["status"] == "fixture"),
        "pending_rows": sum(1 for r in all_rows if r["status"] == "pending"),
        "empty_response_rows": sum(1 for r in all_rows if r["status"] == "empty_response"),
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
