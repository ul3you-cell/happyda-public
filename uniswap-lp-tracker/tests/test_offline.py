"""離線測試：正規化 schema、token 白名單、fee tier 換算、APR 公式。
不呼叫任何網路 / API，不需要 UNISWAP_API_KEY。

執行：
    cd uniswap-lp-tracker/scripts && python3 -m unittest discover -s ../tests -v
或直接：
    python3 tests/test_offline.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402
import normalize  # noqa: E402


class TestFeeTierConversion(unittest.TestCase):
    def test_known_fee_values(self):
        self.assertEqual(common.fee_raw_to_pct(3000), 0.3)
        self.assertEqual(common.fee_raw_to_pct(500), 0.05)
        self.assertEqual(common.fee_raw_to_pct(10000), 1.0)
        self.assertEqual(common.fee_raw_to_pct(100), 0.01)

    def test_none_is_none(self):
        self.assertIsNone(common.fee_raw_to_pct(None))


class TestFeeAprFormula(unittest.TestCase):
    def test_basic_annualization(self):
        # $1000 fees over 24h on $1,000,000 TVL -> 36.5%/yr
        apr = common.fee_apr_pct(fees_usd_window=1000, window_days=1, tvl_usd=1_000_000)
        self.assertAlmostEqual(apr, 36.5, places=2)

    def test_seven_day_window(self):
        # $7000 fees over 7 days on $1,000,000 TVL -> same daily rate -> 36.5%/yr
        apr = common.fee_apr_pct(fees_usd_window=7000, window_days=7, tvl_usd=1_000_000)
        self.assertAlmostEqual(apr, 36.5, places=2)

    def test_zero_tvl_returns_none(self):
        self.assertIsNone(common.fee_apr_pct(1000, 1, 0))
        self.assertIsNone(common.fee_apr_pct(1000, 1, None))

    def test_missing_fees_returns_none(self):
        self.assertIsNone(common.fee_apr_pct(None, 1, 1_000_000))


class TestTokenWhitelist(unittest.TestCase):
    def setUp(self):
        self.config = common.load_config()
        self.whitelist = common.build_token_whitelist(self.config)

    def test_known_ethereum_tokens_recognized(self):
        usdc = common.lookup_token(self.whitelist, 1, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
        weth = common.lookup_token(self.whitelist, 1, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
        self.assertIsNotNone(usdc)
        self.assertEqual(usdc["symbol"], "USDC")
        self.assertIsNotNone(weth)
        self.assertEqual(weth["symbol"], "WETH")

    def test_case_insensitive_lookup(self):
        usdc_lower = common.lookup_token(self.whitelist, 1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
        self.assertIsNotNone(usdc_lower)

    def test_unknown_address_rejected(self):
        fake = common.lookup_token(self.whitelist, 1, "0x000000000000000000000000000000000dead00")
        self.assertIsNone(fake)

    def test_cross_chain_isolation(self):
        # Ethereum USDC address must NOT resolve on a different chain id
        usdc_on_arbitrum = common.lookup_token(self.whitelist, 42161, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
        self.assertIsNone(usdc_on_arbitrum)


class TestNormalizeSchema(unittest.TestCase):
    def setUp(self):
        self.config = common.load_config()
        self.whitelist = common.build_token_whitelist(self.config)
        fixture_path = common.FIXTURES_DIR / "eth-weth-usdc-v3-030.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.fixture = json.load(f)

    def test_fixture_row_schema_and_values(self):
        pool_obj = self.fixture["pools"][0]
        row = normalize.pool_obj_to_row(
            pool_obj, source_label="test-fixture", snapshot_time=None,
            whitelist=self.whitelist, config=self.config,
        )
        required_keys = {
            "id", "status", "chain_id", "chain_name", "protocol", "pool_address",
            "token_a_symbol", "token_b_symbol", "pair_label", "fee_tier_raw", "fee_tier_pct",
            "current_tick", "pool_liquidity_raw", "tvl_usd", "tvl_usd_note",
            "volume_24h_usd", "volume_note", "fee_apr_24h_pct", "fee_apr_note",
            "income_change_direction", "income_change_note", "wallet_position_note",
            "snapshot_time", "source", "limitations",
        }
        self.assertTrue(required_keys.issubset(row.keys()))
        self.assertEqual(row["status"], "live")
        self.assertEqual(row["token_a_symbol"], "USDC")
        self.assertEqual(row["token_b_symbol"], "WETH")
        self.assertEqual(row["pair_label"], "USDC/WETH")
        self.assertEqual(row["fee_tier_raw"], 3000)
        self.assertEqual(row["fee_tier_pct"], 0.3)
        self.assertEqual(row["current_tick"], 197356)
        self.assertEqual(row["pool_liquidity_raw"], "3874818863318575310")
        # 沒有官方資料的欄位必須是 None，不可造數
        self.assertIsNone(row["tvl_usd"])
        self.assertIsNone(row["volume_24h_usd"])
        self.assertIsNone(row["volume_7d_usd"])
        self.assertIsNone(row["fee_apr_24h_pct"])
        self.assertIsNone(row["fee_apr_7d_pct"])
        self.assertIsNone(row["income_change_direction"])
        self.assertTrue(row["tvl_usd_note"])
        self.assertTrue(row["wallet_position_note"])

    def test_untrusted_token_is_flagged_and_neutered(self):
        fake_pool = {
            "poolReferenceIdentifier": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "poolProtocol": "V3",
            "tokenAddressA": "0x0000000000000000000000000000000000dead0",
            "tokenAddressB": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "fee": 3000,
            "chainId": 1,
            "poolLiquidity": "999999999999999999999",
            "currentTick": 1,
        }
        row = normalize.pool_obj_to_row(
            fake_pool, source_label="test-fake", snapshot_time=None,
            whitelist=self.whitelist, config=self.config,
        )
        self.assertEqual(row["status"], "untrusted_token")
        self.assertTrue(row["limitations"])


class TestNormalizeFullRun(unittest.TestCase):
    """對 normalize.main() 的完整輸出做 sanity check（讀真實 data/normalized_latest.json，
    需先跑過 scripts/normalize.py 至少一次；若尚未產生則此測試會自動先產生一份）。"""

    def test_row_count_and_meta_consistent(self):
        out_path = common.DATA_DIR / "normalized_latest.json"
        if not out_path.exists():
            normalize.main()
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data["rows"]
        meta = data["meta"]
        self.assertEqual(len(rows), meta["total_rows"])
        status_counts = {}
        for r in rows:
            status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        self.assertEqual(status_counts.get("live", 0), meta["live_rows"])
        self.assertEqual(status_counts.get("fixture", 0), meta["fixture_rows"])
        self.assertEqual(status_counts.get("pending", 0), meta["pending_rows"])
        # 至少要有一筆 live 或 fixture（本次以 anne fixture 為唯一真實資料來源）
        self.assertGreaterEqual(meta["live_rows"] + meta["fixture_rows"], 1)
        # 沒有偽造：任一 row 若 status 是 pending，數值欄位必須全是 None
        for r in rows:
            if r["status"] == "pending":
                self.assertIsNone(r["tvl_usd"])
                self.assertIsNone(r["pool_liquidity_raw"])
                self.assertIsNone(r["current_tick"])


if __name__ == "__main__":
    unittest.main()
