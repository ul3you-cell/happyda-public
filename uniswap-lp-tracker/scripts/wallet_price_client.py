#!/usr/bin/env python3
"""唯讀查詢 Alchemy Prices API（by-address），取得 token 目前 USD 價格。

端點與請求格式逐字核對自 alchemy.com/docs/reference/prices-api-quickstart
（2026-09-27 抓取，見 handoff.md）：
    POST https://api.g.alchemy.com/prices/v1/{apiKey}/tokens/by-address
    body: {"addresses": [{"network": "eth-mainnet", "address": "0x..."}]}

安全規則：跟 wallet_rpc_client.py 同一套——ALCHEMY_API_KEY 只從環境變數讀，
絕不印出/log 完整 URL；錯誤訊息一律先遮蔽 key。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable, Optional

PRICES_ENDPOINT_TEMPLATE = "https://api.g.alchemy.com/prices/v1/{api_key}/tokens/by-address"


class PriceClientError(RuntimeError):
    pass


def _redact_url(url: str) -> str:
    m = re.match(r"^(https?://[^/]+/prices/v1/)", url)
    return (m.group(1) + "…（key 與其餘路徑已遮蔽）") if m else "（URL 已完全遮蔽）"


HttpPost = Callable[[str, dict], dict]


def _default_http_post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PriceClientError(
            f"Alchemy Prices API HTTP {exc.code}（{_redact_url(url)}）：{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise PriceClientError(f"Alchemy Prices API 連線失敗（{_redact_url(url)}）：{exc}") from exc


def fetch_usd_prices_by_address(
    network_address_pairs: list[tuple[str, str]],
    api_key: Optional[str] = None,
    http_post: Optional[HttpPost] = None,
) -> dict[tuple[str, str], float | None]:
    """輸入 [(alchemy_network_slug, token_address), ...]，回傳
    {(network, address_lower): usd_price 或 None（查無/該幣沒有 USD 報價）}。
    任何單一 token 的 error 欄位不會讓整批呼叫失敗，只讓那一個 token 回 None。"""
    key = api_key if api_key is not None else os.environ.get("ALCHEMY_API_KEY", "").strip()
    if not key:
        raise PriceClientError("缺少 ALCHEMY_API_KEY 環境變數，無法查詢 USD 價格。")
    if not network_address_pairs:
        return {}

    poster = http_post or _default_http_post
    url = PRICES_ENDPOINT_TEMPLATE.format(api_key=key)
    addresses = [{"network": net, "address": addr} for net, addr in network_address_pairs]
    response = poster(url, {"addresses": addresses})

    result: dict[tuple[str, str], float | None] = {}
    for item in response.get("data", []):
        net = item.get("network")
        addr = (item.get("address") or "").lower()
        if item.get("error"):
            result[(net, addr)] = None
            continue
        prices = item.get("prices") or []
        usd = next((p.get("value") for p in prices if p.get("currency") == "USD"), None)
        result[(net, addr)] = float(usd) if usd is not None else None
    return result
