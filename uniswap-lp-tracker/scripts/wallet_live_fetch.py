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
from datetime import datetime, timezone
from pathlib import Path

import common
import wallet_apr_calc
import wallet_price_client
import wallet_rpc_client as rpc
import wallet_snapshot_store as store
import wallet_v3_math as math3

try:
    from zoneinfo import ZoneInfo
    _TAIPEI_TZ = ZoneInfo("Asia/Taipei")
except Exception:  # pragma: no cover - stdlib zoneinfo 應永遠存在
    _TAIPEI_TZ = timezone.utc

WALLET_ADDRESS = "0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd"

# 每日總值口徑版本；口徑一變（例如改成含歷史部位）就要換版本號，
# 避免新舊口徑的每日快照被誤拿來互相比較 delta。
PORTFOLIO_SCOPE_KEY = "active-lp-usd-v1"

V3_CHAINS = [1, 42161, 10, 8453, 56, 130]
V4_CHAINS = [1, 42161, 10, 8453, 56, 130]

OUTPUT_PATH = common.DATA_DIR / "wallet_live_latest.json"
V4_NATIVE_PRICE_ADDRESS_BY_CHAIN = {
    130: "0x4200000000000000000000000000000000000006",
}


def _redact(exc: Exception) -> str:
    # wallet_rpc_client / wallet_price_client 的例外訊息本身已經先遮蔽過 URL/key，
    # 這裡直接轉字串即可，不需要重複遮蔽。
    return str(exc)


def _eth_call_post(rpc_url: str, payload: dict) -> dict:
    return rpc._default_http_post(rpc_url, payload)


def _rpc_simple(rpc_url: str, method: str) -> dict:
    return _eth_call_post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": []})


def _rpc_quantity(value) -> int:
    """Decode an RPC quantity while tolerating providers returning JSON integers."""
    if isinstance(value, bool):
        raise ValueError("RPC quantity 回應為布林值")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    raise ValueError(f"RPC quantity 回應型別不支援：{type(value).__name__}")


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


def fetch_v3_chain(chain_id: int, whitelist: dict, smoke_block_number: int | None = None) -> dict:
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
        if "error" in block_resp:
            raise rpc.WalletRpcError(f"節點回報 eth_blockNumber 錯誤：{block_resp['error']}")
        result["block_number"] = _rpc_quantity(block_resp.get("result"))
    except Exception as exc:  # noqa: BLE001
        if smoke_block_number is None:
            result["error"] = _redact(exc)
            return result
        result["block_number"] = smoke_block_number
        result["block_number_source"] = "同次 eth_chainId smoke 成功回應（eth_blockNumber 本次異常）"
    try:
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
                # anne 2026-10-03 核正：decreaseLiquidity() 燒掉的本金會先寫進
                # tokensOwed，collect() 模擬出來的金額是「本金＋fee」混在一起，
                # 在沒有另外拆 fee-growth 之前絕不可標成「可領 fee」——這是
                # ①目前可提領總額，要套精確恆等式扣掉累計本金才能還原純 fee。
                pos["fees_source"] = (
                    "eth_call 模擬 collect()（唯讀，未廣播交易）；此為①目前可提領總額"
                    "（含尚未拆分的本金與 fee，不可視為純可領 fee，見 cumulative_fee_income_usd）"
                )
                fee0 = (collected["amount0"] / (10 ** dec0)) if dec0 is not None else None
                fee1 = (collected["amount1"] / (10 ** dec1)) if dec1 is not None else None
            except Exception as exc:  # noqa: BLE001
                pos["fees_source"] = f"eth_call collect() 模擬失敗：{_redact(exc)}；退回顯示 positions().tokensOwed（可能非最新）"
                fee0 = (details["tokens_owed_0"] / (10 ** dec0)) if dec0 is not None else None
                fee1 = (details["tokens_owed_1"] / (10 ** dec1)) if dec1 is not None else None

            # ②累計已領：掃描鏈上 Collect 事件，這是「這個 tokenId 有生以來
            # 已經真的領出過」的加總（同樣含本金＋fee，單獨看也分不出來）。
            try:
                cumulative = rpc.get_cumulative_collected_fees(rpc_url, token_id, chain_id=chain_id)
                pos["cumulative_claimed_0_raw"] = str(cumulative["amount0_raw"])
                pos["cumulative_claimed_1_raw"] = str(cumulative["amount1_raw"])
                pos["cumulative_claimed_log_count"] = cumulative["log_count"]
                pos["cumulative_claimed_source"] = "eth_getLogs 掃描 Collect(tokenId indexed) 事件加總"
                claimed0 = (cumulative["amount0_raw"] / (10 ** dec0)) if dec0 is not None else None
                claimed1 = (cumulative["amount1_raw"] / (10 ** dec1)) if dec1 is not None else None
            except Exception as exc:  # noqa: BLE001 — 掃描失敗不當掉整個部位，標示②不可用
                pos["cumulative_claimed_unsupported_reason"] = f"Collect 事件掃描失敗：{_redact(exc)}"
                claimed0 = claimed1 = None

            # anne 2026-10-03 核正新增：累計 decreaseLiquidity() 撤出的本金，
            # 用來從①②裡扣掉本金、還原出精確恆等式算出的純 fee 收入。
            try:
                decreased = rpc.get_cumulative_decrease_liquidity_principal(rpc_url, token_id, chain_id=chain_id)
                pos["cumulative_decrease_principal_0_raw"] = str(decreased["amount0_raw"])
                pos["cumulative_decrease_principal_1_raw"] = str(decreased["amount1_raw"])
                pos["cumulative_decrease_principal_log_count"] = decreased["log_count"]
                pos["cumulative_decrease_principal_source"] = (
                    "eth_getLogs 掃描 DecreaseLiquidity(tokenId indexed) 事件加總（撤資本金）"
                )
                decreased0 = (decreased["amount0_raw"] / (10 ** dec0)) if dec0 is not None else None
                decreased1 = (decreased["amount1_raw"] / (10 ** dec1)) if dec1 is not None else None
            except Exception as exc:  # noqa: BLE001 — 掃描失敗不當掉整個部位，標示該欄不可用
                pos["cumulative_decrease_principal_unsupported_reason"] = f"DecreaseLiquidity 事件掃描失敗：{_redact(exc)}"
                decreased0 = decreased1 = None

            pos["_fee0_amount"] = fee0
            pos["_fee1_amount"] = fee1
            pos["_claimed0_amount"] = claimed0
            pos["_claimed1_amount"] = claimed1
            pos["_decreased0_amount"] = decreased0
            pos["_decreased1_amount"] = decreased1
            pos["_token0_addr_for_price"] = details["token0"].lower()
            pos["_token1_addr_for_price"] = details["token1"].lower()
        except Exception as exc:  # noqa: BLE001
            pos["error"] = _redact(exc)
        result["positions"].append(pos)
    return result


