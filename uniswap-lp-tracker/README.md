# Uniswap 多鏈 LP / 熱門池唯讀儀表板

任務卡：`t_bf8f4d3e`（委託 `@anne`，執行 `dev-claude`）
上游研究：`t_6b258dd1`（`handoff.md` / `handoff.json`，見任務附件）

## 範圍與紅線

- **唯讀**：只呼叫已驗證的官方 Uniswap Liquidity API
  `POST https://liquidity.api.uniswap.org/lp/pool_info`。不簽名、不 approve、
  不 permit、不 swap、不做任何交易準備。
- **不安裝第三方套件／不 clone 第三方 repo**：`Uniswap/uniswap-ai` 是
  Claude Code / Cursor plugin（`plugin.json`），非 MCP，本專案不使用。
- **不得讀取、索取、傳遞或輸出 `UNISWAP_API_KEY`**：所有腳本一律
  `os.environ.get("UNISWAP_API_KEY")`，never `print`/`log`/寫入檔案；
  `.env`、任何憑證檔一律不進 repo（見 `.gitignore`）。
- 本目錄是本案**獨立新增**的專案目錄，不觸碰既有未提交的
  `obsidian-vault-second-brain-20260925.html` 與 repo 根目錄既有 `scripts/`。

## 目錄結構

```
uniswap-lp-tracker/
  config/pools_targets.json     # 鏈、token 白名單、fee tier、特殊池位參考
  scripts/common.py             # 共用：白名單查驗、fee tier 換算、APR 公式
  scripts/fetch_pool_info.py    # 唯讀呼叫官方 API，讀 env key，寫 raw snapshot
  scripts/normalize.py          # raw snapshot(s) + fixture -> 正規化 rows
  scripts/build_dashboard.py    # normalized rows -> 發布用 HTML 儀表板
  scripts/run_daily_update.py   # 串接 fetch -> normalize -> build -> git commit/push
  scripts/notify_telegram.py    # Telegram 通知（需 anne 提供 bot token，未測試）
  data/fixtures/                # 離線 fixture（anne 實測、無 key）
  data/                         # 執行後產生的 raw / normalized 快照（可提交，不含 key）
  tests/test_offline.py         # 離線正規化 / 白名單 / APR 公式 / schema 測試
  handoff.md / handoff.json     # 交付摘要
```

發布頁面（GitHub Pages，比照站內其他報告採 repo 根目錄頂層檔）：
`../uniswap-lp-tracker-20260926.html`

## 執行方式（由 @anne 帶 key 執行）

```bash
export UNISWAP_API_KEY=...   # anne 自己的 key，絕不貼在此 repo / 任何檔案
cd uniswap-lp-tracker
python3 scripts/fetch_pool_info.py        # 呼叫官方 API，寫 data/snapshot-<ts>.json
python3 scripts/normalize.py              # 產生 data/normalized_latest.json
python3 scripts/build_dashboard.py        # 產生 ../uniswap-lp-tracker-20260926.html
```

或一次跑完（含 git commit/push，不含排程本身 — cron 由使用者/anne 建立）：

```bash
UNISWAP_API_KEY=... python3 scripts/run_daily_update.py
```

沒有 `UNISWAP_API_KEY` 時，`fetch_pool_info.py` 會直接報錯結束（exit 2），
不會用假資料填充。`normalize.py` 本身不需要任何旗標即可離線運作：
若 `data/latest_raw.json` 不存在（從未成功呼叫過官方 API），它只會讀
`data/fixtures/` 內的離線 fixture 並把其餘目標池位標為 `pending`，
不會連網，供本次 dev-claude 端驗證用。

## 資料欄位與限制（務必先讀）

官方 `/lp/pool_info` **不提供**：USD TVL、24h/7d volume、fee APR、歷史時序、
任何錢包 LP position（唯讀）。這些欄位在輸出中一律為 `null`，並附
`*_note` 說明「資料源未提供」與需要的替代來源（Uniswap 官方 Subgraph 或
RPC view 函式）。**沒有官方資料時絕不造數**。

詳見 `handoff.md`。
