"""共用工具：白名單查驗、fee tier 換算、APR 公式、schema 常數。

唯讀模組，不含任何憑證，不對外發出請求。
"""
from __future__ import annotations

import json
import os
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


def fee_apr_pct(fees_usd_window: float | None, window_days: float | None, tvl_usd: float | None) -> float | None:
    """業界通用（非官方）fee APR 公式：
    APR% = fees_usd_over_window / tvl_usd * (365 / window_days) * 100
    需要 Subgraph 提供的 feesUSD 與 TVL_USD 才能計算；純 pool_info 無法算。
    保留此函式供未來接上 Subgraph 後直接呼叫，並提供單元測試覆蓋公式本身。
    """
    if tvl_usd is None or tvl_usd == 0 or fees_usd_window is None or window_days in (None, 0):
        return None
    return round((fees_usd_window / tvl_usd) * (365.0 / window_days) * 100.0, 4)


GRAPH_API_KEY_ENV = "GRAPH_API_KEY"
GRAPH_API_KEY_SUSPICIOUSLY_SHORT_LEN = 20  # 只用來印非阻斷提醒，不是驗證門檻


def graph_api_key_length_warning(value: str | None) -> str | None:
    """回傳一行「非阻斷」提醒文字，或 None（沒什麼可提醒）。

    2026-09-26 修正：原本這裡強制要求 64 個十六進位字元，anne 查證 The Graph
    官方文件**沒有**規定固定 key 格式，這個硬性長度驗證有誤擋有效 key 的風險
    （見 anne 回報，撤掉硬性驗證）。key 是否真的有效，只能靠一次唯讀查詢是否
    成功來判斷，本模組不做網路呼叫、不猜格式，也不再假裝知道正確長度。
    這裡只保留一個「明顯過短、疑似複製貼上被截斷」的非阻斷提醒（例如使用者
    今天實測遇到的 12 碼），印出來但**不會**因此擋下呼叫端。
    """
    if not value:
        return None
    stripped = value.strip()
    if len(stripped) < GRAPH_API_KEY_SUSPICIOUSLY_SHORT_LEN:
        return (
            f"WARNING: 讀到的 key 長度只有 {len(stripped)} 碼，"
            f"常見原因是複製貼上被截斷；本工具不會因此擋下，"
            f"但接下來若查詢失敗，請先確認 key 是否完整。"
        )
    return None


