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


def compute_fee_income_delta(
    current_claimable_usd: float | None,
    previous_claimable_usd: float | None,
    period_claimed_usd: float | None,
) -> dict:
    """anne 2026-10-03 spec：③本期新增收入 Δ ＝ 本期末可領 − 上期末可領 ＋ 期間已領。

    這個公式刻意不是「單純比較兩次可領金額的差」——如果中途使用者真的
    claim 過一次，可領金額會先被清空再重新累積，單純比較會算出一個很大的
    負值（誤以為這期虧了手續費），加回「期間已領」才能還原出這期真正新
    產生的手續費收入，不受中途是否 claim 影響。

    任何一個輸入是 None（表示某一段資料缺失或 RPC 失敗），整段回 None，
    不做「缺一段就當 0」的假設——那樣會把「不知道」偽裝成「真的是 0」。
    """
    if current_claimable_usd is None or previous_claimable_usd is None or period_claimed_usd is None:
        missing = [
            name for name, v in (
                ("本期末可領", current_claimable_usd),
                ("上期末可領", previous_claimable_usd),
                ("期間已領", period_claimed_usd),
            ) if v is None
        ]
        return {
            "delta_usd": None,
            "note": "缺少「" + "、".join(missing) + "」資料，無法計算本期新增收入 Δ",
        }
    delta = current_claimable_usd - previous_claimable_usd + period_claimed_usd
    return {"delta_usd": delta, "note": None}


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
