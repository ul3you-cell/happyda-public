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
import stat
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402
import fetch_pool_info  # noqa: E402
import fetch_pool_metrics  # noqa: E402
import graph_gateway_check  # noqa: E402
import normalize  # noqa: E402
import run_daily_update  # noqa: E402
import wallet_apr_calc  # noqa: E402
import wallet_snapshot_store  # noqa: E402


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


class TestGraphApiKeyHandling(unittest.TestCase):
    """2026-09-26 修正：anne 查證 The Graph 官方文件沒有規定固定 key 格式，
    原本的「64 hex 才放行」硬性驗證撤掉（會誤擋有效 key）。現在只驗證
    「有沒有讀到」，長度過短只印非阻斷提醒，不 exit。"""

    PLAUSIBLE_KEY = "a" * 64  # 合成測試值，不是任何真實 key；只是「夠長」的樣本

    def test_none_and_empty_have_no_warning(self):
        # 空值本身不算「疑似截斷」，require_graph_api_key 的缺 key 分支才管這個
        self.assertIsNone(common.graph_api_key_length_warning(None))
        self.assertIsNone(common.graph_api_key_length_warning(""))

    def test_truncated_12_chars_gets_warning_not_rejection(self):
        # 重現使用者實測遇到的情況：keychain 只存到前 12 碼 -> 只警告，不擋
        warning = common.graph_api_key_length_warning("a" * 12)
        self.assertIsNotNone(warning)
        self.assertIn("12", warning)

    def test_plausible_length_key_has_no_warning(self):
        self.assertIsNone(common.graph_api_key_length_warning(self.PLAUSIBLE_KEY))

    def test_non_hex_characters_do_not_trigger_any_warning(self):
        # 不再假設官方格式是 hex；任何字元組成只要夠長都不該被判定可疑
        self.assertIsNone(common.graph_api_key_length_warning("not-hex-but-long-enough-key-string"))

    def test_whitespace_is_trimmed_before_length_check(self):
        self.assertIsNone(common.graph_api_key_length_warning(f"  {self.PLAUSIBLE_KEY}\n"))

    def test_require_graph_api_key_exits_2_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(common.GRAPH_API_KEY_ENV, None)
            with self.assertRaises(SystemExit) as ctx:
                common.require_graph_api_key()
            self.assertEqual(ctx.exception.code, 2)

    def test_require_graph_api_key_does_not_exit_on_truncated_key(self):
        # 核心修正點：12 碼的 key 過去會 exit(2)，現在必須放行（只印警告）
        with mock.patch.dict(os.environ, {common.GRAPH_API_KEY_ENV: "a" * 12}):
            result = common.require_graph_api_key()
            self.assertEqual(result, "a" * 12)

    def test_require_graph_api_key_returns_stripped_value(self):
        with mock.patch.dict(os.environ, {common.GRAPH_API_KEY_ENV: f"  {self.PLAUSIBLE_KEY}  "}):
            result = common.require_graph_api_key()
            self.assertEqual(result, self.PLAUSIBLE_KEY)

    def test_missing_key_error_message_never_prints_key_content(self):
        import contextlib
        import io

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(common.GRAPH_API_KEY_ENV, None)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit):
                    common.require_graph_api_key()
            self.assertIn("GRAPH_API_KEY", buf.getvalue())

    def test_truncated_key_warning_never_prints_full_key_content(self):
        import contextlib
        import io

        truncated = "b" * 12
        with mock.patch.dict(os.environ, {common.GRAPH_API_KEY_ENV: truncated}):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                result = common.require_graph_api_key()
            self.assertEqual(result, truncated)
            # 警告訊息只能提長度數字，不能把完整 key 字串印出來
            self.assertNotIn(truncated, buf.getvalue())


