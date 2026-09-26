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
  .b-pending {{ background:var(--pending); }} .b-not_found {{ background:var(--nf); }}
  .b-error, .b-untrusted_token {{ background:var(--bad); }}
  .summary-cards {{ display:flex; gap:10px; flex-wrap:wrap; margin:12px 0 4px; }}
  .card {{ background:#f4f7fb; border:1px solid var(--line); border-radius:10px; padding:8px 14px; font-size:.82rem; }}
  .card b {{ font-size:1.1rem; display:block; color:var(--blue); }}
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
  footer {{ margin-top:1.6rem; color:#667085; font-size:.8rem; border-top:1px solid var(--line); padding-top:1rem; }}
  a {{ color:#0b57d0; overflow-wrap:anywhere; }}
  @media (max-width:700px) {{ body {{ padding:10px; }} main {{ padding:14px; }} }}
</style>
</head>
<body>
<main>
  <h1>Uniswap 多鏈 LP／熱門池唯讀儀表板 <span class="badge b-live">唯讀 · 官方 API</span></h1>
  <p class="meta">產生時間（UTC）：{generated_at} ｜ 端點：<code>{endpoint}</code> ｜
    任務卡 t_bf8f4d3e（委託 @anne，執行 dev-claude）｜ 上游研究 t_6b258dd1</p>

  <div class="summary-cards">
    <div class="card"><b>{total_rows}</b>目標池位總數</div>
    <div class="card"><b>{live_rows}</b>即時資料</div>
    <div class="card"><b>{fixture_rows}</b>離線 fixture</div>
    <div class="card"><b>{pending_rows}</b>尚未擷取</div>
    <div class="card"><b>{not_found_rows}</b>官方確認無此池</div>
    <div class="card"><b>{untrusted_rows}</b>⚠️ 白名單警示</div>
  </div>

  <div class="disclaimer">
    <strong>資料限制與免責聲明（發布前必讀）：</strong>
    <ul>
      <li>官方 Uniswap Liquidity API <code>/lp/pool_info</code> <strong>不提供</strong> USD TVL、24h/7d 成交量、fee APR、歷史時序、
        任何錢包 LP 部位；本頁對應欄位固定顯示「資料源未提供」，<strong>絕不造數</strong>。</li>
      <li>「Liquidity」欄為官方回傳的 <code>poolLiquidity</code> 原始流動性單位（v3/v4 concentrated liquidity L 值），
        <strong>不是 USD TVL</strong>，不同 token pair 之間不可直接比較大小。</li>
      <li>APR 一律為「年化」推算，<strong>非保證報酬</strong>；fee APR（若未來接上 Subgraph 後計算）僅反映手續費收入，
        <strong>不含</strong>代幣漲跌與無常損失（Impermanent Loss）。</li>
      <li>本頁<strong>不顯示個別錢包 LP 部位或未領手續費</strong>；如需查詢特定錢包需另行使用 Subgraph
        <code>positions</code> 或 RPC view 函式，本頁未實作（Liquidity API 無唯讀 position 端點）。</li>
      <li>Active range／tick 邊界僅反映 <code>currentTick</code> 快照當下狀態，會隨市場變動；本頁不做即時輪詢。</li>
      <li>「⚠️ token 不在白名單」列代表官方回應內含本專案 <code>config/pools_targets.json</code> 未預先驗證的合約位址，
        已停用數值顯示，需人工複核（防止假幣/釣魚合約誤植）。</li>
    </ul>
  </div>

  <h2>池位總表（點欄名可依該欄排序，再點一次反向；灰色斜體＝該欄無資料）</h2>
  <div class="table-wrap">
  <table id="pool-table" aria-describedby="footer-count">
    <caption>共 {total_rows} 筆目標池位／查詢。狀態徽章：live=即時、fixture=離線驗證、pending=尚未擷取、not_found=官方確認無此池。</caption>
    <thead>
      <tr>
        <th data-key="chain_name" data-type="text" aria-sort="none">鏈</th>
        <th data-key="protocol" data-type="text" aria-sort="none">協定</th>
        <th data-key="pair_label" data-type="text" aria-sort="none">Token Pair</th>
        <th data-key="fee_tier_pct" data-type="num" aria-sort="none">Fee Tier</th>
        <th data-key="pool_liquidity_raw" data-type="bigint" aria-sort="none">Liquidity（raw，非 USD）</th>
        <th data-key="tvl_usd" data-type="num" aria-sort="none">TVL (USD)</th>
        <th data-key="volume_24h_usd" data-type="num" aria-sort="none">24h Volume</th>
        <th data-key="volume_7d_usd" data-type="num" aria-sort="none">7d Volume</th>
        <th data-key="fee_apr_24h_pct" data-type="num" aria-sort="none">Fee APR 24h</th>
        <th data-key="fee_apr_7d_pct" data-type="num" aria-sort="none">Fee APR 7d</th>
        <th data-key="current_tick" data-type="num" aria-sort="none">Current Tick</th>
        <th data-key="status" data-type="text" aria-sort="none">狀態</th>
        <th data-key="snapshot_time" data-type="date" aria-sort="none">快照時間</th>
        <th data-key="source" data-type="text" aria-sort="none">來源</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
  </div>
  <p class="footer-count" id="footer-count">顯示中：<span id="visible-count">0</span> 筆 ／ 共 <span id="total-count">0</span> 筆（排序不會改變筆數與內容，只改變顯示順序）</p>

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

    function renderRows(data) {{
      tbody.innerHTML = '';
      const frag = document.createDocumentFragment();
      for (const row of data) {{
        const tr = document.createElement('tr');
        const cols = ['chain_name','protocol','pair_label','fee_tier_pct','pool_liquidity_raw','tvl_usd',
                      'volume_24h_usd','volume_7d_usd','fee_apr_24h_pct','fee_apr_7d_pct','current_tick',
                      'status','snapshot_time','source'];
        for (const key of cols) {{
          const th = document.querySelector('th[data-key="' + key + '"]');
          const type = th ? th.dataset.type : 'text';
          const td = document.createElement('td');
          if (type === 'num' || type === 'bigint') td.classList.add('num');
          if (key === 'pool_liquidity_raw') td.classList.add('mono');
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
      document.getElementById('visible-count').textContent = data.length;
      document.getElementById('total-count').textContent = rows.length;
    }}

    function applySort() {{
      if (!sortState.key) {{ renderRows(rows); return; }}
      const th = document.querySelector('th[data-key="' + sortState.key + '"]');
      const type = th.dataset.type;
      const sorted = sortRowsPure(rows, sortState.key, sortState.dir, type);
      renderRows(sorted);
    }}

    document.querySelectorAll('#pool-table thead th').forEach(th => {{
      th.addEventListener('click', () => {{
        const key = th.dataset.key;
        if (sortState.key === key) {{
          sortState.dir *= -1;
        }} else {{
          sortState.key = key;
          sortState.dir = 1;
        }}
        document.querySelectorAll('#pool-table thead th').forEach(h => {{
          h.setAttribute('aria-sort', 'none');
          const old = h.querySelector('.arrow'); if (old) old.remove();
        }});
        th.setAttribute('aria-sort', sortState.dir === 1 ? 'ascending' : 'descending');
        const arrow = document.createElement('span');
        arrow.className = 'arrow';
        arrow.textContent = sortState.dir === 1 ? '▲' : '▼';
        th.appendChild(arrow);
        applySort();
      }});
    }});

    renderRows(rows);
  }})();
  </script>

  <footer>
    資料來源：<a href="{endpoint}" target="_blank" rel="noopener">{endpoint}</a>（官方 Uniswap Liquidity API，需自有 API key，本頁不含 key）。<br>
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
