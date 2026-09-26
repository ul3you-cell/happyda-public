"""離線測試：正規化 schema、token 白名單、fee tier 換算、APR 公式。
不呼叫任何網路 / API，不需要 UNISWAP_API_KEY。

執行：
    cd uniswap-lp-tracker/scripts && python3 -m unittest discover -s ../tests -v
或直接：
    python3 tests/test_offline.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402
import fetch_pool_info  # noqa: E402
import normalize  # noqa: E402
import run_daily_update  # noqa: E402


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


class TestGraphApiKeyFormatValidation(unittest.TestCase):
    """對應 @anne 要求：長度需為 64 hex 才放行，不是「非空就放行」。"""

    VALID_64_HEX = "a" * 64  # 合成測試值，不是任何真實 key
    VALID_64_HEX_MIXED_CASE = ("1234567890abcdefABCDEF" * 3)[:64]

    def test_none_and_empty_rejected(self):
        self.assertFalse(common.graph_api_key_format_ok(None))
        self.assertFalse(common.graph_api_key_format_ok(""))

    def test_truncated_12_chars_rejected(self):
        # 重現使用者實測遇到的情況：keychain 只存到前 12 碼
        self.assertFalse(common.graph_api_key_format_ok("a" * 12))

    def test_valid_64_hex_accepted(self):
        self.assertTrue(common.graph_api_key_format_ok(self.VALID_64_HEX))
        self.assertTrue(common.graph_api_key_format_ok(self.VALID_64_HEX_MIXED_CASE))

    def test_64_chars_but_non_hex_rejected(self):
        non_hex = ("z" * 64)
        self.assertFalse(common.graph_api_key_format_ok(non_hex))

    def test_whitespace_is_trimmed_before_check(self):
        self.assertTrue(common.graph_api_key_format_ok(f"  {self.VALID_64_HEX}\n"))

    def test_65_or_63_chars_rejected(self):
        self.assertFalse(common.graph_api_key_format_ok("a" * 65))
        self.assertFalse(common.graph_api_key_format_ok("a" * 63))

    def test_require_graph_api_key_exits_2_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(common.GRAPH_API_KEY_ENV, None)
            with self.assertRaises(SystemExit) as ctx:
                common.require_graph_api_key()
            self.assertEqual(ctx.exception.code, 2)

    def test_require_graph_api_key_exits_2_when_truncated(self):
        with mock.patch.dict(os.environ, {common.GRAPH_API_KEY_ENV: "a" * 12}):
            with self.assertRaises(SystemExit) as ctx:
                common.require_graph_api_key()
            self.assertEqual(ctx.exception.code, 2)

    def test_require_graph_api_key_returns_stripped_value_when_valid(self):
        with mock.patch.dict(os.environ, {common.GRAPH_API_KEY_ENV: f"  {self.VALID_64_HEX}  "}):
            result = common.require_graph_api_key()
            self.assertEqual(result, self.VALID_64_HEX)

    def test_error_message_never_prints_key_content(self):
        # 格式錯誤訊息只能印長度，不能把 key 內容印出來
        import contextlib
        import io

        truncated = "b" * 12
        with mock.patch.dict(os.environ, {common.GRAPH_API_KEY_ENV: truncated}):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit):
                    common.require_graph_api_key()
            stderr_text = buf.getvalue()
            self.assertNotIn(truncated, stderr_text)
            self.assertIn("長度為 12 碼", stderr_text)


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
        # 白名單外 token：所有會在儀表板顯示的原始數值欄位必須清空，
        # 不可讓「已排除數值顯示」只改狀態文字卻仍外洩 API 數字。
        for numeric_key in ("fee_tier_raw", "fee_tier_pct", "tick_spacing", "current_tick", "pool_liquidity_raw"):
            self.assertIsNone(row[numeric_key], f"{numeric_key} 應在 untrusted_token 時清空")
        # USD/volume/APR 本來就一律 None（沒有官方資料來源）
        self.assertIsNone(row["tvl_usd"])
        self.assertIsNone(row["volume_24h_usd"])
        self.assertIsNone(row["fee_apr_24h_pct"])


class TestEmptyResponseHandling(unittest.TestCase):
    """驗證 HTTP 200 但 pools:[] 的已完成查詢不會被誤判為 pending（t_bf8f4d3e review 第 3 點）。"""

    def setUp(self):
        self.config = common.load_config()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_data_dir = Path(self.tmpdir.name)
        self.patcher = mock.patch.object(normalize, "DATA_DIR", self.tmp_data_dir)
        self.patcher.start()

        # AAVE/USDC 3000 fee 在 chain 1 是 config 內真實會產生查詢的目標組合。
        latest_raw = {
            "fetched_at": "2026-09-26T00:00:00+00:00",
            "results": [
                {
                    "query_type": "poolParameters",
                    "chain_id": 1,
                    "chain_name": "Ethereum",
                    "protocol": "V3",
                    "base_symbol": "AAVE",
                    "quote_symbol": "USDC",
                    "fee": 3000,
                    "http_status": 200,
                    "response": {"pools": []},
                    "fetched_at": "2026-09-26T00:00:00+00:00",
                },
                {
                    "query_type": "poolParameters",
                    "chain_id": 1,
                    "chain_name": "Ethereum",
                    "protocol": "V3",
                    "base_symbol": "UNI",
                    "quote_symbol": "USDC",
                    "fee": 500,
                    "http_status": 404,
                    "response": "not found",
                    "fetched_at": "2026-09-26T00:00:00+00:00",
                },
            ],
        }
        with open(self.tmp_data_dir / "latest_raw.json", "w", encoding="utf-8") as f:
            json.dump(latest_raw, f)

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_http_200_empty_pools_is_empty_response_not_error(self):
        rows = normalize.load_no_pool_rows(self.config)
        by_pair = {(r["token_a_symbol"], r["token_b_symbol"]): r for r in rows}
        self.assertEqual(by_pair[("AAVE", "USDC")]["status"], "empty_response")
        self.assertEqual(by_pair[("UNI", "USDC")]["status"], "not_found")
        # 沒有偽造數值：即使 pending_note 描述「已查詢」，數值欄位仍須是 None
        self.assertIsNone(by_pair[("AAVE", "USDC")]["tvl_usd"])
        self.assertIsNone(by_pair[("AAVE", "USDC")]["pool_liquidity_raw"])

    def test_full_normalize_does_not_reintroduce_pending_for_queried_combo(self):
        exit_code = normalize.main()
        self.assertEqual(exit_code, 0)
        with open(self.tmp_data_dir / "normalized_latest.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        rows_for_aave_usdc_v3 = [
            r for r in data["rows"]
            if r["chain_id"] == 1 and r["protocol"] == "V3"
            and r["token_a_symbol"] == "AAVE" and r["token_b_symbol"] == "USDC"
            and r["fee_tier_raw"] == 3000
        ]
        # 同一組合（chain 1, V3, AAVE/USDC, fee 3000）只查過一次就只能出現一次，狀態必須是
        # empty_response，不可同時／改為出現一筆 status=pending 的重複列。
        # （config 對同一 base/quote/fee 還會產生 V4 的查詢，那是另一組未查過的組合，
        # 理應仍是 pending，不在本測試斷言範圍內。）
        self.assertEqual(len(rows_for_aave_usdc_v3), 1, rows_for_aave_usdc_v3)
        self.assertEqual(rows_for_aave_usdc_v3[0]["status"], "empty_response")
        self.assertGreaterEqual(data["meta"]["empty_response_rows"], 1)


class TestScopedGitCommit(unittest.TestCase):
    """在隔離的臨時 git repo 內驗證：工作目錄已有其他任務的 tracked 修改與
    untracked 檔案時，commit_and_maybe_push 產生的 commit 絕不能包含它們
    （t_bf8f4d3e review 第 1 點：不得把 obsidian-vault-second-brain 之類的
    既有異動一起提交）。不連網、不 push（push=False）。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.env = env
        self._git("init", "-q")
        self._git("checkout", "-q", "-b", "main")
        (self.repo / "other-task.html").write_text("original\n", encoding="utf-8")
        self._git("add", "other-task.html")
        self._git("commit", "-q", "-m", "seed")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _git(self, *args):
        import subprocess
        result = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, env=self.env)
        if result.returncode != 0:
            raise AssertionError(f"git {args} failed: {result.stderr}")
        return result

    def test_commit_excludes_unrelated_tracked_and_untracked_changes(self):
        # 模擬工作目錄已有其他任務的異動：既有 tracked 檔案被改過、且有未追蹤新檔。
        (self.repo / "other-task.html").write_text("modified by someone else\n", encoding="utf-8")
        (self.repo / "unrelated-untracked.txt").write_text("非本案\n", encoding="utf-8")

        # 本案的異動：新增 dashboard html + data 目錄。
        (self.repo / "uniswap-lp-tracker-20260926.html").write_text("<html>dashboard</html>", encoding="utf-8")
        data_dir = self.repo / "uniswap-lp-tracker" / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "normalized_latest.json").write_text("{}", encoding="utf-8")

        rc = run_daily_update.commit_and_maybe_push(
            self.repo,
            ["uniswap-lp-tracker-20260926.html", "uniswap-lp-tracker/data"],
            "data: test scoped commit",
            push=False,
        )
        self.assertEqual(rc, 0)

        show = self._git("show", "--stat", "--format=", "HEAD")
        changed_files = [line.split("|")[0].strip() for line in show.stdout.strip().splitlines() if "|" in line]
        self.assertIn("uniswap-lp-tracker-20260926.html", changed_files)
        self.assertTrue(any("normalized_latest.json" in f for f in changed_files))
        self.assertNotIn("other-task.html", changed_files)
        self.assertFalse(any("unrelated-untracked" in f for f in changed_files))

        # 其他任務的 tracked 修改與 untracked 檔案必須仍留在工作目錄未被提交（still dirty）。
        status = self._git("status", "--porcelain")
        self.assertIn("other-task.html", status.stdout)
        self.assertIn("unrelated-untracked.txt", status.stdout)

    def test_no_dashboard_change_skips_commit_without_touching_unrelated_dirty_files(self):
        (self.repo / "other-task.html").write_text("modified by someone else\n", encoding="utf-8")
        head_before = self._git("rev-parse", "HEAD").stdout.strip()

        rc = run_daily_update.commit_and_maybe_push(
            self.repo,
            ["uniswap-lp-tracker-20260926.html", "uniswap-lp-tracker/data"],
            "data: should be skipped",
            push=False,
        )
        self.assertEqual(rc, 0)
        head_after = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head_before, head_after, "沒有本案 pathspec 的變動時不該產生新 commit")


