#!/usr/bin/env python3
"""端到端唯讀查詢：錢包 0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd 在
Unichain / Ethereum / Arbitrum / Base / BNB Chain 的 Uniswap V3／V4 部位。

只做唯讀 RPC（eth_call／eth_chainId／eth_blockNumber）與唯讀 Alchemy Prices
API 查詢；不簽名、不 approve/permit/swap/send transaction。V3 用真實
eth_call 模擬 collect() 取得目前可領手續費；V4 因缺少已核對的 poolId(bytes32)
換算（PositionInfo 存的是截斷過的 25-byte poolId，跟 StateView 要的完整
bytes32 poolId 不相容，換算需要 keccak256，本環境未安裝任何 keccak
套件、也不會為了這件事新增依賴），現況只提供 token_id/pool_key/tick
range/liquidity，其餘欄位誠實標示「資料源不支援」，不猜測、不造數字。

輸出：data/wallet_live_latest.json（結構見 build_dashboard.py 使用方式）。
同時把每個 V3 部位寫進 wallet_snapshot_store 的每日快照表。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import common
import wallet_apr_calc
import wallet_price_client
import wallet_rpc_client as rpc
import wallet_snapshot_store as store
import wallet_v3_math as math3

WALLET_ADDRESS = "0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd"

V3_CHAINS = [1, 42161, 10, 8453, 56, 130]
V4_CHAINS = [1, 42161, 10, 8453, 56, 130]

OUTPUT_PATH = common.DATA_DIR / "wallet_live_latest.json"


def _redact(exc: Exception) -> str:
    # wallet_rpc_client / wallet_price_client 的例外訊息本身已經先遮蔽過 URL/key，
    # 這裡直接轉字串即可，不需要重複遮蔽。
    return str(exc)


def _eth_call_post(rpc_url: str, payload: dict) -> dict:
    return rpc._default_http_post(rpc_url, payload)


def _rpc_simple(rpc_url: str, method: str) -> dict:
    return _eth_call_post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": []})


def chain_smoke(chain_id: int) -> dict:
    """AC1：真實執行 eth_chainId，回報狀態但不洩漏 key。

    先試 Alchemy（唯讀 API key），若該帳號沒開通這條鏈的網路，退回官方／知名
    唯讀公開 RPC（見 wallet_rpc_client.PUBLIC_RPC_URL_BY_CHAIN 的核對來源），
    兩邊都失敗才誠實回報 ok=False。"""
    name = rpc.CHAIN_NAME_BY_ID.get(chain_id, f"chain-{chain_id}")
    try:
        rpc_url, rpc_source, attempts = rpc.resolve_rpc_url(chain_id)
    except rpc.WalletRpcError as exc:
        return {"chain_id": chain_id, "chain_name": name, "ok": False, "error": _redact(exc)}
    try:
        chain_id_resp = _rpc_simple(rpc_url, "eth_chainId")
        block_resp = _rpc_simple(rpc_url, "eth_blockNumber")
        if "error" in chain_id_resp:
            raise rpc.WalletRpcError(f"節點回報 eth_chainId 錯誤：{chain_id_resp['error']}")
        if "error" in block_resp:
            raise rpc.WalletRpcError(f"節點回報 eth_blockNumber 錯誤：{block_resp['error']}")
        if not isinstance(chain_id_resp.get("result"), str):
            raise rpc.WalletRpcError(f"eth_chainId 回應格式異常（非預期的 hex 字串）：{chain_id_resp!r}")
        if not isinstance(block_resp.get("result"), str):
            raise rpc.WalletRpcError(f"eth_blockNumber 回應格式異常（非預期的 hex 字串）：{block_resp!r}")
        reported_chain_id = int(chain_id_resp["result"], 16)
        block_number = int(block_resp["result"], 16)
        ok = reported_chain_id == chain_id
        return {
            "chain_id": chain_id,
            "chain_name": name,
            "ok": ok,
            "rpc_source": rpc_source,
            "reported_chain_id": reported_chain_id,
            "block_number": block_number,
            "error": None if ok else f"節點回報 chain_id={reported_chain_id}，與預期 {chain_id} 不符",
        }
    except Exception as exc:  # noqa: BLE001 — 唯讀 smoke，任何失敗都要誠實回報，不中止整批
        return {"chain_id": chain_id, "chain_name": name, "ok": False, "rpc_source": rpc_source, "error": _redact(exc)}


def resolve_token_meta(rpc_url: str, chain_id: int, whitelist: dict, address: str) -> dict:
    entry = common.lookup_token(whitelist, chain_id, address)
    if entry:
        return {"symbol": entry["symbol"], "decimals": entry["decimals"], "source": entry["source"]}
    try:
        decimals = rpc.get_token_decimals(rpc_url, address)
        return {
            "symbol": address[:6] + "…" + address[-4:],
            "decimals": decimals,
            "source": "on-chain decimals()（不在白名單，symbol 用縮寫位址代替，避免顯示未經核對的字串）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"symbol": address[:6] + "…" + address[-4:], "decimals": None, "error": _redact(exc)}


def fetch_v3_chain(chain_id: int, whitelist: dict) -> dict:
    name = rpc.CHAIN_NAME_BY_ID.get(chain_id, f"chain-{chain_id}")
    result = {
        "chain_id": chain_id,
        "chain_name": name,
        "protocol": "v3",
        "queried_at": int(time.time()),
        "positions": [],
        "error": None,
    }
    try:
        rpc_url, rpc_source, _attempts = rpc.resolve_rpc_url(chain_id)
        result["rpc_source"] = rpc_source
    except rpc.WalletRpcError as exc:
        result["error"] = _redact(exc)
        return result

    try:
        block_resp = _rpc_simple(rpc_url, "eth_blockNumber")
        result["block_number"] = int(block_resp["result"], 16)
        token_ids = rpc.list_wallet_token_ids(rpc_url, WALLET_ADDRESS, chain_id=chain_id)
    except Exception as exc:  # noqa: BLE001
        result["error"] = _redact(exc)
        return result

    result["position_count"] = len(token_ids)
    for token_id in token_ids:
        pos = {"token_id": token_id}
        try:
            details = rpc.get_position(rpc_url, token_id, chain_id=chain_id)
            pos.update({
                "fee_tier_raw": details["fee"],
                "fee_tier_pct": common.fee_raw_to_pct(details["fee"]),
                "tick_lower": details["tick_lower"],
                "tick_upper": details["tick_upper"],
                "liquidity_raw": str(details["liquidity"]),
                "tokens_owed_0_raw": str(details["tokens_owed_0"]),
                "tokens_owed_1_raw": str(details["tokens_owed_1"]),
            })
            token0_meta = resolve_token_meta(rpc_url, chain_id, whitelist, details["token0"])
            token1_meta = resolve_token_meta(rpc_url, chain_id, whitelist, details["token1"])
            pos["token0"] = {"address": details["token0"], **token0_meta}
            pos["token1"] = {"address": details["token1"], **token1_meta}

            pool_addr = rpc.get_pool_address(rpc_url, details["token0"], details["token1"], details["fee"], chain_id=chain_id)
            pos["pool_address"] = pool_addr
            if pool_addr is None:
                pos["note"] = "Factory.getPool 回傳零位址：官方確實查無此池（不是尚未擷取）"
                result["positions"].append(pos)
                continue

            slot0 = rpc.get_slot0(rpc_url, pool_addr)
            pos["current_tick"] = slot0["tick"]
            pos["in_range"] = math3.in_range(slot0["tick"], details["tick_lower"], details["tick_upper"])
            liquidity = int(details["liquidity"])
            amt0_raw, amt1_raw = math3.amounts_for_liquidity(
                liquidity, slot0["tick"], slot0["sqrt_price_x96"], details["tick_lower"], details["tick_upper"]
            )
            dec0, dec1 = token0_meta.get("decimals"), token1_meta.get("decimals")
            pos["token0_amount"] = (amt0_raw / (10 ** dec0)) if dec0 is not None else None
            pos["token1_amount"] = (amt1_raw / (10 ** dec1)) if dec1 is not None else None

            try:
                collected = rpc.simulate_collect(rpc_url, token_id, WALLET_ADDRESS, chain_id=chain_id)
                pos["fees_owed_0_raw"] = str(collected["amount0"])
                pos["fees_owed_1_raw"] = str(collected["amount1"])
                pos["fees_source"] = "eth_call 模擬 collect()（唯讀，未廣播交易）"
                fee0 = (collected["amount0"] / (10 ** dec0)) if dec0 is not None else None
                fee1 = (collected["amount1"] / (10 ** dec1)) if dec1 is not None else None
            except Exception as exc:  # noqa: BLE001
                pos["fees_source"] = f"eth_call collect() 模擬失敗：{_redact(exc)}；退回顯示 positions().tokensOwed（可能非最新）"
                fee0 = (details["tokens_owed_0"] / (10 ** dec0)) if dec0 is not None else None
                fee1 = (details["tokens_owed_1"] / (10 ** dec1)) if dec1 is not None else None

            pos["_fee0_amount"] = fee0
            pos["_fee1_amount"] = fee1
            pos["_token0_addr_for_price"] = details["token0"].lower()
            pos["_token1_addr_for_price"] = details["token1"].lower()
        except Exception as exc:  # noqa: BLE001
            pos["error"] = _redact(exc)
        result["positions"].append(pos)
    return result


def fetch_v4_chain(chain_id: int) -> dict:
    name = rpc.CHAIN_NAME_BY_ID.get(chain_id, f"chain-{chain_id}")
    result = {
        "chain_id": chain_id,
        "chain_name": name,
        "protocol": "v4",
        "queried_at": int(time.time()),
        "positions": [],
        "error": None,
    }
    try:
        rpc_url, rpc_source, _attempts = rpc.resolve_rpc_url(chain_id)
        result["rpc_source"] = rpc_source
    except rpc.WalletRpcError as exc:
        result["error"] = _redact(exc)
        return result
    try:
        block_resp = _rpc_simple(rpc_url, "eth_blockNumber")
        result["block_number"] = int(block_resp["result"], 16)
        token_ids = rpc.list_wallet_v4_token_ids(rpc_url, WALLET_ADDRESS, chain_id=chain_id)
    except rpc.V4EnumerationUnsupported as exc:
        # tokenOfOwnerByIndex 不可用是協定本身限制，改用 eth_getLogs 掃
        # Transfer(to=wallet) 事件反查 token_id、再用 ownerOf() 驗證現況——
        # 這是「查得到明細」而不是「balanceOf() 統計數字」，兩者在
        # AC/報告裡要分開講清楚（見 @anne 2026-09-27 覆核意見）。
        try:
            token_ids = rpc.list_wallet_v4_token_ids_via_logs(rpc_url, WALLET_ADDRESS, chain_id)
            result["enumeration_source"] = (
                "eth_getLogs 掃描 Transfer(to=wallet) 事件＋ownerOf() 驗證現況"
                "（v4 PositionManager 本身不支援 tokenOfOwnerByIndex，此為替代方案）"
            )
        except Exception as log_exc:  # noqa: BLE001 — 掃描本身也可能因供應商限制失敗
            # balanceOf() 這個真實數字還是保留下來，不要因為列不出明細就整條鏈消失。
            result["position_count"] = exc.balance_count
            result["error"] = (
                f"{_redact(exc)}｜eth_getLogs 替代方案也失敗：{_redact(log_exc)}"
            )
            return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = _redact(exc)
        return result

    result["position_count"] = len(token_ids)
    for token_id in token_ids:
        try:
            pos = rpc.get_v4_position(rpc_url, token_id, chain_id=chain_id)
            pos["fee_tier_pct"] = common.fee_raw_to_pct(pos.get("fee"))
            pos["current_tick"] = None
            pos["in_range"] = None
            pos["position_value_usd"] = None
            pos["fees_owed_usd"] = None
            pos["value_unsupported_reason"] = (
                "v4 poolId(bytes32) 需 keccak256(poolKey) 才能查 StateView，"
                "本環境未安裝 keccak 套件（未經使用者同意不新增依賴），"
                "故 current_tick／in_range／USD 估值／fee 一律標示不支援，不猜測。"
            )
        except Exception as exc:  # noqa: BLE001
            pos = {"token_id": token_id, "error": _redact(exc)}
        result["positions"].append(pos)
    return result


def enrich_with_usd(v3_results: list[dict]) -> None:
    """一次性批次查所有需要的 (network_slug, address)，避免每個部位各打一次
    Alchemy Prices API。"""
    pairs: set[tuple[str, str]] = set()
    for chain_result in v3_results:
        slug = rpc.ALCHEMY_NETWORK_SLUG_BY_CHAIN.get(chain_result["chain_id"])
        if not slug:
            continue
        for pos in chain_result.get("positions", []):
            if "_token0_addr_for_price" in pos:
                pairs.add((slug, pos["_token0_addr_for_price"]))
            if "_token1_addr_for_price" in pos:
                pairs.add((slug, pos["_token1_addr_for_price"]))

    if not pairs:
        return
    try:
        prices = wallet_price_client.fetch_usd_prices_by_address(sorted(pairs))
    except wallet_price_client.PriceClientError as exc:
        for chain_result in v3_results:
            for pos in chain_result.get("positions", []):
                pos["usd_pricing_error"] = _redact(exc)
        return

    for chain_result in v3_results:
        slug = rpc.ALCHEMY_NETWORK_SLUG_BY_CHAIN.get(chain_result["chain_id"])
        for pos in chain_result.get("positions", []):
            addr0 = pos.get("_token0_addr_for_price")
            addr1 = pos.get("_token1_addr_for_price")
            price0 = prices.get((slug, addr0)) if addr0 else None
            price1 = prices.get((slug, addr1)) if addr1 else None
            pos["token0_usd_price"] = price0
            pos["token1_usd_price"] = price1
            value_usd = None
            if price0 is not None and pos.get("token0_amount") is not None:
                value_usd = (value_usd or 0.0) + price0 * pos["token0_amount"]
            if price1 is not None and pos.get("token1_amount") is not None:
                value_usd = (value_usd or 0.0) + price1 * pos["token1_amount"]
            pos["position_value_usd"] = value_usd
            fees_usd = None
            fee0 = pos.pop("_fee0_amount", None)
            fee1 = pos.pop("_fee1_amount", None)
            if price0 is not None and fee0 is not None:
                fees_usd = (fees_usd or 0.0) + price0 * fee0
            if price1 is not None and fee1 is not None:
                fees_usd = (fees_usd or 0.0) + price1 * fee1
            pos["fees_owed_usd"] = fees_usd
            pos.pop("_token0_addr_for_price", None)
            pos.pop("_token1_addr_for_price", None)
            if value_usd is None and (price0 is None or price1 is None):
                pos.setdefault("note", "部分/全部 token 無法從 Alchemy Prices API 取得 USD 報價，估值欄位為 None（非 0）")


def write_snapshots(v3_results: list[dict]) -> int:
    """把這次查到的每個 v3 部位寫進本機每日快照 SQLite；回傳寫入筆數。
    快照本身是不是「第一筆」由 wallet_snapshot_store/wallet_apr_calc 依歷史
    筆數判斷，本函式不做任何造數字的事。"""
    conn = store.get_connection()
    ts = int(time.time())
    count = 0
    for chain_result in v3_results:
        chain_id = chain_result["chain_id"]
        for pos in chain_result.get("positions", []):
            if "error" in pos or "token_id" not in pos or "pool_address" not in pos or pos.get("pool_address") is None:
                continue
            store.insert_snapshot(
                conn,
                ts=ts,
                wallet_addr=WALLET_ADDRESS,
                chain_id=chain_id,
                token_id=pos["token_id"],
                pool_addr=pos["pool_address"],
                tick_lower=pos["tick_lower"],
                tick_upper=pos["tick_upper"],
                liquidity=pos["liquidity_raw"],
                in_range=bool(pos.get("in_range")),
                fees_accrued_usd=pos.get("fees_owed_usd"),
                position_value_usd=pos.get("position_value_usd"),
            )
            count += 1
    conn.close()
    return count


def _sum_value_fees(row: dict) -> float | None:
    v, f = row.get("position_value_usd"), row.get("fees_accrued_usd")
    if v is None and f is None:
        return None
    return (v or 0.0) + (f or 0.0)


def attach_observed_apr(v3_results: list[dict]) -> None:
    """依本機快照歷史算「快照實測個人 APR」，跟池子級估算 APR 分欄。"""
    conn = store.get_connection()
    for chain_result in v3_results:
        chain_id = chain_result["chain_id"]
        for pos in chain_result.get("positions", []):
            if "token_id" not in pos or pos.get("pool_address") is None:
                continue
            history = store.fetch_position_history(
                conn, wallet_addr=WALLET_ADDRESS, chain_id=chain_id, token_id=pos["token_id"]
            )
            metrics = wallet_apr_calc.compute_wallet_position_metrics(history)
            pos["observed_apr_7d_pct"] = metrics["fee_apr_7d_pct"]
            pos["observed_apr_30d_pct"] = metrics["fee_apr_30d_pct"]
            pos["observed_apr_days_tracked"] = metrics["days_tracked"]
            pos["observed_apr_note"] = metrics["note"]

            # 24h delta：需要「今天」＋「昨天」兩筆快照才能算，第一筆快照一律
            # 誠實顯示「基準已建立」，不得用 0 或任何猜測值頂替（anne 硬性要求）。
            if len(history) >= 2:
                today_row, prev_row = history[-1], history[-2]
                today_total = _sum_value_fees(today_row)
                prev_total = _sum_value_fees(prev_row)
                pos["delta_24h_usd"] = (
                    (today_total - prev_total) if (today_total is not None and prev_total is not None) else None
                )
                pos["base_established"] = False
            else:
                pos["delta_24h_usd"] = None
                pos["base_established"] = True
                pos["delta_note"] = "基準已建立；第二筆快照後才有實測每日 delta"
    conn.close()


def build_wallet_rows(wallet_data: dict) -> list[dict]:
    """把 wallet_live_latest.json 的巢狀結構壓平成 build_dashboard.py 要的
    一維列表——純函式，不做 I/O，方便單元測試（餵合成 JSON 進來驗證）。"""
    rows: list[dict] = []
    for chain_result in wallet_data.get("v3", []) + wallet_data.get("v4", []):
        chain_name = chain_result.get("chain_name")
        protocol = chain_result.get("protocol")
        chain_error = chain_result.get("error")
        for pos in chain_result.get("positions", []):
            # v3 的 token0/token1 是 {symbol, decimals, source} dict（resolve_token_meta
            # 組出來的）；v4 的 get_v4_position() 目前只回傳 pool_key 裡的原始
            # currency 位址字串（未經白名單核對過的 symbol），兩種形狀都要處理，
            # 不能假設同一種結構。
            def _symbol_of(token_field):
                if isinstance(token_field, dict):
                    return token_field.get("symbol")
                if isinstance(token_field, str) and token_field:
                    return token_field[:6] + "…" + token_field[-4:]
                return None

            token0_symbol = _symbol_of(pos.get("token0"))
            token1_symbol = _symbol_of(pos.get("token1"))
            pair_label = None
            if token0_symbol and token1_symbol:
                pair_label = f"{token0_symbol}/{token1_symbol}"
            rows.append({
                "chain_name": chain_name,
                "protocol": protocol,
                "pair_label": pair_label,
                "fee_tier_pct": pos.get("fee_tier_pct"),
                "token_id": pos.get("token_id"),
                "position_value_usd": pos.get("position_value_usd"),
                "fees_owed_usd": pos.get("fees_owed_usd"),
                "in_range": pos.get("in_range"),
                "delta_24h_usd": pos.get("delta_24h_usd"),
                "observed_apr_7d_pct": pos.get("observed_apr_7d_pct"),
                "observed_apr_30d_pct": pos.get("observed_apr_30d_pct"),
                "base_established": pos.get("base_established"),
                "delta_note": pos.get("delta_note") or pos.get("observed_apr_note"),
                "snapshot_time": chain_result.get("queried_at"),
                "source": (
                    pos.get("fees_source")
                    or pos.get("value_unsupported_reason")
                    or pos.get("note")
                    or pos.get("error")
                    or chain_result.get("enumeration_source")
                ),
                "row_error": pos.get("error") or chain_error,
            })
        if not chain_result.get("positions") and chain_error:
            # 這條鏈整批查詢失敗（例如供應商 network not enabled）：仍要出現
            # 一列，讓使用者看得到「這條鏈查了、但失敗，原因是什麼」，不能默默消失。
            rows.append({
                "chain_name": chain_name,
                "protocol": protocol,
                "pair_label": None,
                "fee_tier_pct": None,
                "token_id": None,
                "position_value_usd": None,
                "fees_owed_usd": None,
                "in_range": None,
                "delta_24h_usd": None,
                "observed_apr_7d_pct": None,
                "observed_apr_30d_pct": None,
                "base_established": None,
                "delta_note": None,
                "snapshot_time": chain_result.get("queried_at"),
                "source": chain_error,
                "row_error": chain_error,
            })
    return rows


def main() -> int:
    smoke = [chain_smoke(cid) for cid in sorted(set(V3_CHAINS + V4_CHAINS))]
    graph_key_present = bool(__import__("os").environ.get("GRAPH_API_KEY", "").strip())

    whitelist = common.build_token_whitelist(common.load_config())

    v3_results = [fetch_v3_chain(cid, whitelist) for cid in V3_CHAINS]
    v4_results = [fetch_v4_chain(cid) for cid in V4_CHAINS]

    enrich_with_usd(v3_results)
    snapshot_count = 0
    try:
        snapshot_count = write_snapshots(v3_results)
        attach_observed_apr(v3_results)
    except store.WalletTrackerMigrationError as exc:
        for r in v3_results:
            r["snapshot_error"] = str(exc)

    output = {
        "wallet_address": WALLET_ADDRESS,
        "generated_at": int(time.time()),
        "rpc_smoke": smoke,
        "graph_api_key_present": graph_key_present,
        "v3": v3_results,
        "v4": v4_results,
        "snapshot_rows_written": snapshot_count,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    total_positions = sum(r.get("position_count", 0) for r in v3_results) + sum(
        r.get("position_count", 0) for r in v4_results
    )
    print(f"完成：v3+v4 共查到 {total_positions} 個部位，快照寫入 {snapshot_count} 筆，輸出 {OUTPUT_PATH}")
    any_chain_failed = any(not s["ok"] for s in smoke)
    if any_chain_failed:
        print("警告：至少一條鏈的 eth_chainId smoke 失敗，詳見輸出 JSON 的 rpc_smoke。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
