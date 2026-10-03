#!/usr/bin/env python3
"""方案 B（本機排程追蹤）錢包個人 LP APR 的純計算層。

輸入是 wallet_snapshot_store.fetch_position_history() 回傳的每日快照序列
（依 ts 由舊到新排序），完全不碰資料庫、不做網路呼叫，方便餵合成資料做
單元測試（anne 要求：先完成「合成資料→連續快照→delta／APR」離線測試，
真實 RPC 接上是下一階段）。

分母規則（research 原話，沿用不變）：APR 分母鎖「平均部位 USD」
（`position_value_usd` 的期間平均），不是 pool 的 TVL——兩者尺度差好幾個
量級，算錯會出現虛胖的 4 位數 % APR。

`fees_accrued_usd` 的計算方式（fee-growth 差值或唯讀 eth_call 模擬
collect()）完全是呼叫端（RPC 整合層）的責任；本模組拿到的值一律當成
「這個快照週期內新產生的手續費」直接使用，不做任何比例估算。
"""
from __future__ import annotations

import common

MIN_DAYS_FOR_7D = 7
MIN_DAYS_FOR_30D = 30


def compute_wallet_position_metrics(daily_rows_asc: list[dict]) -> dict:
    """回傳 {days_tracked, fee_apr_7d_pct, fee_apr_30d_pct, note}。

    規則：
      - 完全沒有快照 -> 全部 None，note 說明「尚未開始追蹤」。
      - 快照天數 < 7 -> 7d/30d 皆 None，附「尚不足計算」，不硬湊年化。
      - 快照天數在 [7, 30) -> 7d APR 可算，30d 仍 None。
      - 快照天數 >= 30 -> 7d／30d 皆可算，兩者都用「最近 N 天」視窗
        （不是全部歷史平均），跟 common.compute_pool_day_metrics 的 7d
        視窗邏輯一致，方便維護者對照。
      - 某天的 `fees_accrued_usd` 若為 None（例如那天 RPC 失敗、沒能算出
        來），整個窗口的加總視為不可信，回 None 並在 note 指出哪些日期
        缺資料——不能用 0 頂替（0 是「真的沒收到手續費」的合法值）。
    """
    n = len(daily_rows_asc)
    result = {
        "days_tracked": n,
        "fee_apr_7d_pct": None,
        "fee_apr_30d_pct": None,
        "note": None,
    }

    if n == 0:
        result["note"] = "尚未開始追蹤：這個部位目前沒有任何每日快照"
        return result

    if n < MIN_DAYS_FOR_7D:
        result["note"] = (
            f"尚不足計算（目前已追蹤 {n} 天，7d APR 需要 {MIN_DAYS_FOR_7D} 天，"
            f"30d APR 需要 {MIN_DAYS_FOR_30D} 天）"
        )
        return result

    window7 = daily_rows_asc[-MIN_DAYS_FOR_7D:]
    apr7, missing7 = _window_apr(window7, MIN_DAYS_FOR_7D)
    result["fee_apr_7d_pct"] = apr7

    if n < MIN_DAYS_FOR_30D:
        note = f"7d APR 已可計算；30d APR 尚不足計算（目前已追蹤 {n} 天，需要 {MIN_DAYS_FOR_30D} 天）"
        if missing7:
            note = f"7d 視窗內有 {missing7} 天缺 fees_accrued_usd，7d APR 暫不可信；" + note
            result["fee_apr_7d_pct"] = None
        result["note"] = note
        return result

    window30 = daily_rows_asc[-MIN_DAYS_FOR_30D:]
    apr30, missing30 = _window_apr(window30, MIN_DAYS_FOR_30D)
    result["fee_apr_30d_pct"] = apr30

    notes = []
    if missing7:
        notes.append(f"7d 視窗內有 {missing7} 天缺 fees_accrued_usd，7d APR 暫不可信")
        result["fee_apr_7d_pct"] = None
    if missing30:
        notes.append(f"30d 視窗內有 {missing30} 天缺 fees_accrued_usd，30d APR 暫不可信")
        result["fee_apr_30d_pct"] = None
    result["note"] = "；".join(notes) if notes else None
    return result


