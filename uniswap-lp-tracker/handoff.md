# Uniswap 多鏈 LP／熱門池唯讀儀表板 — handoff

> 任務：`t_bf8f4d3e`｜委託：`@anne`｜執行：`dev-claude`｜日期：2026-09-26
> 上游研究：`t_6b258dd1`（`handoff.md` / `handoff.json`）
> 路線：**官方 API**（`POST https://liquidity.api.uniswap.org/lp/pool_info`），
> **不使用** `Uniswap/uniswap-ai`（非 MCP，plugin 架構，本案不安裝）。

---

## 0. 給 @anne 的一段話：接手要做的三件事

1. **帶自己的 `UNISWAP_API_KEY` 跑一次真實資料**：
   ```bash
   cd uniswap-lp-tracker
   UNISWAP_API_KEY=你的key python3 scripts/run_daily_update.py --no-push
   ```
   會依序跑 `fetch_pool_info.py`（231 筆查詢，含 1 個 Unichain 池位參考 + 230 個
   token-pair 查詢，見下方 §2）→ `normalize.py` → `build_dashboard.py`，
   產生真實資料版的 `uniswap-lp-tracker-20260926.html`。**本次 dev-claude 執行
   環境沒有 key，所以目前頁面上 230 筆是 `pending`**，只有 1 筆（Ethereum
   WETH/USDC V3 0.30%）是你之前提供的離線 fixture。
2. **確認 Unichain 目標池位語意**：任務卡給的 `0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd`
   我假設是 pool address（`poolReferenceIdentifier`），腳本會依序嘗試
   V4 再 V3。如果實際上這是一個**錢包地址**（要查 LP position 而非池子本身），
   官方 Liquidity API **沒有唯讀 position 端點**，做不到 — 麻煩先跟需求方確認，
   若真的是要查錢包 position，需另外規劃 Subgraph `positions` 或 RPC 方案（不在本次範圍）。
3. **排程與 Telegram 是你/使用者的動作**：我沒有建立 cron（dev-claude 硬性限制：
   排程屬使用者/anne 範圍）。`scripts/run_daily_update.py --telegram` 已經寫好，
   但 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 沒給我，**Telegram 通知完全沒有
   實測過、也沒有宣稱送達**——你需要自己跑一次拿到 `message_id` 才算驗證成功。

---

## 1. 範圍與安全（已遵守的紅線）

- 全程唯讀：只呼叫官方 `/lp/pool_info`。不簽名、不 approve、不 permit、不 swap。
- `UNISWAP_API_KEY` 只用 `os.environ.get()` 讀取；三個會用到 key 的腳本
  （`fetch_pool_info.py` / `run_daily_update.py`，加上 `notify_telegram.py`
  用的 Telegram 憑證）在**沒有設定時一律直接報錯結束**，不會用假資料墊檔，
  更不會 print/log key 本身。已用 `grep` 確認 repo 內所有新檔案沒有任何
  `/Users/...` 絕對路徑或看起來像 key 的字串（見下方 §5 測試紀錄）。
- 沒有 `.env`、沒有任何憑證檔進 repo；`uniswap-lp-tracker/.gitignore` 額外擋
  `*api_key*` / `*.key` / `.env*`。
- 沒有安裝第三方套件、沒有 `npm install`/`npx`、沒有 clone 任何第三方 repo。
- 新增內容全部在獨立目錄 `uniswap-lp-tracker/` +頂層新檔
  `uniswap-lp-tracker-20260926.html`（比照站內既有報告的發布慣例）；`index.html`
  加了一個新章節連結，**沒有動** `obsidian-vault-second-brain-20260925.html`
  或既有未提交的根目錄 `scripts/`（先確認過 git baseline，這兩者本次任務前
  就是 modified/untracked 狀態，屬另一張任務卡的工作，本案原封不動）。

---

## 2. 資料目標與涵蓋範圍