def _evaluate_v4_delta_quality(
    *,
    rpc_url: str,
    chain_id: int,
    token_id: int,
    pool_id_hex: str,
    current_raw0: int,
    current_raw1: int,
    current_liquidity: int,
    current_block: int | None,
) -> dict:
    """串接 store 的歷史快照＋rpc 的活動存在性查詢，再丟給純函式
    wallet_apr_calc.compute_v4_fee_delta_quality() 做決策。任何查詢失敗都要
    fail-closed 轉成 activity_unknown／insufficient_snapshot，絕不吞掉改
    回「沒有活動」（那會讓真正有活動但查不到的情況被誤判成精確值）。"""
    conn = store.get_connection()
    try:
        history = store.fetch_position_history(
            conn, wallet_addr=WALLET_ADDRESS, chain_id=chain_id, token_id=token_id
        )
    finally:
        conn.close()
    # 這次尚未寫入資料庫（write_snapshots 還沒跑），history 最後一筆就是
    # 「上一次」快照，不是本次。
    previous_row = history[-1] if history else None

    if previous_row is None or previous_row.get("snapshot_block_number") is None or current_block is None:
        return wallet_apr_calc.compute_v4_fee_delta_quality(
            previous_row=previous_row,
            current_raw0=current_raw0,
            current_raw1=current_raw1,
            current_liquidity=current_liquidity,
            has_activity=None,
            activity_error=None,
        )

    from_block = previous_row["snapshot_block_number"] + 1
    to_block = current_block
    has_activity: bool | None = False
    activity_error: str | None = None
    if from_block <= to_block:
        try:
            has_activity = rpc.has_v4_position_activity_in_range(
                rpc_url,
                pool_id_hex,
                rpc.V4_POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id],
                token_id,
                from_block,
                to_block,
                chain_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed，往下轉成 activity_unknown
            activity_error = _redact(exc)
    # from_block > to_block（同一區塊內重跑）：區塊範圍本身是空的，沒有
    # 任何新區塊可能夾帶活動，has_activity 維持 False 是誠實推論，不是估算。
    return wallet_apr_calc.compute_v4_fee_delta_quality(
        previous_row=previous_row,
        current_raw0=current_raw0,
        current_raw1=current_raw1,
        current_liquidity=current_liquidity,
        has_activity=has_activity,
        activity_error=activity_error,
    )