def compute_cumulative_fee_income_usd(
    cumulative_collected_usd: float | None,
    current_claimable_usd: float | None,
    cumulative_decrease_principal_usd: float | None,
) -> dict:
    """anne 2026-10-03 核正後的精確恆等式：

        累計 fee 收入 = 累計 Collect 已領 ＋ 目前可提領總額 － 累計 DecreaseLiquidity 本金

    背景：V3 的 `decreaseLiquidity()` 會把撤出的本金也寫進
    `position.tokensOwed`，之後不管是 `Collect` 事件還是
    `simulate_collect()` 的結果都是「本金＋fee」混在一起，單獨看任何一個
    都分不出哪部分是本金——必須額外扣掉「累計 DecreaseLiquidity 本金」才能
    還原出純 fee 收入。在沒有另外拆 fee-growth 的前提下，`current_claimable_usd`
    （目前可提領總額）本身**不是**純 fee，只有套進這個恆等式、再跟上一筆快照
    做差之後，才能得到可信的「本期新增 fee 收入」。

    任何一段輸入是 None（資料缺失或 RPC 失敗），整段回 None，不做「缺一段
    就當 0」的假設——那樣會把「不知道」偽裝成「真的是 0」。
    """
    if (
        cumulative_collected_usd is None
        or current_claimable_usd is None
        or cumulative_decrease_principal_usd is None
    ):
        missing = [
            name for name, v in (
                ("累計 Collect 已領", cumulative_collected_usd),
                ("目前可提領總額", current_claimable_usd),
                ("累計 DecreaseLiquidity 本金", cumulative_decrease_principal_usd),
            ) if v is None
        ]
        return {
            "value_usd": None,
            "note": "缺少「" + "、".join(missing) + "」資料，無法套用精確恆等式算出累計 fee 收入",
        }
    value = cumulative_collected_usd + current_claimable_usd - cumulative_decrease_principal_usd
    return {"value_usd": value, "note": None}


def compute_fee_income_delta(
    current_cumulative_fee_income_usd: float | None,
    previous_cumulative_fee_income_usd: float | None,
) -> dict:
    """③本期新增收入 Δ ＝ 本期末累計 fee 收入 － 上期末累計 fee 收入。

    輸入必須是 compute_cumulative_fee_income_usd() 算出來的「已扣除
    DecreaseLiquidity 本金」的累計值，不能直接拿 claimable 或 tokensOwed
    代入——那兩者都可能混著本金，會把撤資誤算成收入（anne 2026-10-03 核正）。
    這個函式單純做差，取差之前的「拆本金」工作已經在
    compute_cumulative_fee_income_usd() 做完，不在這裡重複判斷。

    任一輸入是 None 就回 None，不當 0。
    """
    if current_cumulative_fee_income_usd is None or previous_cumulative_fee_income_usd is None:
        missing = [
            name for name, v in (
                ("本期末累計 fee 收入", current_cumulative_fee_income_usd),
                ("上期末累計 fee 收入", previous_cumulative_fee_income_usd),
            ) if v is None
        ]
        return {
            "delta_usd": None,
            "note": "缺少「" + "、".join(missing) + "」資料，無法計算本期新增收入 Δ",
        }
    delta = current_cumulative_fee_income_usd - previous_cumulative_fee_income_usd
    return {"delta_usd": delta, "note": None}


