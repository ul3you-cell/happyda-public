# Uniswap 多鏈 LP／熱門池唯讀儀表板 — handoff

> 任務：`t_bf8f4d3e`｜委託：`@anne`｜執行：`dev-claude`｜日期：2026-09-26
> 上游研究：`t_6b258dd1`（`handoff.md` / `handoff.json`）
> 路線：**官方 API**（`POST https://liquidity.api.uniswap.org/lp/pool_info`），
> **不使用** `Uniswap/uniswap-ai`（非 MCP，plugin 架構，本案不安裝）。

---

## 0.1 第二輪修正（anne review → 已回應的 5 點）

anne 在第一輪 review 抓到 5 個可重現問題，本輪逐一修正並補測試，細節見
`scripts/run_daily_update.py`、`scripts/normalize.py`、`README.md` 的內文
註解與 `tests/test_offline.py` 新增的 3 個測試類別：

1. **commit 範圍不夠嚴謹**：`run_daily_update.py` 原本用不帶 pathspec 的
   `git status --porcelain` 判斷是否要 commit，`git commit` 也沒帶
   pathspec——工作目錄若剛好有其他任務的異動（例如既有的
   `obsidian-vault-second-brain-20260925.html`、未追蹤的根目錄 `scripts/`），
   理論上可能被一起提交。改成 `commit_and_maybe_push()`：add/status/commit
   全部限定在 `[dashboard.html, uniswap-lp-tracker/data]` 這組 pathspec，
   `git commit -- <pathspec>` 這個寫法只會提交指定路徑的變動，即使 index 裡
   有其他路徑被 staged 也不會夾帶。新增 `tests/test_offline.py::TestScopedGitCommit`：
   在隔離的臨時 git repo內，模擬工作目錄已有其他任務的 tracked 修改 + untracked
   檔案，驗證產生的 commit 完全不包含它們，且它們在 commit 後仍留在工作目錄
   （沒被動到）。
2. **白名單外 token 仍外洩數值**：`normalize.py::pool_obj_to_row` 原本
   untrusted_token 只改狀態文字跟 tvl note，`fee_tier_pct`／`current_tick`／
   `pool_liquidity_raw` 等會顯示在表格上的數值仍原樣保留。改成 untrusted 時
   把 `fee_tier_raw`／`fee_tier_pct`／`tick_spacing`／`current_tick`／
   `pool_liquidity_raw` 全部清成 `None`。`TestNormalizeSchema::test_untrusted_token_is_flagged_and_neutered`
   擴充覆蓋這 5 個欄位。
3. **HTTP 200 空 pools 被誤判為 pending**：`load_not_found_rows`（現改名
   `load_no_pool_rows`）原本直接 `continue` 掉 HTTP 200 的結果，不管
   `pools` 是不是空陣列；`build_pending_rows` 因此又把同一組合標成
   「尚未擷取」。新增明確的 `empty_response` 狀態（官方已回應、查無此池，
   非尚未擷取），並修正 `build_pending_rows`/排除判斷的 key 用同一套
   `_pending_key()`（原本兩處欄位名不同導致比對不到，是這個 bug 的根因）。
   新增 `TestEmptyResponseHandling`：用假造的 `latest_raw.json` 驗證
   HTTP 200 空 pools → `empty_response`、HTTP 404 → `not_found`，且完整跑
   `normalize.main()` 後同一組合不會重複出現一筆 `pending`。
4. **未做真實瀏覽器點擊驗證**：見 §5，已用 headless Chrome + CDP 補齊。
5. **Telegram 失敗未回傳 exit code、README 提到未實作的 `--offline-only`**：
   `run_daily_update.py` 現在把 `notify_telegram.py` 的非 0 exit code
   原樣往外傳（`run_telegram_notification()`），排程可以據此判斷「真的沒
   送達」而不是誤判成功。`README.md` 移除了不存在的 `--offline-only` 旗標
   說明，改成準確描述 `normalize.py` 本身沒有 `latest_raw.json` 時就會自動
   離線運作（不需要旗標）。新增 `TestTelegramExitPropagation`：未設定
   Telegram 憑證時驗證 `run_telegram_notification()` 回傳 3、且不連網。

---

## 0.2 第三輪修正（anne 實跑正式 key 後回報的 2 個資料完整性 bug）

anne 用自己的 `UNISWAP_API_KEY` 實跑 230 筆查詢後回報：228 筆 HTTP 200，2 筆
Unichain HTTP 400；且 normalize 後仍有 61 筆卡在 `pending`（理論上 230 筆
全部已回應，不應該有任何 pending）。逐一修正如下：