class TestGraphGatewayCheck(unittest.TestCase):
    """graph_gateway_check.py 的唯讀 smoke test 邏輯：全部用 mock.patch 攔截
    urllib.request.urlopen，不打真實網路（CI/離線環境不該連上 The Graph）。"""

    FAKE_KEY = "deadbeef" * 8  # 合成測試值，不是任何真實 key

    @staticmethod
    def _fake_response(payload: dict):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        return _Resp()

    def test_success_extracts_block_number(self):
        payload = {"data": {"_meta": {"block": {"number": 12345678}}}}
        with mock.patch("graph_gateway_check.urllib.request.urlopen", return_value=self._fake_response(payload)):
            ok, message = graph_gateway_check.check_gateway(self.FAKE_KEY)
        self.assertTrue(ok)
        self.assertIn("12345678", message)
        self.assertNotIn(self.FAKE_KEY, message)

    def test_graphql_errors_field_is_reported_as_failure(self):
        payload = {"errors": [{"message": "deployment does not exist"}]}
        with mock.patch("graph_gateway_check.urllib.request.urlopen", return_value=self._fake_response(payload)):
            ok, message = graph_gateway_check.check_gateway(self.FAKE_KEY)
        self.assertFalse(ok)
        self.assertIn("deployment does not exist", message)
        self.assertNotIn(self.FAKE_KEY, message)

    def test_missing_expected_field_is_reported_as_failure(self):
        payload = {"data": {}}
        with mock.patch("graph_gateway_check.urllib.request.urlopen", return_value=self._fake_response(payload)):
            ok, message = graph_gateway_check.check_gateway(self.FAKE_KEY)
        self.assertFalse(ok)

    def test_http_error_response_never_leaks_key(self):
        import io

        err_body = io.BytesIO(b'{"error": "invalid api key"}')

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                f"https://gateway.thegraph.com/api/{self.FAKE_KEY}/subgraphs/id/x",
                401, "Unauthorized", {}, err_body,
            )

        with mock.patch("graph_gateway_check.urllib.request.urlopen", side_effect=_raise):
            ok, message = graph_gateway_check.check_gateway(self.FAKE_KEY)
        self.assertFalse(ok)
        self.assertIn("401", message)
        self.assertNotIn(self.FAKE_KEY, message)

    def test_http_error_exposes_public_cf_ray_id_not_key(self):
        # anne 回報使用者實測 HTTP 403 / Cloudflare error 1010：CF-RAY 是
        # Cloudflare 的公開請求識別碼（非憑證），失敗訊息應該把它帶出來，
        # 但同一次仍不能洩漏 key。
        import email.message
        import io

        err_body = io.BytesIO(b"error code: 1010")
        headers = email.message.Message()
        headers["CF-RAY"] = "8c1a2b3d4e5f6789-SJC"

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                f"https://gateway.thegraph.com/api/{self.FAKE_KEY}/subgraphs/id/x",
                403, "Forbidden", headers, err_body,
            )

        with mock.patch("graph_gateway_check.urllib.request.urlopen", side_effect=_raise):
            ok, message = graph_gateway_check.check_gateway(self.FAKE_KEY)
        self.assertFalse(ok)
        self.assertIn("403", message)
        self.assertIn("8c1a2b3d4e5f6789-SJC", message)
        self.assertNotIn(self.FAKE_KEY, message)

    def test_request_sends_explicit_user_agent_and_accept_headers(self):
        # anne 懷疑 Cloudflare 403/1010 是 Python 預設 User-Agent 被擋，
        # 這裡斷言送出的 urllib.Request 確實帶有明確、固定的 UA 與 Accept，
        # 不是讓 urllib 用預設值（例如 "Python-urllib/3.14"）。
        captured = {}

        def _capture(req, timeout=None):
            captured["user_agent"] = req.get_header("User-agent")
            captured["accept"] = req.get_header("Accept")
            captured["content_type"] = req.get_header("Content-type")
            return self._fake_response({"data": {"_meta": {"block": {"number": 1}}}})

        with mock.patch("graph_gateway_check.urllib.request.urlopen", side_effect=_capture):
            graph_gateway_check.check_gateway(self.FAKE_KEY)

        self.assertTrue(captured["user_agent"])
        self.assertNotIn("Python-urllib", captured["user_agent"])
        self.assertEqual(captured["accept"], "application/json")
        self.assertEqual(captured["content_type"], "application/json")

    def test_url_error_never_leaks_key(self):
        def _raise(*args, **kwargs):
            raise urllib.error.URLError("Name or service not known")

        with mock.patch("graph_gateway_check.urllib.request.urlopen", side_effect=_raise):
            ok, message = graph_gateway_check.check_gateway(self.FAKE_KEY)
        self.assertFalse(ok)
        self.assertNotIn(self.FAKE_KEY, message)

    def test_main_exits_2_when_key_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(common.GRAPH_API_KEY_ENV, None)
            with self.assertRaises(SystemExit) as ctx:
                graph_gateway_check.main()
            self.assertEqual(ctx.exception.code, 2)