class TestTelegramExitPropagation(unittest.TestCase):
    """驗證未設定 TELEGRAM_BOT_TOKEN/CHAT_ID 時，run_telegram_notification 會
    回傳 notify_telegram.py 的非 0 exit code（不吞掉），且不連網
    （t_bf8f4d3e review 第 5 點）。"""

    def test_missing_telegram_env_propagates_nonzero_exit_without_network(self):
        env = os.environ.copy()
        env.pop("TELEGRAM_BOT_TOKEN", None)
        env.pop("TELEGRAM_CHAT_ID", None)
        with mock.patch.dict(os.environ, env, clear=True):
            rc = run_daily_update.run_telegram_notification(SCRIPTS_DIR)
        self.assertEqual(rc, 3, "notify_telegram.py 未設定憑證時應以 exit 3 表示 SKIP／未送達")


class TestPoolReferenceRequestSchema(unittest.TestCase):
    """驗證 special_pool_references（Unichain 目標池）送出的請求 body 用官方
    schema 的 poolReferences[0].referenceIdentifier，不是
    poolReferenceIdentifier；且該元素內還要帶巢狀 chainId（anne 實測 2 輪：
    第一輪欄位名錯誤被伺服器視為空值、回 400 invalid_argument；改正欄位名
    後第二輪伺服器回應明確指出 poolReferences[0].chainId must be one of ...
    130 ...，代表巢狀 chainId 也是必填，不能只靠頂層 chainId）。見
    fetch_pool_info.py 修正註解。"""

    def test_pool_reference_body_uses_referenceIdentifier_key(self):
        config = common.load_config()
        queries = fetch_pool_info.build_queries(config)
        ref_queries = [q for q in queries if q["query_type"] == "poolReference"]
        self.assertTrue(ref_queries, "config 應至少有一筆 special_pool_references 目標")
        for q in ref_queries:
            refs = q["body"]["poolReferences"]
            self.assertEqual(len(refs), 1)
            self.assertIn("referenceIdentifier", refs[0])
            self.assertNotIn("poolReferenceIdentifier", refs[0])
            self.assertEqual(refs[0]["referenceIdentifier"], q["pool_reference_identifier"])
            self.assertTrue(refs[0]["referenceIdentifier"])

    def test_pool_reference_body_includes_nested_chain_id(self):
        """anne 第二輪 changes_requested：poolReferences[0] 仍缺巢狀 chainId，
        官方 400 訊息點名 poolReferences[0].chainId must be one of ... 130 ...
        （不是只靠頂層 body["chainId"]）。"""
        config = common.load_config()
        queries = fetch_pool_info.build_queries(config)
        ref_queries = [q for q in queries if q["query_type"] == "poolReference"]
        self.assertTrue(ref_queries, "config 應至少有一筆 special_pool_references 目標")
        for q in ref_queries:
            refs = q["body"]["poolReferences"]
            self.assertEqual(len(refs), 1)
            self.assertIn("chainId", refs[0], "poolReferences[0] 必須帶巢狀 chainId，不能只依賴頂層 chainId")
            self.assertEqual(refs[0]["chainId"], q["body"]["chainId"])
            self.assertEqual(refs[0]["chainId"], q["chain_id"])


