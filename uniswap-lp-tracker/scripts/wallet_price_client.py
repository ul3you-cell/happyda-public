#!/usr/bin/env python3
"""唯讀 USD 報價：Alchemy 合約地址查價；缺價才用 CoinGecko 的已核對合約映射。

不推算未知代幣、不把失敗當 0。ALCHEMY_API_KEY 只從環境變數讀，
永不印出含金鑰的 URL 或 HTTP 回應內容。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

PRICES_ENDPOINT_TEMPLATE = "https://api.g.alchemy.com/prices/v1/{api_key}/tokens/by-address"
COINGECKO_ENDPOINT = "https://api.coingecko.com/api/v3/simple/price"
# 僅限已核對的部署位址；WETH = ETH 的包裝幣，WBTC 取其自身而非 BTC 報價。
COINGECKO_IDS_BY_CONTRACT = {
    ("eth-mainnet", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): "ethereum",
    ("eth-mainnet", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): "usd-coin",
    ("arb-mainnet", "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f"): "wrapped-bitcoin",
    ("arb-mainnet", "0xaf88d065e77c8cc2239327c5edb3a432268e5831"): "usd-coin",
}
MAX_COINGECKO_AGE_SECONDS = 600


class PriceClientError(RuntimeError):
    pass


def _redact_url(url: str) -> str:
    m = re.match(r"^(https?://[^/]+/prices/v1/)", url)
    return (m.group(1) + "…（key 與其餘路徑已遮蔽）") if m else "（URL 已完全遮蔽）"


HttpPost = Callable[[str, dict], dict]
HttpGet = Callable[[str], dict]


def _default_http_post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Provider error bodies may reflect the request URL or credential; never print the body.
        raise PriceClientError(f"Alchemy Prices API HTTP {exc.code}（{_redact_url(url)}）") from exc
    except urllib.error.URLError as exc:
        raise PriceClientError(f"Alchemy Prices API 連線失敗（{_redact_url(url)}）") from exc


def _default_http_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "uniswap-lp-tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PriceClientError(f"CoinGecko HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PriceClientError("CoinGecko 連線失敗") from exc


def _positive_price(value: object) -> float | None:
    try:
        price = float(value) if isinstance(value, (int, float, str)) else None
        if price is None:
            return None
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def fetch_usd_prices_by_address(
    network_address_pairs: list[tuple[str, str]],
    api_key: Optional[str] = None,
    http_post: Optional[HttpPost] = None,
    *,
    http_get: Optional[HttpGet] = None,
    sources: dict[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str], float | None]:
    """回傳 {(network, lower-case address): USD 報價或 None}。

    先查 Alchemy，僅對缺價且合約在白名單的幣查 CoinGecko。
    若主要來源失敗、備援也失敗，拋出 PriceClientError 供上游標 N/A。
    sources 若提供，僅記下實際成功取得價格的供應商，不寫入憑證。
    """
    pairs = list(dict.fromkeys((net, addr.lower()) for net, addr in network_address_pairs))
    if not pairs:
        return {}
    result: dict[tuple[str, str], float | None] = {pair: None for pair in pairs}
    key = api_key if api_key is not None else os.environ.get("ALCHEMY_API_KEY", "").strip()
    primary_failed = False
    if key:
        try:
            response = (http_post or _default_http_post)(
                PRICES_ENDPOINT_TEMPLATE.format(api_key=key),
                {"addresses": [{"network": net, "address": addr} for net, addr in pairs]},
            )
            for item in response.get("data", []):
                pair = (item.get("network"), (item.get("address") or "").lower())
                if pair not in result or item.get("error"):
                    continue
                quotes = item.get("prices") or []
                price = next((_positive_price(q.get("value")) for q in quotes
                              if str(q.get("currency", "")).lower() == "usd"), None)
                if price is not None:
                    result[pair] = price
                    if sources is not None:
                        sources[pair] = "Alchemy Prices API"
        except (PriceClientError, ValueError, TypeError, KeyError) as exc:
            primary_failed = True
    else:
        primary_failed = True

    missing = {pair: COINGECKO_IDS_BY_CONTRACT[pair]
               for pair in pairs if result[pair] is None and pair in COINGECKO_IDS_BY_CONTRACT}
    if missing:
        url = COINGECKO_ENDPOINT + "?" + urllib.parse.urlencode({
            "ids": ",".join(sorted(set(missing.values()))),
            "vs_currencies": "usd", "include_last_updated_at": "true",
        })
        try:
            response = (http_get or _default_http_get)(url)
            now = time.time()
            for pair, coin_id in missing.items():
                quote = response.get(coin_id) or {}
                updated = quote.get("last_updated_at")
                price = _positive_price(quote.get("usd"))
                # Without a recent quote, use N/A rather than present stale data as current.
                if price is None or not isinstance(updated, (int, float)):
                    continue
                if not 0 <= now - updated <= MAX_COINGECKO_AGE_SECONDS:
                    continue
                result[pair] = price
                if sources is not None:
                    sources[pair] = "CoinGecko (合約映射)"
        except (PriceClientError, ValueError, TypeError, KeyError):
            if primary_failed:
                raise PriceClientError("Alchemy 不可用且 CoinGecko 備援查價失敗（未換算 USD）") from None
    elif primary_failed and not any(price is not None for price in result.values()):
        raise PriceClientError("Alchemy 不可用且沒有已核對的 CoinGecko 合約映射（未換算 USD）")
    return result