1. **Unichain `poolReferences` 請求 schema 錯誤（根因：欄位名打錯）**：
   `fetch_pool_info.py::build_queries()` 原本送 `poolReferences: [{"poolReferenceIdentifier": ...}]`，
   但官方 schema 要求的是 `poolReferences[0].referenceIdentifier`；欄位名
   不對，伺服器把它視為空值，回 `400 invalid_argument`（anne 用真實請求
   證實：`RequestValidationError: "poolReferences[0].referenceIdentifier"
   is not allowed to be empty`）。已改成 `{"referenceIdentifier": ...}`。
   新增 `tests/test_offline.py::TestPoolReferenceRequestSchema`，斷言
   special_pool_references 產生的請求 body 用對的欄位名。**此修正尚未經
   真實請求重驗**（本環境無 `UNISWAP_API_KEY`）——需要 anne 帶 key 重跑
   `fetch_pool_info.py` 才能確認 Unichain 這兩筆真的變成 200 或其他非
   schema-error 的結果；下方 §0 的三件事仍需 anne 執行。另外要提醒：
   `0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd` 這個位址依 anne 判斷是
   **錢包位址而非池位**，官方 `pool_info` 端點做不到「錢包→池位」查詢，
   即使 schema 修正後仍很可能查無結果；真正的 V3/V4 wallet position 查詢
   已交由另一張研究卡處理，**不可把這個目標池當成功交付項**。
2. **`covered_keys()` 未正規化 token 順序，導致已回應資料被誤判為 pending
   （根因）**：`normalize.py::covered_keys()` 用 API 回應解析出的
   `token_a_symbol`／`token_b_symbol`（來自回應的 `tokenAddressA`／`B`，
   順序由官方伺服器決定，例如請求 WETH/USDC 時官方可能回
   USDC/WETH）直接組 key，跟 `build_pending_rows()` 用**請求送出時**的
   `base_symbol`／`quote_symbol` 順序組的 key 比對；兩邊順序不保證一致，
   一旦反過來就永遠比對不到，已有 live/fixture 資料的組合會被
   `build_pending_rows()` 重複標成「尚未擷取」（anne 實跑 230 筆全部已
   回應，normalized 卻仍有 61 筆 pending，就是這個 bug）。修正：新增
   `_canonical_pair()`（排序過的 tuple，順序無關），`covered_keys()` 與
   `_pending_key()` 都改用它組 key。用真實 `data/latest_raw.json`（anne
   這輪的原始回應）重跑 `normalize.py`：`pending_rows` 從 `61` 降到 `0`，
   `total_rows` 從 `292` 降到 `231`（消除了同一組合被算兩次的重複列）；
   `live_rows=197`、`fixture_rows=1`、`empty_response_rows=31`、
   `error_rows=2`（就是上面第 1 點的兩筆 Unichain schema 錯誤，等 anne
   重跑後應會變成別的狀態）。新增
   `tests/test_offline.py::TestCoveredKeysIgnoreTokenOrder`（2 個測試：
   單元測 `covered_keys()` 對反轉順序的 row 仍能對上請求 key；整合測
   `normalize.main()` 對反轉順序的模擬回應不會重複產生 pending 列）。

**本輪刻意沒有的動作**：沒有 commit／push anne 這輪真實 key 產生的
`data/latest_raw.json`、`data/snapshot-*.json`、重新產生的
`data/normalized_latest.json` 與 dashboard html——這些是 anne 的資料，
上面第 1 點的 Unichain 修正也還沒被真實請求驗證過，理論上資料尚不完整
（仍有 2 筆 error）。已修正的只是 `scripts/fetch_pool_info.py`、
`scripts/normalize.py`、`tests/test_offline.py` 三個程式檔案 + 這份
`handoff.md`；工作目錄裡上述資料檔仍是**未 commit** 的狀態，供 anne
直接檢視或重跑真實資料後自行決定 commit／push 的時機與內容。

---

## 0.3 第四輪修正（anne 帶正式 key 重跑後，Unichain 仍 400 的第二次根因）

anne 用真實 `UNISWAP_API_KEY` 重跑第三輪修正後的程式碼：228 筆 HTTP
200，Unichain 的 2 筆 V3/V4 poolReference 查詢**仍然** HTTP 400，但這次
官方錯誤訊息換了：`poolReferences[0].chainId must be one of ... 130
...`。代表第三輪只修正了欄位名（`poolReferenceIdentifier` →
`referenceIdentifier`），沒注意到 `poolReferences[0]` 這個物件本身**也
需要自己的 `chainId`**——不能只靠 body 頂層的 `chainId`。

修正：`fetch_pool_info.py::build_queries()` 的 `poolReferences[0]` 現在
同時帶 `referenceIdentifier` 與 `chainId`（值同頂層 `ref["chain_id"]`，
Unichain 是 `130`）：

```python
"poolReferences": [{
    "referenceIdentifier": ref["pool_reference_identifier"],
    "chainId": ref["chain_id"],
}],
```

新增 `tests/test_offline.py::TestPoolReferenceRequestSchema::test_pool_reference_body_includes_nested_chain_id`，
斷言 `poolReferences[0]` 一定帶 `chainId` 且與頂層 `chainId`／查詢的
`chain_id` 一致。**此修正同樣尚未經真實請求重驗**（本環境無
`UNISWAP_API_KEY`）——需要 anne 再帶 key 跑一次 `fetch_pool_info.py`，
確認 Unichain 這兩筆這次真的不再是 schema 相關的 400。

