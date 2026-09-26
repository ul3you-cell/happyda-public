// 真實執行儀表板 HTML 內「原封不動」的過濾純函式（PURE_FILTER_START..END 區塊），
// 針對使用者這輪回報的三個問題驗證：
//   #5「官方確認無此池，為何列出來」 -> hideNonLive 必須把 status!=='live' 全部濾掉
//   #4「沒有 APR 是壞掉還是沒交易量，沒有就別顯示」 -> hideNoApr 必須把
//       fee_apr_7d_pct 為 null/undefined 的列全部濾掉
//   #4 延伸「太小的池子也不敢放」 -> minTvlUsd 必須排除 TVL 低於門檻或無 TVL 的列
// 同時驗證：從不默默丟資料——filterRowsPure 一定要能算出 hiddenCount，且
// visible.length + hiddenCount === 原始筆數，供 UI 誠實顯示「隱藏了幾筆」。
//
// 不用固定的真實資料筆數斷言（那些數字會隨 anne 重新擷取 Graph 資料而變動，
// 硬寫死會變成脆弱測試），只斷言邏輯不變式，跟 sort_logic_check.mjs 同一套
// PURE_*_START/END 抽取手法。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(__dirname, "..", "..", "uniswap-lp-tracker-20260926.html");
const html = readFileSync(htmlPath, "utf-8");

const pureMatch = html.match(/\/\* PURE_FILTER_START[\s\S]*?PURE_FILTER_END \*\//);
if (!pureMatch) {
  console.error("FAIL: 找不到 PURE_FILTER_START/END 區塊，dashboard 產生流程可能被改過");
  process.exit(1);
}
const dataMatch = html.match(/<script type="application\/json" id="pool-data">([\s\S]*?)<\/script>/);
if (!dataMatch) {
  console.error("FAIL: 找不到 #pool-data JSON");
  process.exit(1);
}
const rows = JSON.parse(dataMatch[1]);

const sandbox = new Function(pureMatch[0] + "\nreturn { filterRowsPure };");
const { filterRowsPure } = sandbox();

let failures = 0;

function check(label, cond) {
  if (!cond) {
    console.error(`FAIL: ${label}`);
    failures++;
  } else {
    console.log(`OK   ${label}`);
  }
}

// 1. 兩個選項都關 -> 完全不過濾，visible 就是原始資料（同長度、同 id 集合）
{
  const { visible, hiddenCount } = filterRowsPure(rows, { hideNonLive: false, hideNoApr: false });
  check("兩個過濾都關時不濾掉任何列", visible.length === rows.length && hiddenCount === 0);
}

// 2. hideNonLive=true -> 可見列裡不該再有任何 status !== 'live'（對應使用者 #5）
{
  const { visible, hiddenCount } = filterRowsPure(rows, { hideNonLive: true, hideNoApr: false });
  const leaked = visible.filter(r => r.status !== "live");
  check("hideNonLive 後可見列全部 status==='live'（#5：確認無此池不該再出現）", leaked.length === 0);
  check("hiddenCount 與實際被濾掉的筆數一致", hiddenCount === rows.length - visible.length);
}

// 3. hideNoApr=true -> 可見列裡不該再有 fee_apr_7d_pct 是 null/undefined（對應使用者 #4）
{
  const { visible, hiddenCount } = filterRowsPure(rows, { hideNonLive: false, hideNoApr: true });
  const leaked = visible.filter(r => r.fee_apr_7d_pct === null || r.fee_apr_7d_pct === undefined);
  check("hideNoApr 後可見列全部有 fee_apr_7d_pct（#4：無 APR 的池子不該再出現）", leaked.length === 0);
  check("hiddenCount 與實際被濾掉的筆數一致", hiddenCount === rows.length - visible.length);
}

// 4. 兩個都開（預設狀態）-> 同時滿足兩條規則，且 visible+hidden 守恆（不憑空掉資料/多資料）
{
  const { visible, hiddenCount } = filterRowsPure(rows, { hideNonLive: true, hideNoApr: true });
  const leaked = visible.filter(
    r => r.status !== "live" || r.fee_apr_7d_pct === null || r.fee_apr_7d_pct === undefined
  );
  check("預設（兩個都勾選）時可見列同時滿足兩條規則", leaked.length === 0);
  check("visible.length + hiddenCount === 原始總筆數（不會默默多退少補）", visible.length + hiddenCount === rows.length);
}

// 5. 過濾只改變「哪些列可見」，不能改變任何列本身的內容（不是造假，是隱藏）
{
  const { visible } = filterRowsPure(rows, { hideNonLive: true, hideNoApr: true });
  const byId = new Map(rows.map(r => [r.id, r]));
  const mutated = visible.filter(r => JSON.stringify(r) !== JSON.stringify(byId.get(r.id)));
  check("過濾不修改任何列的原始內容", mutated.length === 0);
}

// 6. minTvlUsd=1,000,000 -> 所有可見列都必須有 TVL 且至少一百萬美元
{
  const { visible, hiddenCount } = filterRowsPure(rows, {
    hideNonLive: true,
    hideNoApr: true,
    minTvlUsd: 1_000_000,
  });
  const leaked = visible.filter(r => !Number.isFinite(Number(r.tvl_usd)) || Number(r.tvl_usd) < 1_000_000);
  check("最低 TVL $1M 後不顯示小池或無 TVL 的列", leaked.length === 0);
  check("TVL 過濾後 visible+hidden 仍等於原始總筆數", visible.length + hiddenCount === rows.length);
}

// 7. minTvlUsd=0 表示不套用 TVL 門檻，向後相容並可讓使用者看回全部
{
  const unfiltered = filterRowsPure(rows, { hideNonLive: false, hideNoApr: false, minTvlUsd: 0 });
  check("最低 TVL 關閉時不濾掉任何列", unfiltered.visible.length === rows.length && unfiltered.hiddenCount === 0);
}

check("頁面提供最低 TVL 選單且預設 $1M", /id="filter-min-tvl"[\s\S]*?<option value="1000000" selected>/.test(html));

console.log(`\n總計檢查：8 組，失敗：${failures}`);
process.exit(failures > 0 ? 1 : 0);