| 鏈 | Chain ID | Base tokens 涵蓋 | Quote | Fee tiers | Protocol |
|---|---:|---|---|---|---|
| Ethereum | 1 | WETH, WBTC, cbBTC, WSOL(Wormhole), AAVE, UNI | USDC, USDT | 0.05% / 0.30% / 1% | V3, V4 |
| Arbitrum | 42161 | WETH, WBTC, cbBTC, AAVE, UNI | USDC, USDT | 同上 | V3, V4 |
| Base | 8453 | WETH, cbBTC, AAVE, UNI | USDC, USDT | 同上 | V3, V4 |
| BNB Smart Chain | 56 | WETH(peg), BTCB, AAVE, UNI | USDC, USDT | 同上 | V3, V4 |
| Unichain | 130 | — | — | — | 指定池位 `0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd`（見 §0.2） |

共 230 個 token-pair 查詢（4 鏈 × 平均 ~5 base token × 2 quote × 3 fee × 2 protocol，
扣除各鏈實際 base token 數不同）+ 1 個 Unichain 池位參考 = **231 筆目標**。

**SOL 覆蓋範圍限制**：只有 Ethereum 上找到可信賴、可交叉驗證的橋接位址
（Wormhole WSOL `0xD31a59c85aE9D8edEFeC411D448f90841571b89c`）。Arbitrum／Base／BNB
沒有找到廣泛採用且可信的橋接 SOL 位址，**沒有放進白名單**（寧可少列，不猜位址）。

**Token 合約位址白名單**：每一個地址都在 `config/pools_targets.json` 標註
`source`，2026-09-26 交叉驗證來源包含：
`developers.uniswap.org`（UNI 官方位址表，Ethereum/Arbitrum/Base/BNB/Unichain 皆有）、
`developers.circle.com`（USDC 官方位址表）、
`etherscan.io` / `arbiscan.io` / `basescan.org` / `bscscan.com`（WETH/WBTC/cbBTC/AAVE/BTCB/USDT 逐一核對）。

---

## 3. 官方端點能力邊界（不可造數的部分）

沿用上游研究 `t_6b258dd1` 的結論，本次逐一在 `normalize.py` 輸出中落實：

| 欄位 | 官方 pool_info 有嗎 | 本頁顯示 |
|---|:---:|---|
| Token pair、fee tier、tickSpacing、currentTick | ✅ | 直接顯示 |
| `poolLiquidity`（v3/v4 L 值） | ✅ | 顯示，明確標註「raw，非 USD」 |
| USD TVL | ❌ | `null` + 說明需 Subgraph |
| 24h/7d volume | ❌ | `null` + 說明需 Subgraph |
| Fee APR (24h/7d) | ❌ | `null` + 說明公式與需要的輸入 |
| 24h/7d 收入變化方向 | ❌ | `null` + 說明需歷史時序 |
| 錢包 LP 未領手續費/position | ❌（無唯讀端點） | 頁面固定顯示免責聲明，不嘗試查詢 |

---

## 4. 儀表板功能

`uniswap-lp-tracker-20260926.html`：
- 14 欄可點擊排序（鏈／協定／Token Pair／Fee Tier／Liquidity(raw)／TVL／24h
  Volume／7d Volume／Fee APR 24h／Fee APR 7d／Current Tick／狀態／快照時間／來源），
  含 `aria-sort` 無障礙標記與方向箭頭（▲/▼），再點同一欄反向排序。
  排序邏輯是純函式（`PURE_SORT_START`/`PURE_SORT_END` 區塊），null 一律排最後、
  不受方向影響；bigint 欄位（`pool_liquidity_raw`）用 `BigInt` 比較避免精度誤差。
- 頂部彙總卡片顯示 live/fixture/pending/not_found/untrusted 筆數；排序只改變
  顯示順序，「顯示中 N 筆／共 N 筆」計數不隨排序改變（見 §5 測試）。
- 免責聲明區塊固定顯示：APR 年化非保證、fee APR 不含幣價與 IL、
  active range 為快照當下、無錢包 LP 顯示（唯讀限制）、⚠️ 白名單警示說明。
- `⚠️ token 不在白名單` 狀態：若官方回應內出現不在 `config/pools_targets.json`
  白名單的 token 位址，`normalize.py` 會標記該列並停用數值顯示，防止假幣/釣魚
  合約污染頁面（`tests/test_offline.py::TestNormalizeSchema::test_untrusted_token_is_flagged_and_neutered` 覆蓋）。