再次提醒（anne 已在第三輪確認、本輪重申，避免被誤判為交付成功）：
`0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd` 是**錢包地址而非池位**，
即使這次 schema 完全修對、伺服器也接受請求格式，`pool_info` 端點依然
**做不到「錢包→池位」查詢**，這兩筆查詢即使拿到 HTTP 200 也很可能是
`pools: []`（`empty_response`），必須照 §0.2 第 3 點的規則正規化為
`empty_response`，**絕對不能**把它標成成功交付的池位或錢包 position。
真正的 V3/V4 wallet position 查詢仍在另一張研究卡的範圍。

本輪只改了 `scripts/fetch_pool_info.py`、`tests/test_offline.py`、這份
`handoff.md`、`handoff.json`；沒有動任何資料檔（anne 的 `data/latest_raw.json`
等仍留在工作目錄未 commit，由 anne 重跑真實請求後自行決定何時
commit/push）。

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
Ran 18 tests in 0.24s
OK
```
涵蓋：fee tier 換算（3000→0.30% 等）、fee APR 公式（合成輸入驗證年化計算）、
token 白名單（跨鏈隔離、大小寫不敏感、未知位址拒絕）、fixture schema 正確性、
untrusted token 攔截（含 5 個數值欄位清空）、`normalize.py` 完整輸出 meta 與
rows 筆數一致性、pending 列數值欄位必須全為 null（防造數迴歸）、HTTP 200
空 pools 正規化為 `empty_response` 而非 pending、隔離 git repo 內驗證 commit
不會夾帶其他任務的異動、Telegram 未設憑證時 exit code 正確傳遞（不連網）。

```
$ node tests/sort_logic_check.mjs
...
總計欄位×方向組合：28，失敗：0
```
對正式輸出的 231 筆真實資料，14 欄 × 正/反向 = 28 組合逐一驗證：排序前後
筆數不變、內容集合不變（id 相同 multiset）、非 null 值單調排序方向正確、
null 一律排最後不受方向影響。**已修過一個真實 bug**：初版把 null 排序判斷放在
方向乘法「之後」，導致降冪排序時 null 反而排到最前面；用這個測試抓到並修正。

**真實瀏覽器點擊驗證（本輪補齊，取代上一輪的 Node.js 純函式替代方案）**：
本機 Hermes `browser_navigate` 工具因 macOS TCC 權限問題仍無法快照使用者的
Chrome profile（`Operation not permitted` 讀取
`~/Library/Application Support/Google/Chrome/Default`；需要使用者在系統設定
給終端機 App「完整磁碟取用權限」才能解，屬使用者動作，不在本任務範圍）。
改用 `tests/browser_click_check.py`：不安裝任何 npm/pip 套件，直接啟動本機
真正安裝的 Google Chrome 154.0.8037.58（`--headless=new` + 獨立乾淨的
`--user-data-dir`，完全不觸碰使用者既有 profile），透過 Chrome DevTools
Protocol（標準 WebSocket，Python 標準函式庫自己實作最小 client）對已發布的
GitHub Pages 頁面送出**真實滑鼠事件**（`Input.dispatchMouseEvent`：
mouseMoved → mousePressed → mouseReleased，含 `scrollIntoView` 處理超出可視
寬度的欄位）逐一點擊 14 個欄位標題 × 正/反向：
```
$ python3 tests/browser_click_check.py
頁面載入完成，總列數（來自 #total-count）：231
OK   [chain_name       ascending ] rows=231 visible=231 total=231 nonNull=231 monotonic=True nulls_last=True
OK   [chain_name       descending] rows=231 visible=231 total=231 nonNull=231 monotonic=True nulls_last=True
...（14 欄 × 正/反向，全部 OK）
OK   [source           ascending ] rows=231 visible=231 total=231 nonNull=1 monotonic=True nulls_last=True
OK   [source           descending] rows=231 visible=231 total=231 nonNull=1 monotonic=True nulls_last=True

總計欄位×方向組合：28，失敗：0
```
每次點擊後在瀏覽器內即時檢查：`aria-sort` 是否正確切換 ascending/descending、
可見列數與合計列數是否維持 231（排序不改變筆數）、非 null 值依欄位型別
（text 用 `String.prototype.localeCompare`、num/bigint/date 依對應轉換）單調
排序，null 一律排最後。`status` 欄顯示的是中文翻譯 label，實際排序依據是英文
raw 值，改用 badge 的 CSS class 還原 raw 值驗證（不能直接比對翻譯後文字）。
過程中也抓到並修正兩個測試腳本本身的假陽性：(a) 表格內容有 `max-width`，
最後幾欄需要 `scrollIntoView` 才點得到；(b) 文字欄位必須用瀏覽器的
`localeCompare` 而非 Python 預設字串比較（兩者對大小寫混合字串的排序規則不同，
例如 "Base" vs "BNB Smart Chain"）。

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
