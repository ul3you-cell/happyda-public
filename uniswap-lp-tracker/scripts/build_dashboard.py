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
      <li>本頁<strong>尚未顯示個別錢包 LP 部位、每日 delta 或個人 APR</strong>；這些功能需要另接
        Uniswap v3 NFT 的唯讀 RPC 快照與本機歷史資料庫，目前仍在建置中。</li>

      <li>「⚠️ token 不在白名單」列代表官方回應內含本專案 <code>config/pools_targets.json</code> 未預先驗證的合約位址，
        已停用數值顯示，需人工複核（防止假幣/釣魚合約誤植）。</li>
    </ul>
  </div>

  <div class="wallet-box" id="wallet-box">
    <strong>錢包位址（選填，僅供之後錢包 LP／每日 delta／個人 APR 功能使用）：</strong>
    <p class="wallet-note">目前這個欄位只把位址存在此瀏覽器的 <code>localStorage</code>，
      <strong>尚未發出任何網路查詢，也不進 log／repo</strong>。錢包追蹤接通後，位址會由本機追蹤程式讀取、
      寫入本機 SQLite，且所選 RPC 供應商會看到位址與查詢內容；查詢功能目前尚未實作。</p>
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
        const label = statusLabels[v] || v;
        return '<span class="badge b-' + v + '">' + label + '</span>';
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
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"寫入 {OUTPUT_PATH}（{len(rows)} rows, {len(html)} bytes）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