class TestComputePoolDayMetrics(unittest.TestCase):
    """common.compute_pool_day_metrics 純函式：TVL／24h·7d volume／fee APR／
    收入變化方向的計算與「資料不足」邊界。合成資料，不連網。

    **`date` 一律用真實 Unix 秒**（不是 day bucket number）——這是
    2026-09-26 那個「65 顆池全部誤判 days_available=0」bug 的教訓：先前這裡
    的 fixture 直接把 day bucket number 當 `date` 塞進去，單元測試對得動，
    但完全沒測到「Unix 秒轉 day bucket」這一步，e2e 才炸開。"""

    DAY = 86400
    TODAY_START = 1735689600  # 2025-01-01 00:00:00 UTC，當天第一秒（真實 Unix 秒）
    NOW_TS = float(TODAY_START + 3600)  # 當天過了 1 小時

    @classmethod
    def _day_ts(cls, days_ago: int) -> int:
        """回傳「days_ago 天前」那一天的（真實）Unix 秒時間戳，模擬 The Graph
        `PoolDayData.date` 的實際格式。"""
        return cls.TODAY_START - days_ago * cls.DAY

    def test_today_partial_day_excluded_from_calculation(self):
        day_data = [{"date": self.TODAY_START + 1800, "volumeUSD": "999999", "feesUSD": "999999"}]
        result = common.compute_pool_day_metrics(1_000_000.0, day_data, self.NOW_TS)
        self.assertEqual(result["days_available"], 0)
        self.assertIsNone(result["volume_24h_usd"])
        self.assertIsNone(result["fee_apr_24h_pct"])
        self.assertIn("資料不足", result["note"])

    def test_out_of_range_zero_fees_is_valid_not_missing(self):
        # research 的重點：out-of-range 那天 feesUSD=0 合法，不是缺值
        day_data = [{"date": self._day_ts(1), "volumeUSD": "0", "feesUSD": "0"}]
        result = common.compute_pool_day_metrics(1_000_000.0, day_data, self.NOW_TS)
        self.assertEqual(result["volume_24h_usd"], 0.0)
        self.assertEqual(result["fee_apr_24h_pct"], 0.0)

    def test_seven_complete_days_yields_7d_apr(self):
        day_data = [
            {"date": self._day_ts(1 + i), "volumeUSD": "10000", "feesUSD": "1000"}
            for i in range(8)  # 8 天前..1 天前，全部都是「完整天」
        ]
        result = common.compute_pool_day_metrics(1_000_000.0, day_data, self.NOW_TS)
        self.assertEqual(result["days_available"], 8)
        self.assertIsNotNone(result["fee_apr_7d_pct"])
        # 7 天 * 1000 fees / 1,000,000 tvl * (365/7) * 100 = 36.5%
        self.assertAlmostEqual(result["fee_apr_7d_pct"], 36.5, places=1)
        self.assertEqual(result["volume_7d_usd"], 70000.0)

    def test_fewer_than_seven_days_marks_insufficient_not_fabricated(self):
        day_data = [{"date": self._day_ts(1 + i), "volumeUSD": "10000", "feesUSD": "1000"} for i in range(3)]
        result = common.compute_pool_day_metrics(1_000_000.0, day_data, self.NOW_TS)
        self.assertIsNone(result["fee_apr_7d_pct"])
        self.assertIsNone(result["volume_7d_usd"])
        self.assertIn("尚不足計算", result["note"])
        # 24h 指標不受 7d 資料不足影響，仍應算得出來
        self.assertIsNotNone(result["fee_apr_24h_pct"])

    def test_income_change_direction_up_down_flat(self):
        up = common.compute_pool_day_metrics(
            1_000_000.0,
            [{"date": self._day_ts(1), "volumeUSD": "1", "feesUSD": "200"}, {"date": self._day_ts(2), "volumeUSD": "1", "feesUSD": "100"}],
            self.NOW_TS,
        )
        down = common.compute_pool_day_metrics(
            1_000_000.0,
            [{"date": self._day_ts(1), "volumeUSD": "1", "feesUSD": "50"}, {"date": self._day_ts(2), "volumeUSD": "1", "feesUSD": "100"}],
            self.NOW_TS,
        )
        flat = common.compute_pool_day_metrics(
            1_000_000.0,
            [{"date": self._day_ts(1), "volumeUSD": "1", "feesUSD": "100"}, {"date": self._day_ts(2), "volumeUSD": "1", "feesUSD": "100"}],
            self.NOW_TS,
        )
        self.assertEqual(up["income_change_direction"], "up")
        self.assertEqual(down["income_change_direction"], "down")
        self.assertEqual(flat["income_change_direction"], "flat")

    def test_single_day_has_no_change_direction(self):
        result = common.compute_pool_day_metrics(1_000_000.0, [{"date": self._day_ts(1), "volumeUSD": "1", "feesUSD": "100"}], self.NOW_TS)
        self.assertIsNone(result["income_change_direction"])

    def test_no_pool_tvl_still_reports_volume_but_no_apr(self):
        result = common.compute_pool_day_metrics(None, [{"date": self._day_ts(1), "volumeUSD": "500", "feesUSD": "50"}], self.NOW_TS)
        self.assertEqual(result["volume_24h_usd"], 500.0)
        self.assertIsNone(result["fee_apr_24h_pct"])

    def test_empty_day_data_reports_insufficient_history(self):
        result = common.compute_pool_day_metrics(1_000_000.0, [], self.NOW_TS)
        self.assertEqual(result["days_available"], 0)
        self.assertIn("資料不足", result["note"])


