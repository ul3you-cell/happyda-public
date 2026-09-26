// 真實執行儀表板 HTML 內「原封不動」的排序純函式（PURE_SORT_START..END 區塊），
// 對正式資料跑每個可排序欄位的正向/反向排序，驗證：
//   1. 筆數不變（排序前後長度相等）
//   2. 內容不變（id 集合相同的 multiset，只是順序變了）
//   3. 排序方向正確（非 null 值兩兩比較單調）
//   4. null 一律排最後
//
// 背景：本機 Hermes browser 工具因 macOS TCC 權限問題無法快照 Chrome profile
// （`Operation not permitted` 讀取 ~/Library/Application Support/Google/Chrome/Default），
// 需要使用者在系統設定給終端機 App 完整磁碟取用權限才能修，不在本任務範圍內。
// 這裡改用 Node.js 直接執行「網頁裡實際會跑的那段 JS」，是次佳但誠實的替代驗證方式；
// 待瀏覽器工具可用後，仍建議做一次真人點擊複查（見 handoff.md）。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(__dirname, "..", "..", "uniswap-lp-tracker-20260926.html");
const html = readFileSync(htmlPath, "utf-8");

const pureMatch = html.match(/\/\* PURE_SORT_START[\s\S]*?PURE_SORT_END \*\//);
if (!pureMatch) {
  console.error("FAIL: 找不到 PURE_SORT_START/END 區塊，dashboard 產生流程可能被改過");
  process.exit(1);
}
const dataMatch = html.match(/<script type="application\/json" id="pool-data">([\s\S]*?)<\/script>/);
if (!dataMatch) {
  console.error("FAIL: 找不到 #pool-data JSON");
  process.exit(1);
}
const rows = JSON.parse(dataMatch[1]);

// eval 出 compareValues / sortRowsPure（沙盒內 Function 建構，避免污染全域）
const sandbox = new Function(pureMatch[0] + "\nreturn { compareValues, sortRowsPure };");
const { sortRowsPure } = sandbox();

const columns = [
  ["chain_name", "text"], ["protocol", "text"], ["pair_label", "text"],
  ["fee_tier_pct", "num"], ["pool_liquidity_raw", "bigint"], ["tvl_usd", "num"],
  ["volume_24h_usd", "num"], ["volume_7d_usd", "num"], ["fee_apr_24h_pct", "num"],
  ["fee_apr_7d_pct", "num"], ["current_tick", "num"], ["status", "text"],
  ["snapshot_time", "date"], ["source", "text"],
];

let failures = 0;
const originalIds = new Set(rows.map(r => r.id));

for (const [key, type] of columns) {
  for (const dir of [1, -1]) {
    const sorted = sortRowsPure(rows, key, dir, type);

    if (sorted.length !== rows.length) {
      console.error(`FAIL [${key} dir=${dir}] 筆數改變：${rows.length} -> ${sorted.length}`);
      failures++;
      continue;
    }
    const sortedIds = new Set(sorted.map(r => r.id));
    if (sortedIds.size !== originalIds.size || [...sortedIds].some(id => !originalIds.has(id))) {
      console.error(`FAIL [${key} dir=${dir}] 內容集合改變`);
      failures++;
      continue;
    }

    // 檢查非 null 值之間單調遞增/遞減，且所有 null 排在最後
    const nonNull = sorted.filter(r => r[key] !== null && r[key] !== undefined && r[key] !== "");
    const nullTail = sorted.slice(nonNull.length);
    const nullTailOk = nullTail.every(r => r[key] === null || r[key] === undefined || r[key] === "");
    if (!nullTailOk) {
      console.error(`FAIL [${key} dir=${dir}] null 未排在最後`);
      failures++;
      continue;
    }
    let monotonic = true;
    for (let i = 1; i < nonNull.length; i++) {
      const cmp = compareCheck(nonNull[i - 1][key], nonNull[i][key], type);
      if (dir === 1 && cmp > 0) monotonic = false;
      if (dir === -1 && cmp < 0) monotonic = false;
    }
    if (!monotonic) {
      console.error(`FAIL [${key} dir=${dir}] 排序方向不單調`);
      failures++;
      continue;
    }
    console.log(`OK   [${key} dir=${dir}] rows=${sorted.length} nonNull=${nonNull.length}`);
  }
}

function compareCheck(a, b, type) {
  if (type === "bigint") { try { const x = BigInt(a), y = BigInt(b); return x < y ? -1 : x > y ? 1 : 0; } catch { return String(a).localeCompare(String(b)); } }
  if (type === "num") { const x = Number(a), y = Number(b); return x < y ? -1 : x > y ? 1 : 0; }
  if (type === "date") { const x = Date.parse(a), y = Date.parse(b); return x < y ? -1 : x > y ? 1 : 0; }
  return String(a).localeCompare(String(b));
}

console.log(`\n總計欄位×方向組合：${columns.length * 2}，失敗：${failures}`);
process.exit(failures > 0 ? 1 : 0);
