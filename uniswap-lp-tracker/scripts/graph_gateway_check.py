#!/usr/bin/env python3
"""唯讀 smoke test：驗證 GRAPH_API_KEY 是否為一把「真的能打到 The Graph
gateway、查到 Uniswap v3 官方 subgraph」的有效 key。

背景：anne 查證 The Graph 官方文件沒有公開固定 key 格式/長度規則，長度檢查
（見 common.py 的 graph_api_key_length_warning）只能抓「明顯截斷」這種輸入
錯誤，不能證明 key 有效。唯一可靠的驗證方式是實際打一次唯讀 query。

安全規則（硬性，比照 fetch_pool_info.py）：
- key 只從環境變數 GRAPH_API_KEY 讀取（透過 common.require_graph_api_key()），
  絕不 print/log key 本身；HTTP 錯誤訊息輸出前一律先遮蔽 URL 內的 key 片段。
- 只送一個最小的唯讀 introspection 查詢（_meta { block { number } }），不查詢
  任何錢包位址、不寫入任何資料。
- 部署 ID 逐字抄自官方文件 developers.uniswap.org/docs/ecosystem/subgraphs/overview
  （2026-09-26 由 @research 交付並附抓取紀錄，見
  workspace/research/drafts/uniswap_wallet_data_sources.md）。

用法：
    KEY="$(security find-generic-password -a GRAPH_API_KEY -s uniswap-tracker -w)" \\
      GRAPH_API_KEY="$KEY" python3 scripts/graph_gateway_check.py
    unset KEY

輸出：只印「成功/失敗＋HTTP 狀態碼＋官方回應的區塊高度或錯誤訊息」，不印 key。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from common import require_graph_api_key

GATEWAY_BASE = "https://gateway.thegraph.com/api"

# 逐字抄自 developers.uniswap.org/docs/ecosystem/subgraphs/overview
# （2026-09-26 抓取，見 research 交付的 uniswap_wallet_data_sources.md §1.A）。
# 官方原文警告：「Explorer links and endpoints in this page are examples of
# public deployments. They are not official deployments...」——這裡選 v3
# Mainnet 只是拿來做「key 是否有效」的 smoke test，不是本專案的正式資料源
# （v3 subgraph 沒有 wallet position entity，見同一份交付文件 §1.C）。
V3_MAINNET_DEPLOYMENT_ID = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

MIN_QUERY = json.dumps({"query": "{ _meta { block { number } } }"})

# 明確、固定的 User-Agent + Accept：anne 回報使用者實測 HTTP 403 / Cloudflare
# error 1010，懷疑是 Cloudflare 依用戶端特徵（例如 Python 預設 UA 字串
# "Python-urllib/3.x"）擋掉，跟 key 是否有效無關。這裡固定送出一個明確、
# 不冒充瀏覽器的 UA，讓請求身分透明、可重現，也方便之後在 The Graph /
# Cloudflare 側對照 log。
REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "uniswap-lp-tracker-gateway-check/1.0 (+https://github.com/ul3you-cell/happyda-public)",
}


def _redact_key_in_text(text: str, api_key: str) -> str:
    """輸出任何字串前，先確保 key 本身不會出現（防禦性二次遮蔽，即使呼叫端
    忘記處理，也不會在 traceback / URL echo 裡洩漏）。"""
    if not api_key:
        return text
    return text.replace(api_key, "***REDACTED***")


def check_gateway(api_key: str, deployment_id: str = V3_MAINNET_DEPLOYMENT_ID) -> tuple[bool, str]:
    """回傳 (成功?, 訊息)。訊息保證不含 api_key 本身。"""
    url = f"{GATEWAY_BASE}/{api_key}/subgraphs/id/{deployment_id}"
    req = urllib.request.Request(
        url,
        data=MIN_QUERY.encode("utf-8"),
        headers=REQUEST_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        # CF-RAY 是 Cloudflare 的公開請求識別碼（不含任何憑證資訊），WAF 層擋
        # 請求時貼出來能讓對方（或我們自己）用它查該次請求的處理紀錄；只在
        # 判斷「這是網路/WAF 層擋，不是 key 本身無效」時才用得到。
        cf_ray = e.headers.get("CF-RAY") if e.headers else None
        cf_note = f"（CF-RAY={cf_ray}）" if cf_ray else ""
        return False, _redact_key_in_text(f"HTTP {e.code}{cf_note}：{raw[:300]}", api_key)
    except urllib.error.URLError as e:
        return False, _redact_key_in_text(f"連線失敗：{e.reason}", api_key)
    except json.JSONDecodeError:
        return False, "gateway 回應不是合法 JSON（可能是 HTML 錯誤頁）"

    if "errors" in body:
        return False, f"gateway 回錯誤：{json.dumps(body['errors'], ensure_ascii=False)[:300]}"
    block_number = (
        body.get("data", {}).get("_meta", {}).get("block", {}).get("number")
    )
    if block_number is None:
        return False, f"回應缺少預期欄位 data._meta.block.number：{json.dumps(body, ensure_ascii=False)[:300]}"
    return True, f"gateway 唯讀查詢成功，v3 mainnet subgraph 最新索引區塊高度={block_number}"


def main() -> int:
    api_key = require_graph_api_key()
    ok, message = check_gateway(api_key)
    if ok:
        print(f"OK：{message}")
        return 0
    print(f"FAIL：{message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
