#!/usr/bin/env python3
"""normalized_latest.json -> 發布用自包含 HTML 儀表板。

輸出至 repo 根目錄（比照站內其他報告採頂層檔，方便 GitHub Pages 連結）：
    ../../uniswap-lp-tracker-20260926.html
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from common import DATA_DIR, ENDPOINT, PROJECT_ROOT

OUTPUT_PATH = PROJECT_ROOT.parent / "uniswap-lp-tracker-20260926.html"

STATUS_LABELS = {
    "live": "即時（官方 API）",
    # 「live」在資料層只代表 pool_info API 有回這個候選池，不代表財務指標
    # （TVL/volume/APR）已經接通——V4、非 Ethereum 鏈目前就是這種狀況。
    # JS 端 fmtCell() 會在 status=='live' 但 tvl_usd 為 null 時改用這個
    # label + 灰色 badge，不與真正接通 TVL/APR 的池子共用綠色「即時」標籤
    # （anne 2026-09-27 review：這樣才不會讓人誤以為財務指標已經可用）。
    "live_unenriched": "池子已發現；TVL／APR 資料源未接通",
    "fixture": "離線 fixture（anne 已驗證，無 key）",
    "pending": "尚未擷取",
    "empty_response": "已查詢，官方確認目前無此池",
    "not_found": "官方確認無此池",
    "error": "查詢錯誤",
    "untrusted_token": "⚠️ token 不在白名單",
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Uniswap 多鏈 LP／熱門池唯讀儀表板 — {generated_date}</title>
<style>
  :root {{ color-scheme: light; --blue:#1a73e8; --ink:#263238; --line:#d9e2ec; --paper:#fff; --bg:#f4f7fb;
           --live:#0f9d58; --fixture:#f4b400; --pending:#90a4ae; --nf:#5f6368; --bad:#d93025; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans TC",sans-serif; line-height:1.6;
          max-width:1280px; margin:0 auto; padding:24px; background:var(--bg); color:var(--ink); }}
  main {{ background:var(--paper); padding:24px; border-radius:14px; box-shadow:0 8px 30px rgba(31,55,80,.08); }}
  h1 {{ color:var(--blue); margin-top:0; font-size:1.5rem; }}
  h2 {{ color:var(--blue); margin-top:1.6rem; border-bottom:2px solid #e8f0fe; padding-bottom:.3rem; font-size:1.15rem; }}
  .meta {{ color:#667085; font-size:.85rem; }}
  .badge {{ display:inline-block; border-radius:999px; padding:2px 9px; font-size:.72rem; color:#fff; }}
  .b-live {{ background:var(--live); }} .b-fixture {{ background:var(--fixture); color:#3c2f00; }}
  .b-live-unenriched {{ background:var(--pending); color:#3c2f00; }}
  .b-pending {{ background:var(--pending); }} .b-not_found, .b-empty_response {{ background:var(--nf); }}
  .b-error, .b-untrusted_token {{ background:var(--bad); }}

  .disclaimer {{ background:#fff8e1; border:1px solid #f2d98a; border-radius:10px; padding:12px 16px; font-size:.85rem; margin:14px 0; }}
  .disclaimer ul {{ margin:.4rem 0 0; padding-left:1.2rem; }}
  table {{ border-collapse: collapse; width:100%; font-size:.82rem; margin-top:10px; }}
  caption {{ text-align:left; font-size:.78rem; color:#667085; padding-bottom:6px; }}
  th, td {{ border:1px solid var(--line); padding:6px 8px; text-align:left; white-space:nowrap; }}
  thead th {{ background:#eef3fb; position:sticky; top:0; cursor:pointer; user-select:none; }}
  thead th:hover {{ background:#e3ecfa; }}
  thead th .arrow {{ font-size:.7rem; margin-left:4px; color:var(--blue); }}
  tbody tr:nth-child(even) {{ background:#fbfcfe; }}
  td.num, td.mono {{ font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.78rem; }}
  .null {{ color:#a3adb8; font-style:italic; }}
  .footer-count {{ margin-top:8px; font-size:.82rem; color:#445; }}
  .table-wrap {{ overflow-x:auto; }}
  .sort-toggle {{ display:flex; gap:8px; margin:10px 0 2px; flex-wrap:wrap; }}
  .toggle-btn {{ font-size:.8rem; padding:6px 12px; border-radius:999px; border:1px solid var(--line);
                 background:#fff; color:var(--ink); cursor:pointer; }}
  .toggle-btn.active {{ background:var(--blue); color:#fff; border-color:var(--blue); }}
  .toggle-btn:disabled {{ cursor:not-allowed; opacity:.55; }}
  .pager-bar {{ display:flex; align-items:center; gap:10px; margin-top:10px; flex-wrap:wrap; }}
  .pager-btn {{ font-size:.8rem; padding:5px 12px; border-radius:6px; border:1px solid var(--line);
                background:#fff; color:var(--ink); cursor:pointer; }}
  .pager-btn:disabled {{ cursor:not-allowed; opacity:.45; }}
  .filter-toggle {{ margin:6px 0 14px; padding:8px 12px; border:1px solid var(--line); border-radius:8px;
                     background:#fbfcfe; font-size:.85rem; }}
  .filter-toggle label {{ display:block; margin:4px 0; cursor:pointer; }}
  .filter-toggle select {{ margin-left:6px; padding:3px 7px; border:1px solid var(--line); border-radius:5px; background:#fff; }}
  .filter-note {{ margin:6px 0 0; color:#667085; font-size:.8rem; }}
  .wallet-box {{ margin:14px 0; padding:12px 14px; border:1px dashed var(--line); border-radius:8px; background:#fbfcfe; }}
  .wallet-note {{ font-size:.82rem; color:#556; margin:6px 0 10px; }}
  .wallet-input-row {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  .wallet-input-row input {{ flex:1; min-width:260px; padding:6px 10px; border:1px solid var(--line);
                             border-radius:6px; font-family:monospace; font-size:.85rem; }}
  .wallet-status {{ font-size:.8rem; margin-top:8px; color:#445; }}
  .wallet-status-ok {{ color:#0a7a3d; }}
  .wallet-status-error {{ color:#b3261e; }}
  .pager-info {{ font-size:.82rem; color:#445; }}
  footer {{ margin-top:1.6rem; color:#667085; font-size:.8rem; border-top:1px solid var(--line); padding-top:1rem; }}
  a {{ color:#0b57d0; overflow-wrap:anywhere; }}
  @media (max-width:700px) {{ body {{ padding:10px; }} main {{ padding:14px; }} }}
</style>
</head>
<body>
<main>
  <h1>Uniswap 多鏈 LP／熱門池唯讀儀表板 <span class="badge b-live">唯讀 · 官方 API</span></h1>
  <p class="meta">產生時間（UTC）：{generated_at} ｜ 資料來源：Uniswap Liquidity API＋The Graph v3 subgraph ｜
    任務卡 t_bf8f4d3e（委託 @anne，執行 dev-claude）｜ 上游研究 t_6b258dd1</p>


  <div class="disclaimer">
    <strong>資料限制與免責聲明（發布前必讀）：</strong>
    <ul>
      <li>官方 Uniswap Liquidity API <code>/lp/pool_info</code> 本身<strong>不提供</strong> USD TVL、成交量或 fee APR；
        本頁已用 The Graph v3 subgraph 補上 <strong>Ethereum v3 池</strong>的 TVL、24h／7d 成交量與池子級 fee APR，
        其他鏈或資料不足的列維持「—」，<strong>絕不造數</strong>。</li>
      <li>官方回傳的 <code>poolLiquidity</code> 是 Uniswap v3 集中流動性的數學參數 L，會同時受 token 數量、
        價格區間與目前價格影響；它<strong>不是幣的顆數，也不是 USD</strong>，不同池之間不能直接比較大小，
        因此本頁不顯示該欄，判斷池子大小請直接看 TVL。</li>
      <li>目前顯示的是<strong>池子級 fee APR</strong>：以完整日 fees 與池子 TVL 年化推算，非保證報酬；
        <strong>不等於你的個人收益</strong>，也不含代幣漲跌與無常損失（Impermanent Loss）。小 TVL 池的年化數字可能極端，需特別審慎。</li>
      <li>下方「我的 LP 部位」表格是唯讀 RPC（eth_call）直接查詢錢包
        <code>0x267EE34200b09Ea8b52D02EeC3300b84985B1eFd</code> 的 Uniswap v3／v4 部位，
        逐鏈查詢＝0 或供應商不支援時會如實顯示區塊與原因。V3 fee 以唯讀
        <code>eth_call</code> 模擬 <code>collect()</code> 取得；V4 poolId 用純 Python
        Ethereum Keccak-256 計算並以 Unichain StateView 交叉驗證，非零 liquidity 部位顯示
        區間內狀態、可取得的 USD 價值及「目前可提領總額」（collect() 模擬結果，
        <strong>含尚未拆分的本金與 fee，不是純可領 fee</strong>——decreaseLiquidity()
        撤出的本金會先寫進同一個 tokensOwed 欄位，要套累計已領＋可提領總額－累計撤出
        本金的恆等式才能還原純 fee 收入，見下方「本期新增收入 Δ」欄）；零流動性歷史部位
        僅保留在內部稽核資料，不列於使用者明細。每日 delta／observed APR 需要至少
        兩筆快照才能算，第一筆快照一律顯示「基準已建立」，不造數字。</li>
      <li><strong>部位內的 token 數量是「目前倉位組成（會隨池價變動，非開倉時存入量）」</strong>：
        V3／V4 集中流動性倉位的兩個 token 比例會隨池子現價在區間內持續漂移，跟當初開倉
        那筆交易存入的數量本來就不會一樣（這是 AMM 的正常物理特性，不是誤差）。
        <strong>Etherscan 顯示的是開倉交易當下的資金流向，不能直接拿來對照目前倉位組成</strong>——
        要核對開倉存入量，請回頭看那筆開倉交易本身的 Transfer／value，不要拿來跟本頁現況數字對帳。</li>
      <li>「⚠️ token 不在白名單」列代表官方回應內含本專案 <code>config/pools_targets.json</code> 未預先驗證的合約位址，
        已停用數值顯示，需人工複核（防止假幣/釣魚合約誤植）。</li>
    </ul>
  </div>

  <div class="wallet-box" id="wallet-box">
    <strong>瀏覽器位址備忘（不會更改上方固定追蹤錢包）：</strong>
    <p class="wallet-note">此欄僅存在本機瀏覽器 <code>localStorage</code>，不會發出網路查詢；
      儀表板上方錢包結果由唯讀排程快照提供。</p>
    <div class="wallet-input-row">
      <input type="text" id="wallet-address-input" placeholder="0x..." spellcheck="false" autocomplete="off">
      <button type="button" id="wallet-address-save-btn" class="pager-btn">儲存</button>
      <button type="button" id="wallet-address-clear-btn" class="pager-btn">清除</button>
    </div>
    <p class="wallet-status" id="wallet-address-status"></p>
  </div>

  <h2>池位總表（點欄名可依該欄排序，再點一次反向；灰色斜體＝該欄無資料）</h2>
  <div class="sort-toggle">
    <button type="button" id="sort-tvl-btn" class="toggle-btn active">依 TVL 排序（預設）</button>
    <button type="button" id="sort-wallet-apr-btn" class="toggle-btn" disabled
      title="錢包 NFT RPC 快照與本機歷史資料庫尚未接通，因此暫無可排序的個人 APR">
      依錢包 APR 排序（功能建置中）</button>
  </div>
  <div class="filter-toggle" id="filter-toggle">
    <label><input type="checkbox" id="filter-hide-nonlive" checked>
      只顯示即時可用的池子（隱藏「已查詢．目前無此池」／「尚未擷取」／「查詢錯誤」等狀態）</label>
    <label><input type="checkbox" id="filter-hide-no-apr" checked>
      只顯示已能算出 7d Fee APR 的池子（隱藏歷史資料不足 7 天的池子——不是壞掉，只是那個池子近期
      poolDayData 天數還不夠，等天數累積到 7 天就會自動出現）</label>
    <label>最低 TVL：
      <select id="filter-min-tvl">
        <option value="0">不限</option>
        <option value="100000">$100K</option>
        <option value="1000000" selected>$1M（預設）</option>
        <option value="10000000">$10M</option>
      </select>
      （小池較容易出現滑價、流動性撤出與 APR 失真；預設不顯示低於 $1M 的池子）
    </label>
    <p class="filter-note" id="filter-note"></p>
  </div>
  <div class="table-wrap">
  <table id="pool-table" aria-describedby="footer-count">
    <caption>符合上方條件的 Uniswap 池子</caption>
    <thead>
      <tr>
        <th data-key="chain_name" data-type="text" aria-sort="none">鏈</th>
        <th data-key="protocol" data-type="text" aria-sort="none">協定</th>
        <th data-key="pair_label" data-type="text" aria-sort="none">Token Pair</th>
        <th data-key="fee_tier_pct" data-type="num" aria-sort="none">Fee Tier</th>
        <th data-key="tvl_usd" data-type="num" aria-sort="none">TVL (USD)</th>
        <th data-key="volume_24h_usd" data-type="num" aria-sort="none">24h Volume</th>
        <th data-key="volume_7d_usd" data-type="num" aria-sort="none">7d Volume</th>
        <th data-key="fee_apr_24h_pct" data-type="num" aria-sort="none">Fee APR 24h</th>
        <th data-key="fee_apr_7d_pct" data-type="num" aria-sort="none">Fee APR 7d</th>
        <th data-key="status" data-type="text" aria-sort="none">狀態</th>
        <th data-key="snapshot_time" data-type="date" aria-sort="none">快照時間</th>
        <th data-key="source" data-type="text" aria-sort="none">來源</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
  </div>
  <p class="footer-count" id="footer-count">本頁顯示：<span id="visible-count">0</span> 筆 ／ 共 <span id="total-count">0</span> 筆（每頁 25 筆，排序只改變順序與分頁內容，不改變總筆數）</p>
  <div class="pager-bar" id="pager"></div>

  <h2>我的 LP 部位（唯讀 RPC 直接查詢錢包 <code>{wallet_address_short}</code>）</h2>
  <p class="filter-note">{wallet_summary_note}</p>
  <div class="table-wrap">
  <table id="wallet-table" aria-describedby="wallet-footer-count">
    <caption>錢包在各鏈的 Uniswap v3／v4 部位（點欄名排序）</caption>
    <thead>
      <tr>
        <th data-key="chain_name" data-type="text" aria-sort="none">鏈</th>
        <th data-key="protocol" data-type="text" aria-sort="none">協定</th>
        <th data-key="pair_label" data-type="text" aria-sort="none">Pair</th>
        <th data-key="fee_tier_pct" data-type="num" aria-sort="none">Fee Tier</th>
        <th data-key="position_status" data-type="text" aria-sort="none">流動性狀態</th>
        <th data-key="position_value_usd" data-type="num" aria-sort="none">部位價值 USD</th>
        <th data-key="fees_owed_usd" data-type="num" aria-sort="none">目前可提領總額 USD<br><small>(含本金，非純fee)</small></th>
        <th data-key="in_range" data-type="text" aria-sort="none">In-range</th>
        <th data-key="delta_24h_usd" data-type="num" aria-sort="none">24h Delta</th>
        <th data-key="observed_apr_7d_pct" data-type="num" aria-sort="none">實測 APR 7d</th>
        <th data-key="observed_apr_30d_pct" data-type="num" aria-sort="none">實測 APR 30d</th>
        <th data-key="snapshot_time" data-type="date" aria-sort="none">查詢時間</th>
        <th data-key="source" data-type="text" aria-sort="none">來源／備註</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
  </div>
  <p class="footer-count" id="wallet-footer-count">本頁顯示：<span id="wallet-visible-count">0</span> 筆 ／ 共 <span id="wallet-total-count">0</span> 筆</p>
  <div class="pager-bar" id="wallet-pager"></div>

  <script type="application/json" id="wallet-data">{wallet_rows_json}</script>
  <script type="application/json" id="pool-data">{rows_json}</script>
  <script>
  /* PURE_SORT_START -- 純函式，無 DOM 依賴，供 tests/ 以 Node.js 直接抽取執行驗證 */
  function compareValues(a, b, type) {{
    // 注意：本函式只比較「兩個非 null 值」；null 的排序位置由 sortRowsPure
    // 在方向乘法「之前」單獨處理，確保 null 永遠排最後，不受 asc/desc 影響。
    if (type === 'bigint') {{
      try {{ const ba = BigInt(a), bb = BigInt(b); return ba < bb ? -1 : (ba > bb ? 1 : 0); }}
      catch (e) {{ return String(a).localeCompare(String(b)); }}
    }}
    if (type === 'num') {{
      const na = Number(a), nb = Number(b);
      return na < nb ? -1 : (na > nb ? 1 : 0);
    }}
    if (type === 'date') {{
      const da = Date.parse(a), db = Date.parse(b);
      return da < db ? -1 : (da > db ? 1 : 0);
    }}
    return String(a).localeCompare(String(b));
  }}

  function isEmptyValue(v) {{
    return v === null || v === undefined || v === '';
  }}

  function sortRowsPure(rows, key, dir, type) {{
    return rows.slice().sort((r1, r2) => {{
      const a = r1[key], b = r2[key];
      const aEmpty = isEmptyValue(a), bEmpty = isEmptyValue(b);
      if (aEmpty && bEmpty) return 0;
      if (aEmpty) return 1;   // null 一律排最後，不受方向影響（不乘 dir）
      if (bEmpty) return -1;
      return compareValues(a, b, type) * dir;
    }});
  }}
  /* PURE_SORT_END */

  /* PURE_FILTER_START -- 純函式，無 DOM 依賴，供 tests/ 以 Node.js 直接抽取執行驗證。
     不刪資料、不改資料，只回傳「哪些列在目前的顯示偏好下該出現」；被過濾掉的列數量
     一律要能被呼叫端算出來顯示給使用者看，不能默默消失（見 opts 回傳的 count）。 */
  function filterRowsPure(rows, opts) {{
    const hideNonLive = !!(opts && opts.hideNonLive);
    const hideNoApr = !!(opts && opts.hideNoApr);
    const minTvlUsd = Math.max(0, Number(opts && opts.minTvlUsd) || 0);
    const visible = rows.filter(r => {{
      if (hideNonLive && r.status !== 'live') return false;
      if (hideNoApr && (r.fee_apr_7d_pct === null || r.fee_apr_7d_pct === undefined)) return false;
      if (minTvlUsd > 0 && (!Number.isFinite(Number(r.tvl_usd)) || Number(r.tvl_usd) < minTvlUsd)) return false;
      return true;
    }});
    return {{ visible: visible, hiddenCount: rows.length - visible.length }};
  }}
  /* PURE_FILTER_END */

  /* PURE_FORMAT_START -- USD 顯示格式純函式，供 tests/ 直接抽取驗證 */
  function trimFixed(value) {{
    return value.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1');
  }}

  function formatUsdCompact(value) {{
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    if (n === 0) return '$0';
    const abs = Math.abs(n);
    if (abs < 0.01) return n > 0 ? '<$0.01' : '>-$0.01';
    const units = [
      [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']
    ];
    for (const [divisor, suffix] of units) {{
      if (abs >= divisor) return '$' + trimFixed(n / divisor) + suffix;
    }}
    return '$' + n.toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
  }}
  /* PURE_FORMAT_END */

  (function() {{
    const rows = JSON.parse(document.getElementById('pool-data').textContent);
    const tbody = document.querySelector('#pool-table tbody');
    const statusLabels = {status_labels_json};
    let sortState = {{ key: null, dir: 1 }};

    function fmtCell(row, key, type) {{
      let v = row[key];
      if (key === 'fee_tier_pct') {{
        return (v === null || v === undefined) ? null : (v.toFixed(2) + '%');
      }}
      if (key === 'fee_apr_24h_pct' || key === 'fee_apr_7d_pct') {{
        return (v === null || v === undefined) ? null : (trimFixed(Number(v)) + '%');
      }}
      if (key === 'tvl_usd' || key === 'volume_24h_usd' || key === 'volume_7d_usd') {{
        if (v === null || v === undefined) return null;
        const exact = '$' + Number(v).toLocaleString('en-US', {{ maximumFractionDigits: 6 }});
        return '<span title="精確值：' + exact + '">' + formatUsdCompact(v) + '</span>';
      }}
      if (key === 'status') {{
        // status==='live' 只代表官方 pool_info API 有回這個候選池，不代表
        // TVL/volume/APR 財務指標已經接通（V4、非 Ethereum 鏈目前正是這種
        // 狀況）。這裡不改資料本身的 status 值（保留原始事實），只在「顯示」
        // 這一層另外挑一個 label／顏色，跟真正財務指標齊全的池子區分開。
        const displayKey = (v === 'live' && row.tvl_usd === null) ? 'live_unenriched' : v;
        const label = statusLabels[displayKey] || displayKey;
        return '<span class="badge b-' + displayKey + '">' + label + '</span>';
      }}
      if (key === 'source') {{
        if (!v) return null;
        return '<span title="' + v.replace(/"/g, '&quot;') + '">' + (v.length > 46 ? v.slice(0, 46) + '…' : v) + '</span>';
      }}
      return v;
    }}

    const PAGE_SIZE = 25;
    let currentPage = 1;
    let currentSorted = rows;
    const hideNonLiveCb = document.getElementById('filter-hide-nonlive');
    const hideNoAprCb = document.getElementById('filter-hide-no-apr');
    const minTvlSelect = document.getElementById('filter-min-tvl');
    const filterNote = document.getElementById('filter-note');

    function getFilteredRows() {{
      const opts = {{
        hideNonLive: hideNonLiveCb ? hideNonLiveCb.checked : false,
        hideNoApr: hideNoAprCb ? hideNoAprCb.checked : false,
        minTvlUsd: minTvlSelect ? Number(minTvlSelect.value) : 0,
      }};
      const {{ visible, hiddenCount }} = filterRowsPure(rows, opts);
      if (filterNote) {{
        filterNote.textContent = hiddenCount > 0
          ? '篩選已套用；下表只顯示符合條件的池子。需要擴大候選時，可取消勾選或調整最低 TVL。'
          : '目前顯示所有原始候選。';
      }}
      return visible;
    }}

    function renderRows(data) {{
      tbody.innerHTML = '';
      const start = (currentPage - 1) * PAGE_SIZE;
      const pageData = data.slice(start, start + PAGE_SIZE);
      const frag = document.createDocumentFragment();
      for (const row of pageData) {{
        const tr = document.createElement('tr');
        const cols = ['chain_name','protocol','pair_label','fee_tier_pct','tvl_usd',
                      'volume_24h_usd','volume_7d_usd','fee_apr_24h_pct','fee_apr_7d_pct',
                      'status','snapshot_time','source'];
        for (const key of cols) {{
          const th = document.querySelector('th[data-key="' + key + '"]');
          const type = th ? th.dataset.type : 'text';
          const td = document.createElement('td');
          if (type === 'num' || type === 'bigint') td.classList.add('num');

          const rendered = fmtCell(row, key, type);
          if (rendered === null || rendered === undefined || rendered === '') {{
            td.innerHTML = '<span class="null">—</span>';
          }} else {{
            td.innerHTML = rendered;
          }}
          tr.appendChild(td);
        }}
        frag.appendChild(tr);
      }}
      tbody.appendChild(frag);
      document.getElementById('visible-count').textContent = pageData.length;
      document.getElementById('total-count').textContent = data.length;
      renderPager(data.length);
    }}

    function renderPager(totalFiltered) {{
      const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
      if (currentPage > totalPages) currentPage = totalPages;
      const pager = document.getElementById('pager');
      pager.innerHTML = '';
      const rangeStart = totalFiltered === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
      const rangeEnd = Math.min(currentPage * PAGE_SIZE, totalFiltered);
      const info = document.createElement('span');
      info.className = 'pager-info';
      info.id = 'pager-info';
      info.textContent = '第 ' + rangeStart + '–' + rangeEnd + ' 筆／共 ' + totalFiltered + ' 筆．第 ' + currentPage + ' / ' + totalPages + ' 頁';
      function mkBtn(label, targetPage, disabled) {{
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        b.disabled = disabled;
        b.className = 'pager-btn';
        b.addEventListener('click', () => {{ currentPage = targetPage; renderRows(currentSorted); }});
        return b;
      }}
      pager.appendChild(mkBtn('« 上一頁', currentPage - 1, currentPage <= 1));
      pager.appendChild(info);
      pager.appendChild(mkBtn('下一頁 »', currentPage + 1, currentPage >= totalPages));
    }}

    function updateSortIndicators() {{
      document.querySelectorAll('#pool-table thead th').forEach(h => {{
        h.setAttribute('aria-sort', 'none');
        const old = h.querySelector('.arrow'); if (old) old.remove();
      }});
      if (!sortState.key) return;
      const th = document.querySelector('th[data-key="' + sortState.key + '"]');
      if (!th) return;
      th.setAttribute('aria-sort', sortState.dir === 1 ? 'ascending' : 'descending');
      const arrow = document.createElement('span');
      arrow.className = 'arrow';
      arrow.textContent = sortState.dir === 1 ? '▲' : '▼';
      th.appendChild(arrow);
    }}

    function applySort() {{
      const filtered = getFilteredRows();
      if (!sortState.key) {{ currentSorted = filtered.slice(); }}
      else {{
        const th = document.querySelector('th[data-key="' + sortState.key + '"]');
        const type = th.dataset.type;
        currentSorted = sortRowsPure(filtered, sortState.key, sortState.dir, type);
      }}
      currentPage = 1;
      renderRows(currentSorted);
    }}

    if (hideNonLiveCb) hideNonLiveCb.addEventListener('change', applySort);
    if (hideNoAprCb) hideNoAprCb.addEventListener('change', applySort);
    if (minTvlSelect) minTvlSelect.addEventListener('change', applySort);

    document.querySelectorAll('#pool-table thead th').forEach(th => {{
      th.addEventListener('click', () => {{
        const key = th.dataset.key;
        if (sortState.key === key) {{
          sortState.dir *= -1;
        }} else {{
          sortState.key = key;
          sortState.dir = 1;
        }}
        document.getElementById('sort-tvl-btn').classList.toggle('active', key === 'tvl_usd');
        updateSortIndicators();
        applySort();
      }});
    }});

    document.getElementById('sort-tvl-btn').addEventListener('click', () => {{
      sortState = {{ key: 'tvl_usd', dir: -1 }};
      document.getElementById('sort-tvl-btn').classList.add('active');
      updateSortIndicators();
      applySort();
    }});
    // 錢包 APR 排序按鈕目前 disabled：錢包 NFT RPC 快照與本機歷史資料庫尚未接通；
    // 等個人 APR 有真實資料後再啟用，屆時可沿用既有分頁／排序邏輯。

    // 預設排序：TVL 由高到低，null（尚未有 TVL 資料的池）永遠置底，不受方向影響
    sortState = {{ key: 'tvl_usd', dir: -1 }};
    updateSortIndicators();
    applySort();
  }})();

  (function() {{
    // 「我的 LP 部位」表格：資料來自後端 wallet_live_fetch.py 唯讀 RPC 查詢結果，
    // 重用上面已定義的 sortRowsPure / compareValues 純函式，邏輯不重寫。
    const wRows = JSON.parse(document.getElementById('wallet-data').textContent);
    const wTbody = document.querySelector('#wallet-table tbody');
    if (!wTbody) return;
    let wSortState = {{ key: null, dir: 1 }};
    let wCurrentPage = 1;
    let wCurrentSorted = wRows;
    const W_PAGE_SIZE = 20;
    const wCols = ['chain_name','protocol','pair_label','fee_tier_pct','position_status',
                   'position_value_usd','fees_owed_usd','in_range','delta_24h_usd',
                   'observed_apr_7d_pct','observed_apr_30d_pct','snapshot_time','source'];

    function wFmtCell(row, key) {{
      let v = row[key];
      if (key === 'fee_tier_pct') return (v === null || v === undefined) ? null : (Number(v).toFixed(2) + '%');
      if (key === 'observed_apr_7d_pct' || key === 'observed_apr_30d_pct') {{
        if (v === null || v === undefined) {{
          return row.base_established ? '<span class="badge">基準已建立</span>' : null;
        }}
        return trimFixed(Number(v)) + '%';
      }}
      if (key === 'position_value_usd' || key === 'fees_owed_usd' || key === 'delta_24h_usd') {{
        if (v === null || v === undefined) {{
          if (key === 'delta_24h_usd' && row.base_established) return '<span class="badge">基準已建立</span>';
          return null;
        }}
        const exact = '$' + Number(v).toLocaleString('en-US', {{ maximumFractionDigits: 6 }});
        return '<span title="精確值：' + exact + '">' + formatUsdCompact(v) + '</span>';
      }}
      if (key === 'in_range') {{
        if (v === null || v === undefined) return null;
        return v ? '<span class="badge b-live">in-range</span>' : '<span class="badge b-not_found">out-of-range</span>';
      }}
      if (key === 'source') {{
        if (!v) return null;
        return '<span title="' + String(v).replace(/"/g, '&quot;') + '">' + (String(v).length > 46 ? String(v).slice(0, 46) + '…' : v) + '</span>';
      }}
      return v;
    }}

    function wRenderRows(data) {{
      wTbody.innerHTML = '';
      const start = (wCurrentPage - 1) * W_PAGE_SIZE;
      const pageData = data.slice(start, start + W_PAGE_SIZE);
      const frag = document.createDocumentFragment();
      for (const row of pageData) {{
        const tr = document.createElement('tr');
        for (const key of wCols) {{
          const th = document.querySelector('#wallet-table th[data-key="' + key + '"]');
          const type = th ? th.dataset.type : 'text';
          const td = document.createElement('td');
          if (type === 'num') td.classList.add('num');
          const rendered = wFmtCell(row, key);
          td.innerHTML = (rendered === null || rendered === undefined || rendered === '') ? '<span class="null">—</span>' : rendered;
          tr.appendChild(td);
        }}
        frag.appendChild(tr);
      }}
      wTbody.appendChild(frag);
      document.getElementById('wallet-visible-count').textContent = pageData.length;
      document.getElementById('wallet-total-count').textContent = data.length;
      wRenderPager(data.length);
    }}

    function wRenderPager(total) {{
      const totalPages = Math.max(1, Math.ceil(total / W_PAGE_SIZE));
      if (wCurrentPage > totalPages) wCurrentPage = totalPages;
      const pager = document.getElementById('wallet-pager');
      pager.innerHTML = '';
      const rangeStart = total === 0 ? 0 : (wCurrentPage - 1) * W_PAGE_SIZE + 1;
      const rangeEnd = Math.min(wCurrentPage * W_PAGE_SIZE, total);
      const info = document.createElement('span');
      info.className = 'pager-info';
      info.textContent = '第 ' + rangeStart + '–' + rangeEnd + ' 筆／共 ' + total + ' 筆．第 ' + wCurrentPage + ' / ' + totalPages + ' 頁';
      function mkBtn(label, targetPage, disabled) {{
        const b = document.createElement('button');
        b.type = 'button'; b.textContent = label; b.disabled = disabled; b.className = 'pager-btn';
        b.addEventListener('click', () => {{ wCurrentPage = targetPage; wRenderRows(wCurrentSorted); }});
        return b;
      }}
      pager.appendChild(mkBtn('« 上一頁', wCurrentPage - 1, wCurrentPage <= 1));
      pager.appendChild(info);
      pager.appendChild(mkBtn('下一頁 »', wCurrentPage + 1, wCurrentPage >= totalPages));
    }}

    document.querySelectorAll('#wallet-table thead th').forEach(th => {{
      th.addEventListener('click', () => {{
        const key = th.dataset.key;
        if (wSortState.key === key) wSortState.dir *= -1; else {{ wSortState.key = key; wSortState.dir = 1; }}
        document.querySelectorAll('#wallet-table thead th').forEach(h => {{
          h.setAttribute('aria-sort', 'none');
          const old = h.querySelector('.arrow'); if (old) old.remove();
        }});
        th.setAttribute('aria-sort', wSortState.dir === 1 ? 'ascending' : 'descending');
        const arrow = document.createElement('span'); arrow.className = 'arrow';
        arrow.textContent = wSortState.dir === 1 ? '▲' : '▼';
        th.appendChild(arrow);
        wCurrentSorted = sortRowsPure(wRows, wSortState.key, wSortState.dir, th.dataset.type);
        wCurrentPage = 1;
        wRenderRows(wCurrentSorted);
      }});
    }});

    wRenderRows(wRows);
  }})();

  (function() {{
    // 錢包位址輸入：純前端 localStorage，不送出到任何伺服器／log／repo（見
    // research 的建議：位址雖非 secret，但足以被 7x24 監控，比 key 還敏感，
    // 應由瀏覽器端處理，不要伺服器端處理）。下一階段的錢包 LP／每日 fee
    // delta／個人 APR 會讀這裡存的位址直接在瀏覽器端發 RPC 唯讀查詢；本頁
    // 目前只負責存取，完全沒有查詢邏輯、也不會把這個值傳給任何後端腳本。
    const STORAGE_KEY = 'uniswapTrackerWalletAddress';
    const ADDR_RE = /^0x[a-fA-F0-9]{{40}}$/;
    const input = document.getElementById('wallet-address-input');
    const saveBtn = document.getElementById('wallet-address-save-btn');
    const clearBtn = document.getElementById('wallet-address-clear-btn');
    const status = document.getElementById('wallet-address-status');
    if (!input || !saveBtn || !clearBtn || !status) return;

    function shortAddr(addr) {{
      return addr.slice(0, 6) + '…' + addr.slice(-4);
    }}

    function refreshStatus() {{
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {{
        status.textContent = '已儲存：' + shortAddr(saved) + '（只存在本機瀏覽器 localStorage，不會送出）';
        status.className = 'wallet-status wallet-status-ok';
        input.value = saved;
      }} else {{
        status.textContent = '尚未儲存任何位址（此欄位為下一階段錢包 LP／每日 delta／個人 APR 準備，目前尚未接上查詢邏輯）';
        status.className = 'wallet-status';
      }}
    }}

    saveBtn.addEventListener('click', () => {{
      const v = input.value.trim();
      if (!ADDR_RE.test(v)) {{
        status.textContent = '格式錯誤：需為 0x 開頭 + 40 個十六進位字元的 EVM 位址，未儲存。';
        status.className = 'wallet-status wallet-status-error';
        return;
      }}
      localStorage.setItem(STORAGE_KEY, v);
      refreshStatus();
    }});

    clearBtn.addEventListener('click', () => {{
      localStorage.removeItem(STORAGE_KEY);
      input.value = '';
      refreshStatus();
    }});

    refreshStatus();
  }})();
  </script>

  <footer>
    資料來源：<a href="{endpoint}" target="_blank" rel="noopener">Uniswap Liquidity API</a>（池子探索）＋
    <a href="https://developers.uniswap.org/docs/ecosystem/subgraphs/overview" target="_blank" rel="noopener">The Graph v3 subgraph</a>
    （Ethereum v3 的 TVL／成交量／池子級 fee APR；本頁不含任何 API key）。<br>
    Token 合約位址白名單與來源見 repo <code>uniswap-lp-tracker/config/pools_targets.json</code>；
    正規化與測試程式見 <code>uniswap-lp-tracker/scripts/</code> 與 <code>uniswap-lp-tracker/tests/</code>。<br>
    本頁由 Hermes Agent（@dev-claude，交由 @anne 驗收）產生，僅供個人研究追蹤，非投資建議。<br>
    <a href="index.html">← 回索引</a>
  </footer>
</main>
</body>
</html>
"""


