"""共用工具：白名單查驗、fee tier 換算、APR 公式、schema 常數。

唯讀模組，不含任何憑證，不對外發出請求。
"""
from __future__ import annotations

import json
import os
import re
import sys
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


GRAPH_API_KEY_ENV = "GRAPH_API_KEY"
_GRAPH_KEY_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def graph_api_key_format_ok(value: str | None) -> bool:
    """The Graph Studio API key 的格式規則：64 個十六進位字元。

    只驗證*格式*，不驗證*有效性*（key 是否真的存在/未過期需要真的打一次
    query 才能知道，本函式故意不做網路呼叫）。這條規則是為了擋下常見的
    人為複製錯誤——例如使用者只選到 key 顯示行的前 12 碼——讓錯誤在
    「送出網路請求前」就被攔下，而不是等到 GraphQL 端回一個難懂的 401。
    """
    if not value:
        return False
    return bool(_GRAPH_KEY_HEX64_RE.match(value.strip()))


def require_graph_api_key(env_var: str = GRAPH_API_KEY_ENV) -> str:
    """讀取並驗證 Graph API key 格式；讀不到或格式不對就印出對使用者友善的
    修正指引後 exit(2)。絕不放行格式錯誤的 key，也絕不 print/log key 本身
    （格式錯誤訊息只印長度，不印內容）。呼叫端（daily cron / GraphQL client）
    一律透過本函式取得 key，不要各自寫 os.environ.get 判斷式。
    """
    value = os.environ.get(env_var)
    if not value:
        print(
            f"ERROR: 未設定環境變數 {env_var}，本腳本不會用假資料代替。"
            f" 請先到 https://thegraph.com/studio/apikeys/ 建立 key，"
            f" 存到 keychain 後在本 shell export {env_var}"
            f"（本工具絕不讀取/顯示 key 本身）。",
            file=sys.stderr,
        )
        sys.exit(2)
    stripped = value.strip()
    if not graph_api_key_format_ok(stripped):
        print(
            f"ERROR: {env_var} 長度為 {len(stripped)} 碼，不是 The Graph API key"
            f" 應有的 64 個十六進位字元——常見原因是複製時被截斷（例如只選到"
            f" key 顯示行的前 12 碼）。請回到 https://thegraph.com/studio/apikeys/"
            f" 確認完整 64 碼字串後重新 export，本工具不會用不完整的 key 嘗試連線。",
            file=sys.stderr,
        )
        sys.exit(2)
    return stripped


def chain_name_for(config: dict, chain_id: int) -> str:
    for chain in config["chains"]:
        if chain["chain_id"] == chain_id:
            return chain["chain_name"]
    for ref in config.get("special_pool_references", []):
        if ref["chain_id"] == chain_id:
            return ref["chain_name"]
    return f"chain-{chain_id}"
