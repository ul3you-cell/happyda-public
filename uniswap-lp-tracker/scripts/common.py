"""共用工具：白名單查驗、fee tier 換算、APR 公式、schema 常數。

唯讀模組，不含任何憑證，不對外發出請求。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "pools_targets.json"
DATA_DIR = PROJECT_ROOT / "data"
FIXTURES_DIR = DATA_DIR / "fixtures"

ENDPOINT = "https://liquidity.api.uniswap.org/lp/pool_info"

# 官方文件未公布 RPM；唯一訊號是 HTTP 429。這裡的退避參數是本專案的工程選擇，
# 不是官方保證值 —— 詳見 uniswap-lp-tracker/README.md 與 handoff.md。
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_RETRIES = 5
REQUEST_MIN_INTERVAL_SECONDS = 0.6  # 禮貌節流；文件範例快取視窗為 15s（同一 pool 重複查詢時適用）


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_token_whitelist(config: dict) -> dict:
    """回傳 {chain_id: {address_lower: {symbol, decimals, source}}}"""
    whitelist: dict[int, dict[str, dict]] = {}
    for chain in config["chains"]:
        cid = chain["chain_id"]
        bucket = whitelist.setdefault(cid, {})
        for tok in chain["quote_tokens"] + chain["base_tokens"]:
            bucket[tok["address"].lower()] = {
                "symbol": tok["symbol"],
                "decimals": tok["decimals"],
                "source": tok["source"],
            }
    return whitelist


def lookup_token(whitelist: dict, chain_id: int, address: str | None) -> dict | None:
    if not address:
        return None
    return whitelist.get(chain_id, {}).get(address.lower())


def fee_raw_to_pct(fee_raw) -> float | None:
    """Uniswap fee 欄位單位：hundredths of a bip *不是* — 依官方範例
    fee=3000 代表 0.30%，換算式為 fee_raw / 10000（結果單位＝百分比數值，
    即 0.3 代表 0.30%）。"""
    if fee_raw is None:
        return None
    try:
        return round(float(fee_raw) / 10000.0, 6)
    except (TypeError, ValueError):
        return None


def tick_to_price_ratio(tick) -> float | None:
    """v3/v4 tick -> raw price ratio (token1 per token0), 未做 decimals 校正。
    僅供顯示 active range 邊界用，不代表 USD 價格。"""
    if tick is None:
        return None
    try:
        return 1.0001 ** float(tick)
    except (TypeError, ValueError, OverflowError):
        return None


def fee_apr_pct(fees_usd_window: float, window_days: float, tvl_usd: float) -> float | None:
    """業界通用（非官方）fee APR 公式：
    APR% = fees_usd_over_window / tvl_usd * (365 / window_days) * 100
    需要 Subgraph 提供的 feesUSD 與 TVL_USD 才能計算；純 pool_info 無法算。
    保留此函式供未來接上 Subgraph 後直接呼叫，並提供單元測試覆蓋公式本身。
    """
    if tvl_usd is None or tvl_usd == 0 or fees_usd_window is None or window_days in (None, 0):
        return None
    return round((fees_usd_window / tvl_usd) * (365.0 / window_days) * 100.0, 4)


def chain_name_for(config: dict, chain_id: int) -> str:
    for chain in config["chains"]:
        if chain["chain_id"] == chain_id:
            return chain["chain_name"]
    for ref in config.get("special_pool_references", []):
        if ref["chain_id"] == chain_id:
            return ref["chain_name"]
    return f"chain-{chain_id}"