def main() -> int:
    normalized_path = DATA_DIR / "normalized_latest.json"
    with open(normalized_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = data["rows"]
    meta = data["meta"]

    import wallet_live_fetch

    wallet_live_path = DATA_DIR / "wallet_live_latest.json"
    wallet_rows: list[dict] = []
    wallet_address_short = "（尚未查詢）"
    wallet_summary_note = "尚未執行過 wallet_live_fetch.py，本節暫無資料。"
    if wallet_live_path.exists():
        with open(wallet_live_path, "r", encoding="utf-8") as wf:
            wallet_data = json.load(wf)
        wallet_rows = wallet_live_fetch.build_wallet_rows(wallet_data)
        addr = wallet_data.get("wallet_address", "")
        wallet_address_short = (addr[:6] + "…" + addr[-4:]) if addr else "（未知）"
        chains = wallet_data.get("v3", []) + wallet_data.get("v4", [])
        active_total = sum(
            1 for r in chains for pos in r.get("positions", [])
            if (pos.get("active") is True or
                (pos.get("liquidity_raw") is not None and int(pos.get("liquidity_raw", "0")) > 0))
        )
        active_positions = [
            pos for r in chains for pos in r.get("positions", [])
            if pos.get("active") is True or
            (pos.get("liquidity_raw") is not None and int(pos.get("liquidity_raw", "0")) > 0)
        ]
        value_known = [p["position_value_usd"] for p in active_positions if p.get("position_value_usd") is not None]
        fees_known = [p["fees_owed_usd"] for p in active_positions if p.get("fees_owed_usd") is not None]
        value_summary = (f"${sum(value_known):,.2f}" if len(value_known) == active_total
                         else f"N/A（僅 {len(value_known)}/{active_total} 個活躍部位有完整 USD 價格）")
        fee_summary = (f"${sum(fees_known):,.6f}" if len(fees_known) == active_total
                       else f"N/A（僅 {len(fees_known)}/{active_total} 個活躍部位可完整換算 USD）")
        failed_chains = [
            r["chain_name"] for r in chains if r.get("error")
        ]
        note_parts = [
            f"非零 liquidity 活躍部位：{active_total}",
            # anne 2026-10-03 核正：fees_owed_usd 是 collect() 模擬出來的「目前
            # 可提領總額」，含尚未拆分的 decreaseLiquidity() 本金，不是純 fee，
            # 絕不可在摘要文字標成「可領 fee」。
            f"活躍部位總估值：{value_summary}；目前可提領總額（含本金，非純fee）：{fee_summary}",
            f"最後查詢時間：{datetime.fromtimestamp(wallet_data.get('generated_at', 0), tz=timezone.utc).isoformat()}",
        ]
        if failed_chains:
            note_parts.append(f"查詢失敗（詳見下表來源欄的真實錯誤訊息）：{', '.join(failed_chains)}")

        portfolio_delta = wallet_data.get("portfolio_daily_delta") or {}
        if portfolio_delta.get("comparable"):
            delta_usd = portfolio_delta.get("delta_usd")
            delta_pct = portfolio_delta.get("delta_pct")
            prev_date = portfolio_delta.get("previous_snapshot_date")
            pct_str = f"（{delta_pct:+.2f}%）" if delta_pct is not None else ""
            note_parts.append(
                f"較前一可比較日（{prev_date}）總值變化：{delta_usd:+,.2f} USD{pct_str}"
            )
        else:
            note_parts.append(
                "較前一日總值變化：不可比較——" + (portfolio_delta.get("note") or "尚無足夠每日快照")
            )
        wallet_summary_note = "；".join(note_parts)

    html = HTML_TEMPLATE.format(
        generated_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        generated_at=data["generated_at"],
        endpoint=ENDPOINT,
        total_rows=meta["total_rows"],
        live_rows=meta["live_rows"],
        fixture_rows=meta["fixture_rows"],
        pending_rows=meta["pending_rows"],
        empty_response_rows=meta.get("empty_response_rows", 0),
        not_found_rows=meta["not_found_rows"],
        untrusted_rows=meta["untrusted_rows"],
        rows_json=json.dumps(rows, ensure_ascii=False),
        status_labels_json=json.dumps(STATUS_LABELS, ensure_ascii=False),
        wallet_address_short=wallet_address_short,
        wallet_summary_note=wallet_summary_note,
        wallet_rows_json=json.dumps(wallet_rows, ensure_ascii=False),
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"寫入 {OUTPUT_PATH}（{len(rows)} rows, {len(html)} bytes）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