def fetch_v4_chain(chain_id: int, smoke_block_number: int | None = None) -> dict:
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
        if "error" in block_resp:
            raise rpc.WalletRpcError(f"節點回報 eth_blockNumber 錯誤：{block_resp['error']}")
        result["block_number"] = _rpc_quantity(block_resp.get("result"))
    except Exception as exc:  # noqa: BLE001
        if smoke_block_number is None:
            result["error"] = _redact(exc)
            return result
        result["block_number"] = smoke_block_number
        result["block_number_source"] = "同次 eth_chainId smoke 成功回應（eth_blockNumber 本次異常）"
    try:
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
            pos["active"] = int(pos.get("liquidity", 0)) > 0
            if not pos["active"]:
                pos["position_status"] = "已退出／無流動性（liquidity=0）"
                pos["token0_amount"] = 0
                pos["token1_amount"] = 0
                pos["fees_unsupported_reason"] = "StateView 無此歷史部位的可領費用；liquidity=0，列為已退出／無流動性。"
                result["positions"].append(pos)
                continue
            pool_key = {
                "currency0": pos["token0"], "currency1": pos["token1"],
                "fee": pos["fee"], "tick_spacing": pos["tick_spacing"], "hooks": pos["hooks"],
            }
            pos["pool_id_hex"] = rpc.v4_pool_id(pool_key)
            state = rpc.get_v4_slot0(rpc_url, pos["pool_id_hex"], chain_id)
            pos["current_tick"] = state["tick"]
            pos["in_range"] = math3.in_range(state["tick"], pos["tick_lower"], pos["tick_upper"])
            pos["position_status"] = "活躍（非零 liquidity）"
            pos["lp_fee_raw"] = state["lp_fee"]
            pos["pool_state_source"] = "Uniswap v4 StateView.getSlot0(bytes32)；RPC eth_call"
            amt0_raw, amt1_raw = math3.amounts_for_liquidity(
                int(pos["liquidity"]), state["tick"], state["sqrt_price_x96"],
                pos["tick_lower"], pos["tick_upper"],
            )
            whitelist = common.build_token_whitelist(common.load_config())
            meta0 = ({"symbol": "ETH", "decimals": 18, "source": "Uniswap native currency (zero address)"}
                     if pos["token0"].lower() == "0x" + "0" * 40
                     else resolve_token_meta(rpc_url, chain_id, whitelist, pos["token0"]))
            meta1 = ({"symbol": "ETH", "decimals": 18, "source": "Uniswap native currency (zero address)"}
                     if pos["token1"].lower() == "0x" + "0" * 40
                     else resolve_token_meta(rpc_url, chain_id, whitelist, pos["token1"]))
            pos["token0"] = {"address": pool_key["currency0"], **meta0}
            pos["token1"] = {"address": pool_key["currency1"], **meta1}
            pos["token0_amount"] = amt0_raw / (10 ** meta0["decimals"]) if meta0.get("decimals") is not None else None
            pos["token1_amount"] = amt1_raw / (10 ** meta1["decimals"]) if meta1.get("decimals") is not None else None
            try:
                growth = rpc.get_v4_position_fee_growth(
                    rpc_url, pos["pool_id_hex"], token_id, pos["tick_lower"], pos["tick_upper"], chain_id
                )
                pos["position_liquidity_stateview"] = str(growth["liquidity"])
                delta0 = max(0, growth["fee_growth_inside_0_x128"] - growth["fee_growth_inside_last_0_x128"])
                delta1 = max(0, growth["fee_growth_inside_1_x128"] - growth["fee_growth_inside_last_1_x128"])
                pos["fees_owed_0_raw"] = str(delta0 * growth["liquidity"] // (2**128))
                pos["fees_owed_1_raw"] = str(delta1 * growth["liquidity"] // (2**128))
                pos["fees_source"] = "StateView current feeGrowthInside − position checkpoint，乘以鏈上 liquidity 再除 2^128；估算可領 token base units"
                pos.pop("fees_unsupported_reason", None)

                # t_4be89664 第 2 段：持久化目前未領 fee 的原始 feeGrowthInside
                # 全域值（不是上面的「扣掉自身 checkpoint」之後的可領估計），
                # 搭配快照區塊高度，才能讓下一次快照用「無活動期間的 diff」
                # 還原出跨快照週期的精確原幣 Δ（見 _evaluate_v4_delta_quality）。
                pos["v4_fee_growth_inside0_raw"] = str(growth["fee_growth_inside_0_x128"])
                pos["v4_fee_growth_inside1_raw"] = str(growth["fee_growth_inside_1_x128"])
                pos["v4_snapshot_block_number"] = result.get("block_number")
                quality = _evaluate_v4_delta_quality(
                    rpc_url=rpc_url,
                    chain_id=chain_id,
                    token_id=token_id,
                    pool_id_hex=pos["pool_id_hex"],
                    current_raw0=growth["fee_growth_inside_0_x128"],
                    current_raw1=growth["fee_growth_inside_1_x128"],
                    current_liquidity=growth["liquidity"],
                    current_block=result.get("block_number"),
                )
                pos["v4_delta_quality_status"] = quality["status"]
                pos["v4_delta_quality_reason"] = quality["reason"]
                pos["fee_income_delta_token0_raw"] = (
                    str(quality["delta0_raw"]) if quality["delta0_raw"] is not None else None
                )
                pos["fee_income_delta_token1_raw"] = (
                    str(quality["delta1_raw"]) if quality["delta1_raw"] is not None else None
                )
            except Exception as exc:  # noqa: BLE001 — 保留已查得的部位狀態與估值
                pos["fees_unsupported_reason"] = f"StateView fee-growth 讀取失敗，無法計算個人可領 fee：{_redact(exc)}"
                # fail-closed：連「目前未領 fee」都讀不到時，跨快照 Δ 更不可信，
                # 不得留空（留空會被誤讀成「尚未判斷」而不是「確定失敗」）。
                pos["v4_delta_quality_status"] = "growth_unavailable"
                pos["v4_delta_quality_reason"] = f"StateView fee-growth 讀取失敗，無法計算 Δ：{_redact(exc)}"
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
            for i, (price, amount) in enumerate(
                ((price0, pos.get("token0_amount")), (price1, pos.get("token1_amount")))
            ):
                pos[f"token{i}_value_usd"] = (
                    None if amount is None or (amount != 0 and price is None)
                    else 0.0 if amount == 0
                    else price * amount
                )
            value_usd = 0.0
            value_complete = True
            for price, amount in ((price0, pos.get("token0_amount")), (price1, pos.get("token1_amount"))):
                if amount is None:
                    value_complete = False
                elif amount != 0:
                    if price is None:
                        value_complete = False
                    else:
                        value_usd += price * amount
            if not value_complete:
                value_usd = None
            pos["position_value_usd"] = value_usd
            fees_usd = None
            fee0 = pos.pop("_fee0_amount", None)
            fee1 = pos.pop("_fee1_amount", None)
            fees_usd = 0.0
            fees_complete = True
            for price, amount in ((price0, fee0), (price1, fee1)):
                if amount is None:
                    fees_complete = False
                elif amount != 0:
                    if price is None:
                        fees_complete = False
                    else:
                        fees_usd += price * amount
            if not fees_complete:
                fees_usd = None
            pos["fees_owed_usd"] = fees_usd

            # ②累計已領換算 USD：刻意用「本次查詢同一時間點」的 price0/price1，
            # 跟①可領、部位估值用同一套即時報價——不去抓各次歷史 collect 當下
            # 的歷史幣價（那會讓「本期新增收入」的計算混進幣價漲跌，anne 明確
            # 要求分開：Δ 只能反映 fee 收入本身，不能被幣價波動污染）。
            claimed0 = pos.pop("_claimed0_amount", None)
            claimed1 = pos.pop("_claimed1_amount", None)
            if claimed0 is None and claimed1 is None:
                pos["cumulative_claimed_usd"] = None
            else:
                claimed_usd = 0.0
                claimed_complete = True
                for price, amount in ((price0, claimed0), (price1, claimed1)):
                    if amount is None:
                        claimed_complete = False
                    elif amount != 0:
                        if price is None:
                            claimed_complete = False
                        else:
                            claimed_usd += price * amount
                pos["cumulative_claimed_usd"] = claimed_usd if claimed_complete else None

            # anne 2026-10-03 核正新增：累計 decreaseLiquidity() 本金換算 USD，
            # 同樣用這次查詢的即時報價，跟①②③用同一套，不混用歷史幣價。
            decreased0 = pos.pop("_decreased0_amount", None)
            decreased1 = pos.pop("_decreased1_amount", None)
            if decreased0 is None and decreased1 is None:
                pos["cumulative_decrease_principal_usd"] = None
            else:
                decreased_usd = 0.0
                decreased_complete = True
                for price, amount in ((price0, decreased0), (price1, decreased1)):
                    if amount is None:
                        decreased_complete = False
                    elif amount != 0:
                        if price is None:
                            decreased_complete = False
                        else:
                            decreased_usd += price * amount
                pos["cumulative_decrease_principal_usd"] = decreased_usd if decreased_complete else None

            # anne 2026-10-03 核正的精確恆等式：累計 fee 收入 ＝ 累計已領 ＋ 目前
            # 可提領總額 － 累計撤出本金。任何一段缺資料就整段 None，不猜測。
            income_result = wallet_apr_calc.compute_cumulative_fee_income_usd(
                pos.get("cumulative_claimed_usd"),
                pos.get("fees_owed_usd"),
                pos.get("cumulative_decrease_principal_usd"),
            )
            pos["cumulative_fee_income_usd"] = income_result["value_usd"]
            if income_result["note"]:
                pos["cumulative_fee_income_note"] = income_result["note"]

            pos.pop("_token0_addr_for_price", None)
            pos.pop("_token1_addr_for_price", None)
            if value_usd is None and (price0 is None or price1 is None):
                pos.setdefault("note", "部分/全部 token 無法從 Alchemy Prices API 取得 USD 報價，估值欄位為 None（非 0）")


def enrich_v4_with_usd(v4_results: list[dict]) -> None:
    pairs: set[tuple[str, str]] = set()
    for chain_result in v4_results:
        slug = rpc.ALCHEMY_NETWORK_SLUG_BY_CHAIN.get(chain_result["chain_id"])
        if not slug:
            continue
        for pos in chain_result.get("positions", []):
            if not pos.get("active"):
                continue
            for i in (0, 1):
                token = pos.get(f"token{i}") or {}
                addr = token.get("address", "").lower()
                if addr == "0x" + "0" * 40:
                    addr = V4_NATIVE_PRICE_ADDRESS_BY_CHAIN.get(chain_result["chain_id"], "")
                if addr:
                    pos[f"_v4_price_addr_{i}"] = addr
                    pairs.add((slug, addr))
    try:
        prices = wallet_price_client.fetch_usd_prices_by_address(sorted(pairs)) if pairs else {}
    except wallet_price_client.PriceClientError as exc:
        for chain_result in v4_results:
            for pos in chain_result.get("positions", []):
                if pos.get("active"):
                    pos["usd_pricing_error"] = _redact(exc)
        return
    for chain_result in v4_results:
        slug = rpc.ALCHEMY_NETWORK_SLUG_BY_CHAIN.get(chain_result["chain_id"])
        if not slug:
            continue
        for pos in chain_result.get("positions", []):
            if not pos.get("active"):
                continue
            value = fees = 0.0
            value_complete = fees_complete = True
            price_by_index: dict[int, float | None] = {}
            decimals_by_index: dict[int, int | None] = {}
            for i in (0, 1):
                price = prices.get((slug, pos.get(f"_v4_price_addr_{i}")))
                amount = pos.get(f"token{i}_amount")
                raw_fee = int(pos.get(f"fees_owed_{i}_raw", "0"))
                decimals = (pos.get(f"token{i}") or {}).get("decimals")
                price_by_index[i] = price
                decimals_by_index[i] = decimals
                pos[f"token{i}_usd_price"] = price
                pos[f"token{i}_value_usd"] = (
                    None if amount is None or (amount != 0 and price is None)
                    else 0.0 if amount == 0
                    else price * amount
                )
                if amount is None:
                    value_complete = False
                elif amount != 0:
                    if price is None:
                        value_complete = False
                    else:
                        value += price * amount
                if raw_fee:
                    if price is None or decimals is None:
                        fees_complete = False
                    else:
                        fees += price * raw_fee / (10 ** decimals)
            pos["position_value_usd"] = value if value_complete else None
            pos["fees_owed_usd"] = fees if fees_complete else None
            missing_prices = [
                (pos.get(f"token{i}") or {}).get("symbol") or f"token{i}"
                for i in (0, 1)
                if pos.get(f"token{i}_amount") and pos.get(f"token{i}_usd_price") is None
            ]
            if missing_prices:
                pos["value_unsupported_reason"] = (
                    "Alchemy Prices API 未回傳 USD 報價：" + ", ".join(missing_prices)
                    + "；不以部分代幣估值冒充整筆 LP 價值。"
                )
            pos.pop("_v4_price_addr_0", None)
            pos.pop("_v4_price_addr_1", None)

            # anne 核正同款規則套用在 V4：③本期新增收入 Δ 只有
            # v4_delta_quality_status=="ok" 時，原幣精確值才可信；USD 換算
            # 還要求兩邊代幣同時有即時報價，缺價維持 None，不當 0。
            quality_status = pos.get("v4_delta_quality_status")
            if quality_status != "ok":
                pos["fee_income_delta_usd"] = None
                pos["fee_income_delta_note"] = pos.get("v4_delta_quality_reason")
            else:
                delta_usd_sum = 0.0
                delta_complete = True
                for i in (0, 1):
                    raw_val = pos.get(f"fee_income_delta_token{i}_raw")
                    decimals = decimals_by_index.get(i)
                    price = price_by_index.get(i)
                    if raw_val is None or decimals is None:
                        delta_complete = False
                        continue
                    amount = int(raw_val) / (10 ** decimals)
                    if amount != 0:
                        if price is None:
                            delta_complete = False
                        else:
                            delta_usd_sum += price * amount
                if delta_complete:
                    pos["fee_income_delta_usd"] = delta_usd_sum
                    pos["fee_income_delta_note"] = None
                else:
                    pos["fee_income_delta_usd"] = None
                    pos["fee_income_delta_note"] = (
                        "Δ 原幣精確值已算出，但 token0/token1 缺即時 USD 報價，不以 0 頂替。"
                    )


def write_snapshots(chain_results: list[dict]) -> int:
    """把這次查到的活躍 V3/V4 部位寫進本機每日快照 SQLite；回傳寫入筆數。
    快照本身是不是「第一筆」由 wallet_snapshot_store/wallet_apr_calc 依歷史
    筆數判斷，本函式不做任何造數字的事。"""
    conn = store.get_connection()
    ts = int(time.time())
    count = 0
    for chain_result in chain_results:
        chain_id = chain_result["chain_id"]
        protocol = chain_result.get("protocol")
        for pos in chain_result.get("positions", []):
            liquidity = pos.get("liquidity_raw", pos.get("liquidity", 0))
            if "error" in pos or "token_id" not in pos or int(liquidity or 0) <= 0:
                continue
            pool_addr = pos.get("pool_address") if protocol == "v3" else pos.get("pool_id_hex")
            if not pool_addr:
                continue
            store.insert_snapshot(
                conn,
                ts=ts,
                wallet_addr=WALLET_ADDRESS,
                chain_id=chain_id,
                token_id=pos["token_id"],
                pool_addr=pool_addr,
                tick_lower=pos["tick_lower"],
                tick_upper=pos["tick_upper"],
                liquidity=str(liquidity),
                in_range=bool(pos.get("in_range")),
                fees_accrued_usd=pos.get("fees_owed_usd"),
                position_value_usd=pos.get("position_value_usd"),
                claimable_0_raw=pos.get("fees_owed_0_raw"),
                claimable_1_raw=pos.get("fees_owed_1_raw"),
                cumulative_claimed_0_raw=pos.get("cumulative_claimed_0_raw"),
                cumulative_claimed_1_raw=pos.get("cumulative_claimed_1_raw"),
                cumulative_claimed_usd=pos.get("cumulative_claimed_usd"),
                cumulative_decrease_principal_usd=pos.get("cumulative_decrease_principal_usd"),
                cumulative_fee_income_usd=pos.get("cumulative_fee_income_usd"),
                # V3：fee_income_delta_usd 要跟「上一筆快照」比較才能算，寫入當下
                # 這筆 snapshot 時還沒有「上一筆」可比——改在 attach_observed_apr()
                # 讀到完整歷史後現算，再透過 update_fee_income_delta() 回填這一筆。
                # V4：Δ 在 fetch_v4_chain()／enrich_v4_with_usd() 已經用原幣精確
                # feeGrowth diff 算好（見 v4_delta_quality_status），這裡直接寫入，
                # attach_observed_apr() 不會再覆蓋 v4 部位這欄。
                fee_income_delta_usd=(pos.get("fee_income_delta_usd") if protocol == "v4" else None),
                v4_fee_growth_inside0_raw=pos.get("v4_fee_growth_inside0_raw"),
                v4_fee_growth_inside1_raw=pos.get("v4_fee_growth_inside1_raw"),
                snapshot_block_number=pos.get("v4_snapshot_block_number"),
                v4_delta_quality_status=pos.get("v4_delta_quality_status"),
                v4_delta_quality_reason=pos.get("v4_delta_quality_reason"),
            )
            count += 1
    conn.close()
    return count


def _sum_value_fees(row: dict) -> float | None:
    v, f = row.get("position_value_usd"), row.get("fees_accrued_usd")
    if v is None and f is None:
        return None
    return (v or 0.0) + (f or 0.0)


def compute_portfolio_daily_delta(daily_rows_asc: list[dict]) -> dict:
    """以同 scope_key 的每日總值計算「較前一個成功日」變化。"""
    empty = {
        "comparable": False,
        "previous_snapshot_date": None,
        "delta_usd": None,
        "delta_pct": None,
        "note": "尚無每日估值快照。",
    }
    if not daily_rows_asc:
        return empty

    current = daily_rows_asc[-1]
    if not current.get("valuation_complete") or current.get("total_value_usd") is None:
        reason = current.get("data_quality_note") or "目前活躍 LP 尚未取得完整 USD 估值"
        return {
            **empty,
            "note": f"較昨日部位總值暫不能計算：{reason}",
        }

    previous = next(
        (
            row for row in reversed(daily_rows_asc[:-1])
            if row.get("valuation_complete") and row.get("total_value_usd") is not None
        ),
        None,
    )
    if previous is None:
        return {
            **empty,
            "note": "基準已建立；需累積 2 個可比較的每日成功快照，預計在下一次成功日更後顯示。",
        }

    current_value = float(current["total_value_usd"])
    previous_value = float(previous["total_value_usd"])
    delta_usd = current_value - previous_value
    delta_pct = None if previous_value == 0 else delta_usd / previous_value * 100
    return {
        "comparable": True,
        "previous_snapshot_date": previous["snapshot_date"],
        "delta_usd": delta_usd,
        "delta_pct": delta_pct,
        "note": "前一日總值為 0，百分比無法定義。" if previous_value == 0 else None,
    }


def summarize_active_portfolio(chain_results: list[dict]) -> dict:
    """彙總使用者真正仍持有流動性的部位；缺價時不做部分加總。"""
    active_positions: list[dict] = []
    failed_scopes: list[str] = []
    for chain_result in chain_results:
        if chain_result.get("error"):
            failed_scopes.append(
                f"{chain_result.get('chain_name', '未知鏈')} {chain_result.get('protocol', '')}".strip()
            )
        for pos in chain_result.get("positions", []):
            liquidity = pos.get("liquidity_raw", pos.get("liquidity", 0))
            if pos.get("active") is True or int(liquidity or 0) > 0:
                active_positions.append(pos)

    missing_values = sum(1 for pos in active_positions if pos.get("position_value_usd") is None)
    missing_fees = sum(1 for pos in active_positions if pos.get("fees_owed_usd") is None)
    valuation_complete = not failed_scopes and missing_values == 0
    fees_complete = not failed_scopes and missing_fees == 0
    notes = []
    if failed_scopes:
        notes.append("查詢失敗：" + "、".join(failed_scopes))
    if missing_values:
        notes.append(f"{missing_values}/{len(active_positions)} 個活躍部位缺少完整 USD 報價")
    if missing_fees:
        notes.append(f"{missing_fees}/{len(active_positions)} 個活躍部位的可領 fee 無法完整換算 USD")

    return {
        "active_position_count": len(active_positions),
        "total_value_usd": (
            sum(float(pos["position_value_usd"]) for pos in active_positions)
            if valuation_complete else None
        ),
        "fees_owed_usd": (
            sum(float(pos["fees_owed_usd"]) for pos in active_positions)
            if fees_complete else None
        ),
        "valuation_complete": valuation_complete,
        "fees_complete": fees_complete,
        "data_quality_note": "；".join(notes) if notes else None,
    }


def attach_observed_apr(chain_results: list[dict]) -> None:
    """依本機快照歷史算「快照實測個人 APR」，跟池子級估算 APR 分欄。"""
    conn = store.get_connection()
    for chain_result in chain_results:
        chain_id = chain_result["chain_id"]
        protocol = chain_result.get("protocol")
        for pos in chain_result.get("positions", []):
            if "token_id" not in pos or "error" in pos:
                continue
            liquidity = pos.get("liquidity_raw", pos.get("liquidity", 0))
            if int(liquidity or 0) <= 0:
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

                if protocol == "v3":
                    # anne 2026-10-03 核正版 ③本期新增收入 Δ ＝ 本期末累計 fee 收入
                    # － 上期末累計 fee 收入；累計 fee 收入本身已經在 enrich_with_usd()
                    # 套過精確恆等式（累計已領＋目前可提領總額－累計撤出本金）扣掉了
                    # decreaseLiquidity() 的本金，這裡只是單純做差，不再重複拆本金。
                    # 故意不拿 delta_24h_usd（部位總值+可提領總額的總變化）當 fee
                    # 收入——那會把幣價漲跌、也可能把撤資本金都算成 fee，anne 明確禁止。
                    today_cum_income = today_row.get("cumulative_fee_income_usd")
                    prev_cum_income = prev_row.get("cumulative_fee_income_usd")
                    delta_result = wallet_apr_calc.compute_fee_income_delta(
                        today_cum_income, prev_cum_income
                    )
                    pos["fee_income_delta_usd"] = delta_result["delta_usd"]
                    pos["fee_income_delta_note"] = delta_result["note"]
                    store.update_fee_income_delta(
                        conn,
                        ts=today_row["ts"],
                        wallet_addr=WALLET_ADDRESS,
                        chain_id=chain_id,
                        token_id=pos["token_id"],
                        fee_income_delta_usd=delta_result["delta_usd"],
                    )
                # v4：fee_income_delta_usd／note 已在 enrich_v4_with_usd() 用原幣
                # 精確 Δ（feeGrowthInside diff，見 v4_delta_quality_status）＋即時
                # 報價算好，也已經在 write_snapshots() 寫進這一筆快照——這裡刻意
                # 不覆蓋：V3 那套「累計 Collect 恆等式做差」依賴 cumulative_fee_
                # income_usd，V4 目前沒有累計已領事件掃描（StateView 不提供事件），
                # 硬套這段邏輯只會把剛算好的正確值蓋成 None。
            else:
                pos["delta_24h_usd"] = None
                pos["base_established"] = True
                pos["delta_note"] = "基準已建立；第二筆快照後才有實測每日 delta"
                if protocol == "v3":
                    pos["fee_income_delta_usd"] = None
                    pos["fee_income_delta_note"] = "基準已建立；第二筆快照後才有「本期新增收入 Δ」"
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
            has_liquidity_marker = (
                pos.get("active") is not None
                or pos.get("liquidity_raw") is not None
                or pos.get("liquidity") is not None
            )
            liquidity = pos.get("liquidity_raw", pos.get("liquidity", 0))
            is_active = pos.get("active") is True or int(liquidity or 0) > 0
            if has_liquidity_marker and not is_active:
                # 歷史／已退出 NFT 仍保留在 wallet_live_latest.json 供內部稽核，
                # 但不進使用者可見的頁面資料。
                continue
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
            source_notes = [
                pos.get("fees_source"), pos.get("fees_unsupported_reason"),
                pos.get("pool_state_source"), pos.get("value_unsupported_reason"),
                pos.get("note"), pos.get("error"), chain_result.get("enumeration_source"),
            ]
            rows.append({
                "chain_name": chain_name,
                "protocol": protocol,
                "pair_label": pair_label,
                "fee_tier_pct": pos.get("fee_tier_pct"),
                "position_value_usd": pos.get("position_value_usd"),
                "fees_owed_usd": pos.get("fees_owed_usd"),
                "position_status": pos.get("position_status") or (
                    "活躍（非零 liquidity）"
                    if pos.get("active") is True or int(pos.get("liquidity_raw", pos.get("liquidity", "0")) or 0) > 0
                    else "已退出／無流動性"
                ) if (pos.get("liquidity_raw") is not None or pos.get("liquidity") is not None
                      or pos.get("active") is not None) else None,
                "token0_symbol": _symbol_of(pos.get("token0")),
                "token1_symbol": _symbol_of(pos.get("token1")),
                "token0_value_usd": pos.get("token0_value_usd"),
                "token1_value_usd": pos.get("token1_value_usd"),
                "in_range": pos.get("in_range"),
                "delta_24h_usd": pos.get("delta_24h_usd"),
                "cumulative_claimed_usd": pos.get("cumulative_claimed_usd"),
                "cumulative_decrease_principal_usd": pos.get("cumulative_decrease_principal_usd"),
                "cumulative_fee_income_usd": pos.get("cumulative_fee_income_usd"),
                "fee_income_delta_usd": pos.get("fee_income_delta_usd"),
                "fee_income_delta_note": (
                    pos.get("fee_income_delta_note")
                    or pos.get("cumulative_fee_income_note")
                    or pos.get("cumulative_claimed_unsupported_reason")
                    or pos.get("cumulative_decrease_principal_unsupported_reason")
                ),
                "observed_apr_7d_pct": pos.get("observed_apr_7d_pct"),
                "observed_apr_30d_pct": pos.get("observed_apr_30d_pct"),
                "base_established": pos.get("base_established"),
                "delta_note": pos.get("delta_note") or pos.get("observed_apr_note"),
                "snapshot_time": chain_result.get("queried_at"),
                "source": "；".join(dict.fromkeys(str(note) for note in source_notes if note)),
                "row_error": pos.get("error") or chain_error,
            })
        if not chain_result.get("positions"):
            # 0 部位與查詢失敗都必須逐鏈可見；0 是成功查詢結果，不能隱藏。
            zero_status = "查詢錯誤" if chain_error else "已查詢＝0"
            query_note = chain_error or (
                f"{zero_status}；block {chain_result.get('block_number', 'N/A')}；"
                f"{datetime.fromtimestamp(chain_result.get('queried_at', 0), tz=timezone.utc).isoformat()}"
            )
            rows.append({
                "chain_name": chain_name,
                "protocol": protocol,
                "pair_label": None,
                "fee_tier_pct": None,
                "position_value_usd": None,
                "fees_owed_usd": None,
                "position_status": zero_status,
                "token0_symbol": None,
                "token1_symbol": None,
                "token0_value_usd": None,
                "token1_value_usd": None,
                "in_range": None,
                "delta_24h_usd": None,
                "cumulative_claimed_usd": None,
                "cumulative_decrease_principal_usd": None,
                "cumulative_fee_income_usd": None,
                "fee_income_delta_usd": None,
                "fee_income_delta_note": None,
                "observed_apr_7d_pct": None,
                "observed_apr_30d_pct": None,
                "base_established": None,
                "delta_note": None,
                "snapshot_time": chain_result.get("queried_at"),
                "source": query_note,
                "row_error": chain_error,
            })
    return rows


def main() -> int:
    smoke = [chain_smoke(cid) for cid in sorted(set(V3_CHAINS + V4_CHAINS))]
    smoke_blocks = {r["chain_id"]: r.get("block_number") for r in smoke if r.get("ok")}
    graph_key_present = bool(__import__("os").environ.get("GRAPH_API_KEY", "").strip())

    whitelist = common.build_token_whitelist(common.load_config())

    v3_results = [fetch_v3_chain(cid, whitelist, smoke_blocks.get(cid)) for cid in V3_CHAINS]
    v4_results = [fetch_v4_chain(cid, smoke_blocks.get(cid)) for cid in V4_CHAINS]

    enrich_with_usd(v3_results)
    enrich_v4_with_usd(v4_results)
    snapshot_count = 0
    try:
        snapshot_count = write_snapshots(v3_results + v4_results)
        attach_observed_apr(v3_results + v4_results)
    except store.WalletTrackerMigrationError as exc:
        for r in v3_results + v4_results:
            r["snapshot_error"] = str(exc)

    generated_at = int(time.time())

    portfolio_summary = summarize_active_portfolio(v3_results + v4_results)
    snapshot_date = datetime.fromtimestamp(generated_at, tz=_TAIPEI_TZ).strftime("%Y-%m-%d")
    portfolio_daily_delta: dict = {
        "comparable": False,
        "previous_snapshot_date": None,
        "delta_usd": None,
        "delta_pct": None,
        "note": "每日總值快照尚未寫入（DB 連線失敗）。",
    }
    try:
        conn = store.get_connection()
        try:
            store.upsert_daily_value_snapshot(
                conn,
                snapshot_date=snapshot_date,
                ts=generated_at,
                wallet_addr=WALLET_ADDRESS,
                scope_key=PORTFOLIO_SCOPE_KEY,
                active_position_count=portfolio_summary["active_position_count"],
                total_value_usd=portfolio_summary["total_value_usd"],
                valuation_complete=portfolio_summary["valuation_complete"],
                data_quality_note=portfolio_summary["data_quality_note"],
            )
            history = store.fetch_daily_value_history(
                conn, wallet_addr=WALLET_ADDRESS, scope_key=PORTFOLIO_SCOPE_KEY
            )
            portfolio_daily_delta = compute_portfolio_daily_delta(history)
        finally:
            conn.close()
    except store.WalletTrackerMigrationError as exc:
        portfolio_daily_delta["note"] = f"每日總值快照寫入失敗：{exc}"

    output = {
        "wallet_address": WALLET_ADDRESS,
        "generated_at": generated_at,
        "rpc_smoke": smoke,
        "graph_api_key_present": graph_key_present,
        "v3": v3_results,
        "v4": v4_results,
        "snapshot_rows_written": snapshot_count,
        "portfolio_summary": portfolio_summary,
        "portfolio_daily_delta": portfolio_daily_delta,
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
