"""Alchemy/CoinGecko LP USD 報價：完全離線，不接觸憑證。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import wallet_price_client as prices
import wallet_live_fetch

ETH_WETH = ("eth-mainnet", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")
ETH_USDC = ("eth-mainnet", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
ARB_WBTC = ("arb-mainnet", "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f")
ARB_USDC = ("arb-mainnet", "0xaf88d065e77c8cc2239327c5edb3a432268e5831")
UNKNOWN = ("eth-mainnet", "0x" + "f" * 40)
PAIRS = [ETH_WETH, ETH_USDC, ARB_WBTC, ARB_USDC]


class WalletPriceFallbackTest(unittest.TestCase):
    def test_alchemy_lowercase_usd_is_valid_and_does_not_call_fallback(self):
        def primary(_url, payload):
            return {"data": [{**entry, "prices": [{"currency": "usd", "value": "2381.83"}]}
                             for entry in payload["addresses"]]}
        fallback = mock.Mock(side_effect=AssertionError("primary result needs no fallback"))
        sources = {}
        result = prices.fetch_usd_prices_by_address(
            PAIRS, api_key="synthetic-key", http_post=primary,
            http_get=fallback, sources=sources)
        self.assertEqual(result[ETH_WETH], 2381.83)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(s == "Alchemy Prices API" for s in sources.values()))
        fallback.assert_not_called()

    def test_only_missing_quote_uses_verified_contract_mapping(self):
        def primary(_url, payload):
            return {"data": [{**entry, "prices": [{"currency": "usd", "value": "2400"}]}
                             for entry in payload["addresses"] if entry["address"] == ETH_WETH[1]]}
        fallback = mock.Mock(return_value={
            "usd-coin": {"usd": 0.9999, "last_updated_at": 1000},
            "wrapped-bitcoin": {"usd": 81000, "last_updated_at": 1000},
        })
        with mock.patch("wallet_price_client.time.time", return_value=1001):
            sources = {}
            result = prices.fetch_usd_prices_by_address(
                PAIRS, api_key="synthetic-key", http_post=primary,
                http_get=fallback, sources=sources)
        self.assertEqual(result[ETH_WETH], 2400)
        self.assertEqual(result[ETH_USDC], 0.9999)
        self.assertEqual(result[ARB_WBTC], 81000)
        self.assertEqual(result[ARB_USDC], 0.9999)
        self.assertEqual(sources[ETH_WETH], "Alchemy Prices API")
        self.assertEqual(sources[ARB_WBTC], "CoinGecko (合約映射)")
        self.assertEqual(fallback.call_count, 1)
        self.assertNotIn("ethereum", fallback.call_args.args[0])

    def test_missing_key_or_primary_outage_uses_coingecko(self):
        fallback = mock.Mock(return_value={"ethereum": {"usd": 2400, "last_updated_at": 1000}})
        with mock.patch("wallet_price_client.time.time", return_value=1001):
            with mock.patch.dict("wallet_price_client.os.environ", {"ALCHEMY_API_KEY": ""}):
                result = prices.fetch_usd_prices_by_address(
                    [ETH_WETH], http_get=fallback)
        self.assertEqual(result[ETH_WETH], 2400)
        def broken(_url, _payload):
            raise prices.PriceClientError("provider unavailable")
        with mock.patch("wallet_price_client.time.time", return_value=1001):
            result = prices.fetch_usd_prices_by_address(
                [ETH_WETH], api_key="synthetic-key", http_post=broken, http_get=fallback)
        self.assertEqual(result[ETH_WETH], 2400)

    def test_unknown_or_stale_quote_remains_missing(self):
        def broken(_url, _payload):
            raise prices.PriceClientError("provider unavailable")
        fallback = mock.Mock(return_value={"ethereum": {"usd": 2400, "last_updated_at": 1}})
        with mock.patch("wallet_price_client.time.time", return_value=10000):
            result = prices.fetch_usd_prices_by_address(
                [ETH_WETH, UNKNOWN], api_key="synthetic-key", http_post=broken, http_get=fallback)
        self.assertIsNone(result[ETH_WETH])
        self.assertIsNone(result[UNKNOWN])

    def test_both_providers_fail_does_not_silently_return_zero(self):
        def broken(_url, _payload):
            raise prices.PriceClientError("provider unavailable")
        with self.assertRaises(prices.PriceClientError):
            prices.fetch_usd_prices_by_address(
                [ETH_WETH], api_key="synthetic-key", http_post=broken,
                http_get=mock.Mock(side_effect=prices.PriceClientError("rate limited")))


class WalletValuationIntegrationTest(unittest.TestCase):
    def test_two_active_positions_value_and_claimable_amount_have_price_sources(self):
        positions = [
            {"token_id": 429477, "active": True, "liquidity": 1,
             "token0": {"address": "0x" + "0" * 40, "symbol": "ETH", "decimals": 18},
             "token1": {"address": ETH_USDC[1], "symbol": "USDC", "decimals": 6},
             "token0_amount": 1.5, "token1_amount": 2000,
             "fees_owed_0_raw": str(10**16), "fees_owed_1_raw": str(10**7)},
            {"token_id": 212750, "active": True, "liquidity": 1,
             "token0": {"address": ARB_WBTC[1], "symbol": "WBTC", "decimals": 8},
             "token1": {"address": ARB_USDC[1], "symbol": "USDC", "decimals": 6},
             "token0_amount": 0.05, "token1_amount": 2800,
             "fees_owed_0_raw": str(10**4), "fees_owed_1_raw": str(10**7)},
        ]
        chains = [{"chain_id": 1, "chain_name": "Ethereum", "protocol": "v4",
                   "positions": [positions[0]]},
                  {"chain_id": 42161, "chain_name": "Arbitrum", "protocol": "v4",
                   "positions": [positions[1]]}]
        def fake_quotes(pairs, *, sources):
            self.assertEqual(set(pairs), set(PAIRS))
            sources.update({pair: "CoinGecko (合約映射)" for pair in pairs})
            return {ETH_WETH: 2400, ETH_USDC: 1, ARB_WBTC: 80000, ARB_USDC: 1}
        with mock.patch.object(prices, "fetch_usd_prices_by_address", side_effect=fake_quotes):
            wallet_live_fetch.enrich_v4_with_usd(chains)
        self.assertEqual(positions[0]["position_value_usd"], 5600)
        self.assertEqual(positions[1]["position_value_usd"], 6800)
        self.assertEqual(positions[0]["fees_owed_usd"], 34)
        self.assertEqual(positions[1]["fees_owed_usd"], 18)
        self.assertEqual(positions[0]["token0_usd_price_source"], "CoinGecko (合約映射)")
        self.assertEqual(positions[1]["token1_usd_price_source"], "CoinGecko (合約映射)")
        summary = wallet_live_fetch.summarize_active_portfolio(chains)
        self.assertTrue(summary["valuation_complete"])
        self.assertTrue(summary["fees_complete"])
        self.assertEqual(summary["total_value_usd"], 12400)
        self.assertEqual(summary["fees_owed_usd"], 52)


if __name__ == "__main__":
    unittest.main()