def compute_v4_fee_delta_quality(
    previous_row: dict | None,
    current_raw0: int,
    current_raw1: int,
    current_liquidity: int,
    has_activity: bool | None,
    activity_error: str | None = None,
) -> dict:
    """V4 本期新增收入（原幣精確 Δ）的品質判定——t_4be89664 第 2 段。

    回傳 {"status", "reason", "delta0_raw", "delta1_raw"}。

    規則（fail-closed，禁止用 block-1 feeGrowth 回算或估算冒充精確已領）：
      - previous_row 是 None，或缺 v4_fee_growth_inside0/1_raw／
        snapshot_block_number（第一筆快照、舊資料、或尚未升級的呼叫端）
        -> "insufficient_snapshot"，Δ 皆 None。
      - activity_error 非 None（has_v4_position_activity_in_range() 本身
        查詢失敗）-> "activity_unknown"，fail-closed：絕不假設沒有活動。
      - current_raw < previous_raw（理論上 feeGrowthInside 單調不減，出現
        下降代表資料異常或 pool 重建）-> "activity_unknown"，不計算 Δ。
      - has_activity is True（區間內偵測到這個部位的 ModifyLiquidity）
        -> "activity_detected"：checkpoint／liquidity 可能已變動，diff 不再
        是精確值，Δ 皆 None。
      - 以上都通過（確認無活動、上一筆快照齊全、raw 值單調不減）-> "ok"：
        diff = (current_raw − previous_raw) * liquidity // 2**128 是精確值
        （不是估算）——因為確認期間內 liquidity／checkpoint 都沒被動過，
        feeGrowthInside 的增量本身就等於這段期間真正新增的手續費。
    """
    if previous_row is None:
        return {
            "status": "insufficient_snapshot",
            "reason": "尚無上一筆 v4 快照，無法建立比較基準（第一筆快照，基準已建立）。",
            "delta0_raw": None,
            "delta1_raw": None,
        }
    prev_raw0 = previous_row.get("v4_fee_growth_inside0_raw")
    prev_raw1 = previous_row.get("v4_fee_growth_inside1_raw")
    prev_block = previous_row.get("snapshot_block_number")
    if prev_raw0 is None or prev_raw1 is None or prev_block is None:
        return {
            "status": "insufficient_snapshot",
            "reason": "上一筆快照缺 v4 fee growth 原始值或區塊高度（可能是舊資料或尚未升級的快照）。",
            "delta0_raw": None,
            "delta1_raw": None,
        }
    if activity_error is not None:
        return {
            "status": "activity_unknown",
            "reason": f"查詢此部位期間是否有活動失敗，fail-closed 不假設沒有活動：{activity_error}",
            "delta0_raw": None,
            "delta1_raw": None,
        }
    try:
        prev_raw0_int = int(prev_raw0)
        prev_raw1_int = int(prev_raw1)
    except (TypeError, ValueError):
        return {
            "status": "insufficient_snapshot",
            "reason": "上一筆快照的 v4 fee growth 原始值格式異常，無法比較。",
            "delta0_raw": None,
            "delta1_raw": None,
        }
    if current_raw0 < prev_raw0_int or current_raw1 < prev_raw1_int:
        return {
            "status": "activity_unknown",
            "reason": "feeGrowthInside 原始值較上一筆下降，與「無活動」假設矛盾（可能是資料異常），fail-closed 不計算 Δ。",
            "delta0_raw": None,
            "delta1_raw": None,
        }
    if has_activity:
        return {
            "status": "activity_detected",
            "reason": "上一筆快照～本次快照之間偵測到這個部位的 ModifyLiquidity 活動，checkpoint／liquidity 可能已變動，差值不可信。",
            "delta0_raw": None,
            "delta1_raw": None,
        }
    delta0_raw = (current_raw0 - prev_raw0_int) * current_liquidity // (2**128)
    delta1_raw = (current_raw1 - prev_raw1_int) * current_liquidity // (2**128)
    return {"status": "ok", "reason": None, "delta0_raw": delta0_raw, "delta1_raw": delta1_raw}


def _window_apr(window_rows: list[dict], window_days: int) -> tuple[float | None, int]:
    """回傳 (apr_pct或None, 缺 fees_accrued_usd 的天數)。"""
    missing = sum(1 for r in window_rows if r.get("fees_accrued_usd") is None)
    if missing:
        return None, missing
    fees_sum = sum(r["fees_accrued_usd"] for r in window_rows)
    values = [r.get("position_value_usd") for r in window_rows if r.get("position_value_usd") is not None]
    if not values:
        return None, 0
    avg_value = sum(values) / len(values)
    return common.fee_apr_pct(fees_sum, window_days, avg_value), 0