class TestCoveredKeysIgnoreTokenOrder(unittest.TestCase):
    """驗證官方回應把 tokenAddressA/B 順序反過來（例如請求 WETH/USDC，回應
    是 USDC/WETH）時，covered_keys() 仍能跟 build_pending_rows() 的請求
    base/quote key 對上，不會把已查到的池位誤判成 pending（t_bf8f4d3e
    review：anne 實跑 230 筆全部已回應，normalized 卻仍有 61 筆卡在
    pending，根因是 token 順序未正規化）。"""

    def setUp(self):
        self.config = common.load_config()

    def test_reversed_token_order_row_is_recognized_as_covered(self):
        # 模擬請求 base=WETH quote=USDC，但官方回應 tokenAddressA/B 剛好反過來。
        row = {
            "chain_id": 1,
            "protocol": "V3",
            "token_a_symbol": "USDC",
            "token_b_symbol": "WETH",
            "fee_tier_raw": 3000,
            "status": "live",
        }
        covered = normalize.covered_keys([row])

        matching_query = None
        for q in fetch_pool_info.build_queries(self.config):
            if (
                q.get("query_type") == "poolParameters"
                and q["chain_id"] == 1
                and q["protocol"] == "V3"
                and q["base_symbol"] == "WETH"
                and q["quote_symbol"] == "USDC"
                and q["fee"] == 3000
            ):
                matching_query = q
                break
        self.assertIsNotNone(matching_query, "config 應含 chain1/V3/WETH-USDC/3000 這組查詢")
        self.assertIn(normalize._pending_key(matching_query), covered)

    def test_full_normalize_reversed_order_does_not_create_pending_duplicate(self):
        # 用 UNI/USDC（chain 1, fee 3000）而非 WETH/USDC，避免跟固定存在的
        # anne 離線 fixture（本身就是 chain1/V3/WETH-USDC/3000）撞同一組合，
        # 干擾「只應有一筆」的斷言。
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data_dir = Path(tmp)
            with mock.patch.object(normalize, "DATA_DIR", tmp_data_dir):
                latest_raw = {
                    "fetched_at": "2026-09-26T00:00:00+00:00",
                    "results": [
                        {
                            "query_type": "poolParameters",
                            "chain_id": 1,
                            "chain_name": "Ethereum",
                            "protocol": "V3",
                            "base_symbol": "UNI",
                            "quote_symbol": "USDC",
                            "fee": 3000,
                            "http_status": 200,
                            "response": {
                                "pools": [{
                                    "poolReferenceIdentifier": "0xreversedorder",
                                    "poolProtocol": "V3",
                                    # 故意跟請求的 base/quote 順序相反。
                                    "tokenAddressA": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
                                    "tokenAddressB": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",  # UNI
                                    "fee": 3000,
                                    "chainId": 1,
                                    "poolLiquidity": "123",
                                    "currentTick": 1,
                                }],
                            },
                            "fetched_at": "2026-09-26T00:00:00+00:00",
                        },
                    ],
                }
                with open(tmp_data_dir / "latest_raw.json", "w", encoding="utf-8") as f:
                    json.dump(latest_raw, f)

                exit_code = normalize.main()
                self.assertEqual(exit_code, 0)
                with open(tmp_data_dir / "normalized_latest.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

        rows_for_pair = [
            r for r in data["rows"]
            if r["chain_id"] == 1 and r["protocol"] == "V3" and r["fee_tier_raw"] == 3000
            and {r["token_a_symbol"], r["token_b_symbol"]} == {"UNI", "USDC"}
        ]
        # 已回應（live）的這組合，不能同時又跑出一筆 pending 的重複列。
        self.assertEqual(len(rows_for_pair), 1, rows_for_pair)
        self.assertEqual(rows_for_pair[0]["status"], "live")


class TestFixtureDedupedAgainstLive(unittest.TestCase):
    """驗證同一顆池若已經有正式 live 回應，離線 fixture 不會再重複列出同一池
    （t_bf8f4d3e review 第三輪：anne 的正式資料裡，Ethereum WETH/USDC V3 0.30%
    fixture 池位地址 0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8 跟正式 API 回應
    同一顆池同時出現在 rows，把 total_rows 撐大且重複計數一顆池）。"""

    def setUp(self):
        self.config = common.load_config()
        self.whitelist = common.build_token_whitelist(self.config)
        fixture_path = common.FIXTURES_DIR / "eth-weth-usdc-v3-030.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.fixture_payload = json.load(f)
        self.fixture_pool_obj = self.fixture_payload["pools"][0]

    def test_dedupe_drops_fixture_row_matching_live_pool_address(self):
        fixture_row = normalize.pool_obj_to_row(
            self.fixture_pool_obj, source_label="offline fixture", snapshot_time=None,
            whitelist=self.whitelist, config=self.config,
        )
        fixture_row["status"] = "fixture"
        # 模擬正式 API 回應同一顆池（同 chain/protocol/pool_address），是 live 狀態。
        live_row = normalize.pool_obj_to_row(
            self.fixture_pool_obj, source_label="live", snapshot_time="2026-09-26T07:40:00+00:00",
            whitelist=self.whitelist, config=self.config,
        )
        self.assertEqual(live_row["status"], "live")

        deduped = normalize.dedupe_fixture_rows([fixture_row], [live_row])
        self.assertEqual(deduped, [], "同一池位已有 live 資料時，fixture 不能再重複列出")

    def test_dedupe_keeps_fixture_row_when_no_matching_live_pool(self):
        fixture_row = normalize.pool_obj_to_row(
            self.fixture_pool_obj, source_label="offline fixture", snapshot_time=None,
            whitelist=self.whitelist, config=self.config,
        )
        fixture_row["status"] = "fixture"
        # 完全沒有 live 資料時（例如本次執行環境沒有 UNISWAP_API_KEY），fixture 必須保留
        # 作為離線 fallback，不能因為新增了 dedupe 邏輯就連唯一的資料來源都被濾掉。
        deduped = normalize.dedupe_fixture_rows([fixture_row], [])
        self.assertEqual(len(deduped), 1)

    def test_full_normalize_no_duplicate_pool_when_live_matches_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data_dir = Path(tmp)
            with mock.patch.object(normalize, "DATA_DIR", tmp_data_dir):
                pool_obj = self.fixture_pool_obj
                latest_raw = {
                    "fetched_at": "2026-09-26T07:40:00+00:00",
                    "results": [
                        {
                            "query_type": "poolParameters",
                            "chain_id": pool_obj["chainId"],
                            "chain_name": "Ethereum",
                            "protocol": pool_obj["poolProtocol"],
                            "base_symbol": "WETH",
                            "quote_symbol": "USDC",
                            "fee": pool_obj["fee"],
                            "http_status": 200,
                            "response": {"pools": [pool_obj]},
                            "fetched_at": "2026-09-26T07:40:00+00:00",
                        },
                    ],
                }
                with open(tmp_data_dir / "latest_raw.json", "w", encoding="utf-8") as f:
                    json.dump(latest_raw, f)

                exit_code = normalize.main()
                self.assertEqual(exit_code, 0)
                with open(tmp_data_dir / "normalized_latest.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

        pool_address = pool_obj["poolReferenceIdentifier"].lower()
        rows_for_pool = [
            r for r in data["rows"]
            if r["pool_address"] and r["pool_address"].lower() == pool_address
        ]
        # 正式 latest_raw.json 已含這顆池的 live 資料時，fixture 不可再重複列出同一顆池；
        # 只能有一筆，且狀態是 live（不是被 fixture 蓋掉或兩者並存）。
        self.assertEqual(len(rows_for_pool), 1, rows_for_pool)
        self.assertEqual(rows_for_pool[0]["status"], "live")


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