class TestComputePoolDayMetricsUnixTimestampRegression(unittest.TestCase):
    """@research 交付的 regression fixture spec：tests/fixtures/pool_day/*.json
    每個 case 是「真實 Unix 秒 `date` + 手算 expected」，逐一比對
    common.compute_pool_day_metrics() 的輸出，直接對應 2026-09-26 那個
    「65 顆池全部誤判 days_available=0」bug 的修復。"""

    FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "pool_day")

    def _load(self, case_id: str) -> dict:
        path = os.path.join(self.FIXTURE_DIR, f"{case_id}.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _assert_case(self, case_id: str):
        case = self._load(case_id)
        result = common.compute_pool_day_metrics(
            case["tvl_usd"], case["day_data_desc"], float(case["now_ts"])
        )
        expected = case["expected"]
        for key, value in expected.items():
            if isinstance(value, float):
                self.assertAlmostEqual(result[key], value, places=4, msg=f"{case_id}: field {key}")
            else:
                self.assertEqual(result[key], value, msg=f"{case_id}: field {key}")

    def test_case_a_simple_full_week(self):
        self._assert_case("case_a_simple_full_week")
        # 核心 regression 斷言：修復前這條必失敗（days_available 恆為 0）
        result = common.compute_pool_day_metrics(**self._as_kwargs("case_a_simple_full_week"))
        self.assertGreater(result["days_available"], 0)
        self.assertIsNotNone(result["fee_apr_7d_pct"])

    def test_case_b_partial_lt7(self):
        self._assert_case("case_b_partial_lt7")

    def test_case_c_today_bucket_present(self):
        self._assert_case("case_c_today_bucket_present")

    def test_case_d_out_of_range_zero_fees(self):
        self._assert_case("case_d_out_of_range_zero_fees")

    def test_case_e_stale_data_beyond_7(self):
        self._assert_case("case_e_stale_data_beyond_7")

    def _as_kwargs(self, case_id: str) -> dict:
        case = self._load(case_id)
        return {
            "tvl_usd": case["tvl_usd"],
            "day_data_desc": case["day_data_desc"],
            "now_ts": float(case["now_ts"]),
        }


class TestFetchPoolMetricsBatchQuery(unittest.TestCase):
    """fetch_pool_metrics.py 的批次 GraphQL alias 組裝／解析：純字串/字典操作，
    不連網。"""

    def test_build_batch_query_contains_aliases_for_each_pool(self):
        query = fetch_pool_metrics.build_batch_query(["0xAAA", "0xBBB"])
        self.assertIn("p0: pool(id: \"0xaaa\")", query)
        self.assertIn("d0: poolDayDatas(where: { pool: \"0xaaa\" }", query)
        self.assertIn("p1: pool(id: \"0xbbb\")", query)
        self.assertIn("d1: poolDayDatas(where: { pool: \"0xbbb\" }", query)

    def test_parse_batch_response_extracts_tvl_and_day_data(self):
        body = {
            "data": {
                "p0": {"id": "0xaaa", "totalValueLockedUSD": "1234.5"},
                "d0": [{"date": 100, "volumeUSD": "10", "feesUSD": "1"}],
                "p1": {"id": "0xbbb", "totalValueLockedUSD": "999"},
                "d1": [],
            }
        }
        parsed = fetch_pool_metrics.parse_batch_response(body, ["0xAAA", "0xBBB"])
        self.assertEqual(parsed["0xaaa"]["tvl_usd"], 1234.5)
        self.assertEqual(len(parsed["0xaaa"]["day_data"]), 1)
        self.assertEqual(parsed["0xbbb"]["tvl_usd"], 999.0)
        self.assertEqual(parsed["0xbbb"]["day_data"], [])

    def test_parse_batch_response_pool_not_found_keeps_none_not_fabricated(self):
        # 官方回 pool: null（池子在這個 deployment 上真的不存在），不能假裝有資料
        body = {"data": {"p0": None, "d0": []}}
        parsed = fetch_pool_metrics.parse_batch_response(body, ["0xCCC"])
        self.assertIsNone(parsed["0xccc"]["tvl_usd"])
        self.assertEqual(parsed["0xccc"]["day_data"], [])


class TestMergeGraphEnrichment(unittest.TestCase):
    """normalize.merge_graph_enrichment：只覆蓋對得上 chain_id + pool_address
    且 status 為 live/fixture 的列，其餘維持原本的『資料源未提供』。"""

    def _row(self, **overrides):
        row = normalize.base_row(1, "Ethereum")
        row.update({
            "status": "live",
            "pool_address": "0xAAAABBBBCCCCDDDDEEEEFFFF00001111222233334",
        })
        row.update(overrides)
        return row

    def test_none_payload_leaves_rows_untouched(self):
        rows = [self._row()]
        result = normalize.merge_graph_enrichment(rows, None)
        self.assertIsNone(result[0]["tvl_usd"])
        self.assertEqual(result[0]["tvl_usd_note"], normalize.TVL_NOTE)

    def test_matching_pool_gets_overlaid(self):
        addr = "0xaaaabbbbccccddddeeeeffff00001111222233334"
        rows = [self._row(pool_address=addr)]
        payload = {
            "chain_id": 1,
            "generated_at": "2026-09-26T00:00:00Z",
            "source": "test-source",
            "pools": {
                addr: {
                    "tvl_usd": 1_000_000.0,
                    "volume_24h_usd": 5000.0,
                    "volume_7d_usd": 35000.0,
                    "fee_apr_24h_pct": 12.3,
                    "fee_apr_7d_pct": 11.1,
                    "income_change_direction": "up",
                    "note": None,
                }
            },
        }
        result = normalize.merge_graph_enrichment(rows, payload)
        self.assertEqual(result[0]["tvl_usd"], 1_000_000.0)
        self.assertEqual(result[0]["fee_apr_7d_pct"], 11.1)
        self.assertEqual(result[0]["income_change_direction"], "up")
        self.assertIsNone(result[0]["tvl_usd_note"])

    def test_pending_status_row_never_overlaid(self):
        rows = [self._row(status="pending", pool_address=None)]
        payload = {"chain_id": 1, "pools": {}, "generated_at": "x", "source": "y"}
        result = normalize.merge_graph_enrichment(rows, payload)
        self.assertIsNone(result[0]["tvl_usd"])

    def test_wrong_chain_id_never_overlaid(self):
        rows = [self._row()]
        rows[0]["chain_id"] = 42161  # Arbitrum -- enrichment 只覆蓋 chain_id=1
        payload = {"chain_id": 1, "pools": {}, "generated_at": "x", "source": "y"}
        result = normalize.merge_graph_enrichment(rows, payload)
        self.assertIsNone(result[0]["tvl_usd"])

    def test_pool_not_in_enrichment_gets_explanatory_note_not_silence(self):
        rows = [self._row()]
        payload = {"chain_id": 1, "pools": {}, "generated_at": "2026-09-26T00:00:00Z", "source": "y"}
        result = normalize.merge_graph_enrichment(rows, payload)
        self.assertIsNone(result[0]["tvl_usd"])
        self.assertIn("查無對應資料", result[0]["tvl_usd_note"])


class TestWalletSnapshotStore(unittest.TestCase):
    """wallet_snapshot_store.py：最小化 SQLite 儲存層，schema/insert/fetch
    round trip。全部用 tempfile 路徑，不碰使用者真實的 DEFAULT_DB_PATH。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wallet-tracker-test-")
        self.db_path = os.path.join(self.tmpdir, "test.sqlite3")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _snapshot(self, ts, fees=100.0, value=10000.0, in_range=True):
        return dict(
            ts=ts, wallet_addr="0xABCDEF0000000000000000000000000000ABCD",
            chain_id=1, token_id=42, pool_addr="0xPOOL", tick_lower=-100, tick_upper=100,
            liquidity="123456789012345678901234", in_range=in_range,
            fees_accrued_usd=fees, position_value_usd=value,
        )

    def test_insert_and_fetch_round_trip_ordered_by_ts(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(300, fees=3.0))
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(100, fees=1.0))
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(200, fees=2.0))
        history = wallet_snapshot_store.fetch_position_history(
            conn, wallet_addr="0xabcdef0000000000000000000000000000abcd", chain_id=1, token_id=42
        )
        self.assertEqual([h["ts"] for h in history], [100, 200, 300])
        self.assertEqual([h["fees_accrued_usd"] for h in history], [1.0, 2.0, 3.0])
        conn.close()

    def test_insert_or_replace_same_day_overwrites_not_errors(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(100, fees=1.0))
        # 排程重跑同一天：不能因為主鍵衝突而炸掉，應該視為修正覆蓋
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(100, fees=999.0))
        history = wallet_snapshot_store.fetch_position_history(
            conn, wallet_addr="0xabcdef0000000000000000000000000000abcd", chain_id=1, token_id=42
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["fees_accrued_usd"], 999.0)
        conn.close()

    def test_wallet_addr_case_insensitive_lookup(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(100))
        history = wallet_snapshot_store.fetch_position_history(
            conn, wallet_addr="0xABCDEF0000000000000000000000000000ABCD", chain_id=1, token_id=42
        )
        self.assertEqual(len(history), 1)
        conn.close()

    def test_missing_fees_stored_as_none_not_zero(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(100, fees=None))
        history = wallet_snapshot_store.fetch_position_history(
            conn, wallet_addr="0xabcdef0000000000000000000000000000abcd", chain_id=1, token_id=42
        )
        self.assertIsNone(history[0]["fees_accrued_usd"])
        conn.close()

    def test_list_tracked_positions(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_snapshot_store.insert_snapshot(conn, **self._snapshot(100))
        other = self._snapshot(100)
        other["token_id"] = 43
        other["pool_addr"] = "0xPOOL2"
        wallet_snapshot_store.insert_snapshot(conn, **other)
        positions = wallet_snapshot_store.list_tracked_positions(
            conn, wallet_addr="0xabcdef0000000000000000000000000000abcd", chain_id=1
        )
        self.assertEqual(sorted(p[0] for p in positions), [42, 43])
        conn.close()


class TestMigrateLegacyDb(unittest.TestCase):
    """wallet_snapshot_store.migrate_legacy_db()：舊隱藏路徑 ->
    ~/Documents/... 新路徑的一次性安全搬移。全程用 tempdir 模擬兩邊路徑，
    完全不碰真實的 LEGACY_DB_PATH / DEFAULT_DB_PATH。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wallet-tracker-migrate-")
        self.legacy_path = Path(self.tmpdir) / "legacy" / "wallet_snapshots.sqlite3"
        self.new_path = Path(self.tmpdir) / "new" / "wallet_snapshots.sqlite3"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_valid_legacy_db(self):
        self.legacy_path.parent.mkdir(parents=True, exist_ok=True)
        conn = wallet_snapshot_store.get_connection(self.legacy_path)
        wallet_snapshot_store.insert_snapshot(
            conn, ts=100, wallet_addr="0xabc", chain_id=1, token_id=1, pool_addr="0xpool",
            tick_lower=-1, tick_upper=1, liquidity="1", in_range=True,
            fees_accrued_usd=1.0, position_value_usd=100.0,
        )
        conn.close()

    def test_nothing_to_migrate_when_legacy_absent(self):
        status = wallet_snapshot_store.migrate_legacy_db(self.legacy_path, self.new_path)
        self.assertEqual(status, "nothing_to_migrate")
        self.assertFalse(self.new_path.exists())

    def test_migrates_valid_legacy_db_to_new_path(self):
        self._make_valid_legacy_db()
        status = wallet_snapshot_store.migrate_legacy_db(self.legacy_path, self.new_path)
        self.assertEqual(status, "migrated")
        self.assertTrue(self.new_path.exists())
        self.assertFalse(self.legacy_path.exists())  # 搬移=不留舊檔
        # 搬過去的資料要能正常讀出來，不是只搬了個空殼
        conn = wallet_snapshot_store.get_connection(self.new_path)
        history = wallet_snapshot_store.fetch_position_history(conn, wallet_addr="0xabc", chain_id=1, token_id=1)
        self.assertEqual(len(history), 1)
        conn.close()

    def test_never_overwrites_existing_new_path(self):
        self._make_valid_legacy_db()
        self.new_path.parent.mkdir(parents=True, exist_ok=True)
        conn = wallet_snapshot_store.get_connection(self.new_path)
        wallet_snapshot_store.insert_snapshot(
            conn, ts=999, wallet_addr="0xexisting", chain_id=1, token_id=2, pool_addr="0xpool2",
            tick_lower=-1, tick_upper=1, liquidity="1", in_range=True,
            fees_accrued_usd=2.0, position_value_usd=200.0,
        )
        conn.close()
        status = wallet_snapshot_store.migrate_legacy_db(self.legacy_path, self.new_path)
        self.assertEqual(status, "skip_new_already_exists")
        self.assertTrue(self.legacy_path.exists())  # 舊檔完全沒被動
        conn = wallet_snapshot_store.get_connection(self.new_path)
        history = wallet_snapshot_store.fetch_position_history(conn, wallet_addr="0xexisting", chain_id=1, token_id=2)
        self.assertEqual(len(history), 1)  # 新檔內容沒被覆蓋掉
        conn.close()

    def test_corrupt_legacy_db_not_migrated(self):
        self.legacy_path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_path.write_bytes(b"this is not a real sqlite file")
        status = wallet_snapshot_store.migrate_legacy_db(self.legacy_path, self.new_path)
        self.assertEqual(status, "legacy_corrupt_not_migrated")
        self.assertFalse(self.new_path.exists())
        self.assertTrue(self.legacy_path.exists())  # 壞檔留在原地，沒被搬走或刪掉


class TestWalletTrackerReadmeAndPermissions(unittest.TestCase):
    """README-備份與還原.md 與資料夾權限（get_connection 的預設路徑分支）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wallet-tracker-readme-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_readme_written_with_required_topics(self):
        target = Path(self.tmpdir) / "docs-style-dir"
        target.mkdir(parents=True, exist_ok=True)
        wallet_snapshot_store._ensure_readme(target)
        readme_path = target / wallet_snapshot_store.README_FILENAME
        self.assertTrue(readme_path.exists())
        text = readme_path.read_text(encoding="utf-8")
        for keyword in ("備份", "還原", "Time Machine", "不進任何 git repo"):
            self.assertIn(keyword, text)

    def test_readme_not_overwritten_if_already_present(self):
        target = Path(self.tmpdir) / "docs-style-dir"
        target.mkdir(parents=True, exist_ok=True)
        readme_path = target / wallet_snapshot_store.README_FILENAME
        readme_path.write_text("使用者自己寫的筆記", encoding="utf-8")
        wallet_snapshot_store._ensure_readme(target)
        self.assertEqual(readme_path.read_text(encoding="utf-8"), "使用者自己寫的筆記")

    def test_get_connection_sets_owner_only_directory_permission(self):
        db_path = Path(self.tmpdir) / "perm-check" / "wallet_snapshots.sqlite3"
        conn = wallet_snapshot_store.get_connection(db_path)
        conn.close()
        mode = stat.S_IMODE(db_path.parent.stat().st_mode)
        self.assertEqual(mode, stat.S_IRWXU)


class TestComputeWalletPositionMetrics(unittest.TestCase):
    """wallet_apr_calc.compute_wallet_position_metrics：純函式，合成資料，
    對應 anne 的要求「先完成合成資料→連續快照→delta／APR 離線測試」。"""

    def _rows(self, n, fees=10.0, value=10000.0):
        # fees/value 比例固定 = 0.001/day -> 不論窗口長度都年化成 36.5%
        # （APR = fees_sum/avg_value*(365/window_days)*100，資料均勻時窗口長度
        # 會互相抵消，這是刻意選的比例，方便手算對照）。
        return [{"ts": i, "fees_accrued_usd": fees, "position_value_usd": value} for i in range(n)]

    def test_zero_days_reports_not_started(self):
        result = wallet_apr_calc.compute_wallet_position_metrics([])
        self.assertEqual(result["days_tracked"], 0)
        self.assertIsNone(result["fee_apr_7d_pct"])
        self.assertIn("尚未開始追蹤", result["note"])

    def test_six_days_insufficient_for_7d(self):
        result = wallet_apr_calc.compute_wallet_position_metrics(self._rows(6))
        self.assertIsNone(result["fee_apr_7d_pct"])
        self.assertIsNone(result["fee_apr_30d_pct"])
        self.assertIn("尚不足計算", result["note"])

    def test_exactly_seven_days_yields_7d_apr_not_30d(self):
        # 100 usd/day * 7 天 / 10000 avg value * (365/7) * 100 = 36.5%
        result = wallet_apr_calc.compute_wallet_position_metrics(self._rows(7))
        self.assertEqual(result["days_tracked"], 7)
        self.assertAlmostEqual(result["fee_apr_7d_pct"], 36.5, places=1)
        self.assertIsNone(result["fee_apr_30d_pct"])
        self.assertIn("30d APR 尚不足計算", result["note"])

    def test_twenty_nine_days_still_no_30d(self):
        result = wallet_apr_calc.compute_wallet_position_metrics(self._rows(29))
        self.assertIsNotNone(result["fee_apr_7d_pct"])
        self.assertIsNone(result["fee_apr_30d_pct"])

    def test_thirty_days_yields_both_windows(self):
        result = wallet_apr_calc.compute_wallet_position_metrics(self._rows(30))
        self.assertAlmostEqual(result["fee_apr_7d_pct"], 36.5, places=1)
        self.assertAlmostEqual(result["fee_apr_30d_pct"], 36.5, places=1)
        self.assertIsNone(result["note"])

    def test_uses_only_most_recent_window_not_full_history(self):
        # 前面 40 天 fees=0，最近 7 天 fees=1000 -- 7d APR 必須只反映最近 7 天
        rows = [{"ts": i, "fees_accrued_usd": 0.0, "position_value_usd": 10000.0} for i in range(40)]
        rows += [{"ts": 40 + i, "fees_accrued_usd": 1000.0, "position_value_usd": 10000.0} for i in range(7)]
        result = wallet_apr_calc.compute_wallet_position_metrics(rows)
        # 7d 視窗全部是 fees=1000：7000/10000*(365/7)*100 = 3650%
        self.assertAlmostEqual(result["fee_apr_7d_pct"], 3650.0, places=1)
        # 30d 視窗涵蓋 23 天 fees=0 + 7 天 fees=1000：7000/10000*(365/30)*100 ≈ 851.67%
        self.assertAlmostEqual(result["fee_apr_30d_pct"], 851.6667, places=3)
        # 兩個數字明顯不同，證明 7d/30d 真的各自用自己的視窗算，不是同一份全歷史平均
        self.assertNotAlmostEqual(result["fee_apr_30d_pct"], result["fee_apr_7d_pct"], places=1)

    def test_missing_fee_in_window_blocks_that_windows_apr_not_fabricated(self):
        rows = self._rows(30)
        rows[-3]["fees_accrued_usd"] = None  # 7d 與 30d 視窗都涵蓋這天
        result = wallet_apr_calc.compute_wallet_position_metrics(rows)
        self.assertIsNone(result["fee_apr_7d_pct"])
        self.assertIsNone(result["fee_apr_30d_pct"])
        self.assertIn("暫不可信", result["note"])


class TestWalletTrackingEndToEnd(unittest.TestCase):
    """合成資料 -> 連續快照寫入 SQLite -> 讀出 -> 算 delta/APR 的完整鏈路，
    對應 anne 這輪明確要求的離線驗收順序。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wallet-tracker-e2e-")
        self.db_path = os.path.join(self.tmpdir, "e2e.sqlite3")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_35_synthetic_days_produce_7d_and_30d_apr(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_addr = "0x1111111111111111111111111111111111aaaa"
        for day in range(35):
            wallet_snapshot_store.insert_snapshot(
                conn,
                ts=1735689600 + day * 86400,
                wallet_addr=wallet_addr,
                chain_id=1,
                token_id=7,
                pool_addr="0xpool",
                tick_lower=-887220,
                tick_upper=887220,
                liquidity="5000000000000000000",
                in_range=True,
                fees_accrued_usd=50.0,
                position_value_usd=20000.0,
            )
        history = wallet_snapshot_store.fetch_position_history(
            conn, wallet_addr=wallet_addr, chain_id=1, token_id=7
        )
        self.assertEqual(len(history), 35)
        result = wallet_apr_calc.compute_wallet_position_metrics(history)
        # 均勻資料下 APR = fees/value*365*100，跟窗口長度無關：50/20000*365*100 = 91.25%
        self.assertAlmostEqual(result["fee_apr_7d_pct"], 91.25, places=2)
        self.assertAlmostEqual(result["fee_apr_30d_pct"], 91.25, places=2)
        conn.close()

    def test_first_six_days_of_real_rollout_show_insufficient_not_fabricated(self):
        conn = wallet_snapshot_store.get_connection(self.db_path)
        wallet_addr = "0x2222222222222222222222222222222222bbbb"
        for day in range(6):
            wallet_snapshot_store.insert_snapshot(
                conn, ts=1735689600 + day * 86400, wallet_addr=wallet_addr, chain_id=1, token_id=9,
                pool_addr="0xpool2", tick_lower=-1000, tick_upper=1000, liquidity="1",
                in_range=True, fees_accrued_usd=10.0, position_value_usd=5000.0,
            )
        history = wallet_snapshot_store.fetch_position_history(conn, wallet_addr=wallet_addr, chain_id=1, token_id=9)
        result = wallet_apr_calc.compute_wallet_position_metrics(history)
        self.assertIsNone(result["fee_apr_7d_pct"])
        self.assertIn("尚不足計算", result["note"])
        conn.close()


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