def require_graph_api_key(env_var: str = GRAPH_API_KEY_ENV) -> str:
    """讀取 Graph API key；缺少就印出使用者友善的修正指引後 exit(2)。

    只驗證「有沒有讀到」，不驗證格式/長度（見 graph_api_key_length_warning
    的說明：The Graph 沒有公開的固定格式，長度不是可靠的正確性訊號）。
    絕不 print/log key 本身；呼叫端（daily cron / GraphQL client）一律透過
    本函式取得 key，不要各自寫 os.environ.get 判斷式。
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
    warning = graph_api_key_length_warning(stripped)
    if warning:
        print(warning, file=sys.stderr)
    return stripped


# The Graph 官方 v3 subgraph deployment ID，逐字抄自
# developers.uniswap.org/docs/ecosystem/subgraphs/overview（2026-09-26 抓取，
# 抓取紀錄見 workspace/research/drafts/uniswap_wallet_data_sources.md §1.A）。
# 目前只確認 Ethereum mainnet 這一條；Arbitrum/Base/BNB 的 deployment ID
# 尚未由 @research 交付，缺的鏈就不放進這個字典——寧可少列，不猜 ID。
GRAPH_V3_DEPLOYMENT_ID_BY_CHAIN: dict[int, str] = {
    1: "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",  # Ethereum mainnet
}

POOL_METRICS_INSUFFICIENT_HISTORY_NOTE = "尚不足計算（該池 The Graph poolDayDatas 完整天數不足，見 fee_apr_note 的天數說明）"


def compute_pool_day_metrics(
    tvl_usd: float | None,
    day_data_desc: list[dict],
    now_ts: float,
) -> dict:
    """把 The Graph v3 subgraph 的 `pool.totalValueLockedUSD` + `poolDayDatas`
    原始資料，換算成儀表板要顯示的 TVL／24h·7d volume／24h·7d fee APR／
    收入變化方向。純函式，不做網路呼叫，方便餵合成資料做單元測試。

    輸入：
      tvl_usd: pool 目前的 totalValueLockedUSD（None 代表沒查到這個 pool）
      day_data_desc: poolDayDatas 陣列，依 date 由新到舊排序（The Graph
        query 用 orderBy: date, orderDirection: desc 即符合此順序），
        每筆至少含 {date:int, volumeUSD, feesUSD}（後兩者可為 str 或 number）
      now_ts: 呼叫當下的 unix timestamp（供判斷「今天」這個尚未結束的
        day bucket，避免把還在累積中的當日資料當成完整 24h 用）

    規則：
      - 當天（date == now_ts // 86400）如果出現在 day_data_desc，視為
        「尚在累積中、不完整」，一律排除，不拿來算 24h/7d 數字。
      - 24h 指標＝最近一個「完整」天的資料；沒有完整天 → 全部回 None，
        並附上「資料不足」原因（不是 0，也不是造數字）。
      - 7d 指標需要至少 7 個完整天；不足 7 天 → 回 None，附
        POOL_METRICS_INSUFFICIENT_HISTORY_NOTE，*不*用不足 7 天的資料
        硬湊一個看起來像樣的年化數字（那樣算出來的 APR 會虛胖或虛胖不定）。
      - `feesUSD` 當天可以合法為 0（out-of-range 完全不收手續費，見
        research 的說明）；0 不是缺值，跟「沒有這一天的資料」是兩回事，
        本函式只用「這個 index 存不存在」判斷夠不夠天數，不用「值是不是
        0」來判斷。
      - income_change_direction 需要至少 2 個完整天才能比較，否則 None。
    """
    result = {
        "tvl_usd": tvl_usd,
        "volume_24h_usd": None,
        "volume_7d_usd": None,
        "fee_apr_24h_pct": None,
        "fee_apr_7d_pct": None,
        "income_change_direction": None,
        "days_available": 0,
        "note": None,
    }

    today_bucket = int(now_ts // 86400)
    completed = [d for d in day_data_desc if int(d["date"]) < today_bucket]
    result["days_available"] = len(completed)

    if not completed:
        result["note"] = "資料不足：The Graph 目前只有當日未結束的資料，尚無完整一天可用於計算"
        return result

    last_day = completed[0]
    fees_24h = float(last_day["feesUSD"])
    volume_24h = float(last_day["volumeUSD"])
    result["volume_24h_usd"] = volume_24h
    result["fee_apr_24h_pct"] = fee_apr_pct(fees_24h, 1, tvl_usd)

    if len(completed) < 7:
        result["note"] = (
            f"{POOL_METRICS_INSUFFICIENT_HISTORY_NOTE}（目前只有 {len(completed)} 個完整天，需要 7 天）"
        )
    else:
        window = completed[:7]
        fees_7d = sum(float(d["feesUSD"]) for d in window)
        volume_7d = sum(float(d["volumeUSD"]) for d in window)
        result["volume_7d_usd"] = volume_7d
        result["fee_apr_7d_pct"] = fee_apr_pct(fees_7d, 7, tvl_usd)

    if len(completed) >= 2:
        prev_fees = float(completed[1]["feesUSD"])
        if fees_24h > prev_fees:
            result["income_change_direction"] = "up"
        elif fees_24h < prev_fees:
            result["income_change_direction"] = "down"
        else:
            result["income_change_direction"] = "flat"

    return result


def chain_name_for(config: dict, chain_id: int) -> str:
    for chain in config["chains"]:
        if chain["chain_id"] == chain_id:
            return chain["chain_name"]
    for ref in config.get("special_pool_references", []):
        if ref["chain_id"] == chain_id:
            return ref["chain_name"]
    return f"chain-{chain_id}"
