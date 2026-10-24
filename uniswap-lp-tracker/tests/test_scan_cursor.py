"""共用掃描游標機制的離線測試：四個掃描家族共用同一套續掃規則。

不呼叫任何網路 / API。
執行：cd uniswap-lp-tracker && python3 -m unittest discover -s tests -p 'test_scan_cursor.py' -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import wallet_live_fetch  # noqa: E402
import wallet_rpc_client  # noqa: E402
import wallet_scan_cursor as cursor_store  # noqa: E402


WALLET = "0x267ee34200b09ea8b52d02eec3300b84985b1efd"


class TestPlanWindow(unittest.TestCase):
    """所有掃描家族共用的續掃判斷。"""

    def test_missing_cursor_falls_back_to_full_baseline(self):
        plan = cursor_store.plan_window(current_block=500, previous_block=None)
        self.assertEqual(plan, (0, 0, 0, False))

    def test_missing_base_falls_back_to_full_baseline(self):
        plan = cursor_store.plan_window(current_block=500, previous_block=100, base0=None, base1=None)
        self.assertEqual(plan, (0, 0, 0, False))

    def test_resumes_from_previous_block_plus_one(self):
        plan = cursor_store.plan_window(current_block=500, previous_block=100, base0=7, base1=9)
        self.assertEqual(plan, (101, 7, 9, False))

    def test_same_block_does_not_rescan(self):
        plan = cursor_store.plan_window(current_block=100, previous_block=100, base0=7, base1=9)
        self.assertEqual(plan, (101, 7, 9, True))

    def test_block_going_backwards_falls_back_to_full_baseline(self):
        plan = cursor_store.plan_window(current_block=90, previous_block=100, base0=7, base1=9)
        self.assertEqual(plan, (0, 0, 0, False))


class TestCursorStore(unittest.TestCase):
    """游標與已知部位集合的持久化。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cursor_store.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_cursor_roundtrip(self):
        cursor_store.write_cursor(
            self.conn, wallet_addr=WALLET, chain_id=42161, scope="v3_collect",
            scope_key="7", last_scanned_block=1234, total0_raw=10, total1_raw=20,
        )
        row = cursor_store.read_cursor(
            self.conn, wallet_addr=WALLET, chain_id=42161, scope="v3_collect", scope_key="7"
        )
        self.assertEqual(row["last_scanned_block"], 1234)
        self.assertEqual(row["total0_raw"], "10")
        self.assertEqual(row["total1_raw"], "20")

    def test_missing_cursor_reads_none(self):
        self.assertIsNone(cursor_store.read_cursor(
            self.conn, wallet_addr=WALLET, chain_id=1, scope="v4_activity", scope_key="9"
        ))

    def test_known_token_ids_accumulate(self):
        cursor_store.add_tokens(
            self.conn, wallet_addr=WALLET, chain_id=42161, scope="v4_enumeration",
            token_ids=[212750, 55626], first_seen_block=100,
        )
        cursor_store.add_tokens(
            self.conn, wallet_addr=WALLET, chain_id=42161, scope="v4_enumeration",
            token_ids=[212750], first_seen_block=200,
        )
        self.assertEqual(
            cursor_store.list_tokens(
                self.conn, wallet_addr=WALLET, chain_id=42161, scope="v4_enumeration"
            ),
            [55626, 212750],
        )


class TestV4EnumerationUsesSharedCursor(unittest.TestCase):
    """V4 持倉列舉必須走共用游標：只掃上次區塊之後的新事件。"""

    def test_enumeration_scans_only_after_cursor(self):
        calls = {}

        def fake_logs(rpc_url, wallet_addr, chain_id, http_post=None, from_block=0, known_token_ids=None):
            calls["from_block"] = from_block
            calls["known"] = list(known_token_ids or [])
            return {"token_ids": [212750], "scanned_to_block": 900, "discovered_token_ids": [212750],
                    "candidate_token_ids": [212750], "scan_error": None}

        class _Store:
            @staticmethod
            def get_connection():
                return sqlite3.connect(":memory:")

        original_store = wallet_live_fetch.store
        original_logs = wallet_rpc_client.list_wallet_v4_token_ids_via_logs
        wallet_live_fetch.store = _Store
        wallet_rpc_client.list_wallet_v4_token_ids_via_logs = fake_logs
        try:
            result = wallet_live_fetch._enumerate_v4_positions_incrementally(
                "https://fake-rpc.invalid", 42161, 900
            )
        finally:
            wallet_live_fetch.store = original_store
            wallet_rpc_client.list_wallet_v4_token_ids_via_logs = original_logs
        self.assertEqual(result["token_ids"], [212750])
        self.assertEqual(result["scanned_to_block"], 900)
        # 沒有游標、沒有可用的前一份結果 → 退回完整基準掃描（寧可慢不漏）
        self.assertEqual(calls["from_block"], 0)

    def test_v3_scan_returns_pending_cursor_instead_of_writing_early(self):
        """V3 事件掃描的游標必須等快照寫入成功才推進。"""
        def fake_scanner(url, token_id, *, chain_id, from_block, to_block):
            return {"amount0_raw": 3, "amount1_raw": 4, "log_count": 2}

        result = wallet_live_fetch._scan_v3_cumulative_events(
            rpc_url="https://fake-rpc.invalid",
            chain_id=42161,
            token_id=7,
            current_block=150,
            previous_row=None,
            total0_key="cumulative_claimed_0_raw",
            total1_key="cumulative_claimed_1_raw",
            scanner=fake_scanner,
            scope="v3_collect",
        )
        self.assertEqual(result["amount0_raw"], 3)
        self.assertEqual(result["amount1_raw"], 4)
        self.assertEqual(result["pending_cursor"]["scope"], "v3_collect")
        self.assertEqual(result["pending_cursor"]["last_scanned_block"], 150)


if __name__ == "__main__":
    unittest.main()