---

## 5. 測試紀錄（實際執行輸出）

```
$ python3 tests/test_offline.py -v
...
Ran 13 tests in 0.003s
OK
```
涵蓋：fee tier 換算（3000→0.30% 等）、fee APR 公式（合成輸入驗證年化計算）、
token 白名單（跨鏈隔離、大小寫不敏感、未知位址拒絕）、fixture schema 正確性、
untrusted token 攔截、`normalize.py` 完整輸出 meta 與 rows 筆數一致性、
pending 列數值欄位必須全為 null（防造數迴歸）。

```
$ node tests/sort_logic_check.mjs
...
總計欄位×方向組合：28，失敗：0
```
對正式輸出的 231 筆真實資料，14 欄 × 正/反向 = 28 組合逐一驗證：排序前後
筆數不變、內容集合不變（id 相同 multiset）、非 null 值單調排序方向正確、
null 一律排最後不受方向影響。**已修過一個真實 bug**：初版把 null 排序判斷放在
方向乘法「之後」，導致降冪排序時 null 反而排到最前面；用這個測試抓到並修正。

**已知限制**：本機 Hermes `browser_navigate` 工具因 macOS TCC 權限問題失敗
（`Operation not permitted` 讀取 `~/Library/Application Support/Google/Chrome/Default`，
需要使用者在系統設定給終端機 App「完整磁碟取用權限」才能修，不在本任務範圍）。
因此排序驗證改用 Node.js 直接執行網頁裡「原封不動」的那段排序 JS
（`PURE_SORT_START`..`PURE_SORT_END`，逐字從已產生的 HTML 抽取執行，不是重寫一份），
是次佳但誠實的替代方案。建議日後瀏覽器工具可用時，人工用滑鼠對每個欄位點一次
複查一次視覺呈現（尤其是箭頭圖示與 `aria-sort` 屬性）。

```
$ python3 scripts/fetch_pool_info.py         (無 UNISWAP_API_KEY)
ERROR: 未設定環境變數 UNISWAP_API_KEY... exit=2

$ python3 scripts/run_daily_update.py        (無 UNISWAP_API_KEY)
ERROR: 未設定 UNISWAP_API_KEY，中止... exit=2

$ python3 scripts/notify_telegram.py         (無 TELEGRAM_BOT_TOKEN/CHAT_ID)
SKIP：未設定... 不會假裝已送達 exit=3
```
三個腳本在沒有憑證時都乾淨地拒絕執行、不寫假資料、不發真實網路請求（notify_telegram
在缺憑證時完全不連網）。

---

## 6. 發布狀態

- 檔案已加入 git，commit 訊息與 push 結果見下方「發布紀錄」章節（由本次執行
  補在 kanban 任務完成留言 / commit log 中，handoff.md 本身在 commit 之前寫成，
  故不重複貼 commit hash——請以 `git log` 為準）。
- 公開 URL（push 後生效，需等 GitHub Pages 建置）：
  `https://ul3you-cell.github.io/happyda-public/uniswap-lp-tracker-20260926.html`
- **本 handoff 不宣稱 Pages 已回 200 / Telegram 已送達**，除非有本次執行的
  真實 curl / API 回應佐證——若尚未附上，代表尚在等待 GitHub Pages 建置完成，
  需要後續一次 `curl -sI <url>` 複查。

---

## 7. 後續建議（給 anne / 使用者決定，非本卡範圍）

1. 帶 key 跑一次 `run_daily_update.py`，把 230 個 `pending` 換成真實資料，
   順便驗證哪些 token-pair × fee tier 組合在鏈上真的存在（不存在的會變成
   `not_found`，是正常現象，不代表程式錯誤）。
2. 若要 TVL／volume／fee APR，需要另外評估串接官方 Subgraph（endpoint URL
   需在官方文件當下頁面重新抓，因 hosted service 即將退役，不要沿用本次
   handoff 或上游 `t_6b258dd1` 提到的任何舊連結）。
3. Taipei 08:20 每日排程與 Telegram 通知：腳本已備妥（`run_daily_update.py
   --telegram`），實際建立 cron/launchd 與提供 Telegram 憑證屬使用者/anne 決定。
