#!/usr/bin/env python3
"""方案 B（本機排程追蹤）的最小化 SQLite 儲存層。

隱私邊界（照 anne 的更正版說法，不誇大也不假裝）：
- 錢包位址**不進 git、不送自建後端、不寫入 log**。但一旦接上真實的鏈上
  查詢（下一階段的 RPC 整合），你選用的 RPC 供應商（例如 Infura／Alchemy
  等）本來就會看到你的錢包位址與查詢內容——這是打 `eth_call` 這件事本身
  的必然代價，不是這支程式額外洩漏出去的，但也不能假裝完全沒有第三方
  看得到。
- 錢包位址**會**進入本機這支追蹤 process 的記憶體、也會寫進本機這個
  SQLite 檔案（這就是「本機排程追蹤」存在的目的：明天要對得起今天的
  基準，才能算得出每日 delta／7d／30d APR）——不能承諾「完全不進
  process」，那種說法已經被 anne 糾正過一次，不再重複那個錯誤。
- DB 檔案預設路徑在 `~/Documents/Hermes Data/Uniswap LP Tracker/`
  （anne 的選擇，2026-09-26 定案）——**完全在這個 git repo 之外**（結構上
  就不可能被誤 commit），且刻意放在 Finder 看得到的 `Documents` 底下，
  Migration Assistant／Time Machine 遷移或重灌時比較不容易被漏掉（跟
  一開始選的隱藏資料夾 `~/.uniswap_tracker/` 相反，那個位置在搬家時很
  容易被忘記，anne 已否決）。可用環境變數 WALLET_TRACKER_DB_PATH 覆蓋
  （測試會覆蓋成 tempfile 路徑，不會碰到使用者的真實檔案）。
  **選對位置不等於自動備份**——資料夾本身不會被這支程式自動同步到任何
  地方，重灌前仍需使用者自行確認 Documents 有進 Time Machine 或其他
  備份機制（見同資料夾的 README-備份與還原.md）。

Schema 只存「錢包位址＋NFT 部位＋每日 fee 快照」這一張表（anne：
「最小化 SQLite」），不另外存一張衍生的 delta 表——delta／APR 一律在
wallet_apr_calc.py 用查詢結果現算，避免兩份狀態互相漂移。

`fees_accrued_usd` 這個欄位的內容**由呼叫端（未來的 RPC 整合層）**用
fee-growth 差值或唯讀 eth_call 模擬 collect() 算出後寫入；本模組完全
不做「liquidity 佔比」這種比例估算（anne 明確禁止：v3 集中流動性下，
比例法會把收益算歪），只負責存取。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from pathlib import Path

DEFAULT_DB_DIR = Path.home() / "Documents" / "Hermes Data" / "Uniswap LP Tracker"
DEFAULT_DB_PATH = Path(
    os.environ.get("WALLET_TRACKER_DB_PATH", str(DEFAULT_DB_DIR / "wallet_snapshots.sqlite3"))
)
# 2026-09-26 以前的舊預設路徑（隱藏資料夾，anne 已否決：重灌/搬家時容易忘記）。
# 只用來做一次性安全搬移，之後新安裝不會再產生這個路徑。
LEGACY_DB_PATH = Path.home() / ".uniswap_tracker" / "wallet_snapshots.sqlite3"

README_FILENAME = "README-備份與還原.md"
README_CONTENT = """# Uniswap LP Tracker 本機資料夾

這個資料夾存的是「方案 B：本機排程追蹤」的錢包 LP 部位快照，**只在這台電腦
上**，不進任何 git repo、不送任何我們自建的後端伺服器、不寫進任何 log。

## 這裡有什麼

- `wallet_snapshots.sqlite3`：SQLite 資料庫，一張表 `wallet_position_snapshot`，
  每天一筆快照（錢包位址、NFT 部位、當日估算手續費、部位估值）。
  用來算 7 天／30 天 fee APR；資料從你啟用追蹤那天開始累積，滿 7／30 天前
  會明確顯示「尚不足計算」，不會用不足的資料硬湊一個年化數字。

## 隱私邊界（誠實版，不誇大）

- 錢包位址**不會**進 git、**不會**送到我們自建的任何後端伺服器、**不會**
  寫進任何 log 檔。
- 錢包位址**會**進入執行這支排程的本機 process 記憶體，也**會**寫進這個
  SQLite 檔案——這是「本機排程追蹤」能夠算出每日 delta／APR 的必要代價，
  不能承諾「完全不進 process」。
- **啟用真實鏈上查詢後**：你選用的 RPC 供應商（例如 Infura／Alchemy 等）
  會看到你的錢包位址與每一次查詢內容——這是打 `eth_call` 這個動作本身
  的必然代價，不是我們額外送出去的，但也不能假裝「完全沒有第三方看
  得到」。如果在意這點，可以選擇自己架設或使用你信任、有隱私政策承諾
  的 RPC 供應商。

## 備份與還原

- **選對這個位置（`~/Documents/...`）不等於自動備份。** 這支程式不會自動
  把這個資料夾同步到任何雲端或外接硬碟。
- 想備份：確認 macOS 的 Time Machine（或你慣用的備份工具）有把整個
  `~/Documents` 資料夾納入備份範圍即可，這個資料夾會跟著一起備份。
- 重灌或換機前：先確認上一次 Time Machine 備份的時間點晚於你最後一次
  更新這個資料庫的時間，或手動把整個 `Uniswap LP Tracker` 資料夾複製到
  新機器的相同路徑（`~/Documents/Hermes Data/Uniswap LP Tracker/`）。
- 還原後不用做任何額外設定，程式預設路徑就會直接找到這裡的
  `wallet_snapshots.sqlite3` 繼續累積歷史。

## 舊版路徑

2026-09-26 以前，本程式的預設路徑是隱藏資料夾 `~/.uniswap_tracker/`——
已改到這裡，理由是隱藏資料夾在 Migration Assistant／Time Machine 選擇性
還原時比較容易被漏掉。第一次在新路徑啟動時，程式會**自動、安全地**把
舊路徑的資料庫搬過來（先驗證完整性、絕不覆蓋這裡已存在的新檔案），
搬移成功後才會刪除舊檔案。
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_position_snapshot (
  ts INTEGER NOT NULL,               -- unix 秒，每日排程戳的快照時間
  wallet_addr TEXT NOT NULL,         -- 只存在這個本機檔案，不進 git/log/repo
  chain_id INTEGER NOT NULL,
  token_id INTEGER NOT NULL,         -- NonfungiblePositionManager 的 ERC-721 tokenId
  pool_addr TEXT NOT NULL,
  tick_lower INTEGER NOT NULL,
  tick_upper INTEGER NOT NULL,
  liquidity TEXT NOT NULL,           -- 字串存，避免 uint128 精度在 SQLite REAL 上失真
  in_range INTEGER NOT NULL,         -- 0/1
  fees_accrued_usd REAL,             -- 這個快照週期內新產生的手續費（USD）；
                                      -- None 代表這次還沒能算出來（例如剛好卡在
                                      -- RPC 失敗），不能用 0 頂替（0 是「真的沒收
                                      -- 到手續費」的合法值，跟「沒算出來」不同）。
  position_value_usd REAL,           -- 這個部位在快照當下的估值（USD）
  PRIMARY KEY (ts, wallet_addr, chain_id, token_id)
);
"""


def _verify_sqlite_integrity(path: Path) -> bool:
    """搬移前先確認舊檔真的是一個沒壞的 SQLite 檔案，壞檔不搬（留在原地，
    不製造「新位置有一個壞掉但看起來存在」的檔案）。"""
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


class WalletTrackerMigrationError(RuntimeError):
    """搬移舊資料庫失敗時丟出。**Fail-closed**：get_connection() 遇到這個
    狀態一律直接中止、絕不繼續往下建立一個空的新 SQLite 檔案——anne 的
    更正：先前的 fail-open 設計（搬移失敗仍靜默建出空的新 DB）會讓使用者
    誤以為歷史資料消失了，正好違反「搬遷不能忘記資料」這個功能存在的
    目的。"""


def migrate_legacy_db(legacy_path: Path | None = None, new_path: Path | None = None) -> str:
    """一次性安全搬移：舊隱藏路徑 -> 新的 `~/Documents/...` 路徑。

    規則（anne 指定，2026-09-26 更正為 fail-closed）：
      - 新路徑已經有檔案 -> 完全不動，回報 skip（絕不覆蓋）。
      - 舊路徑不存在 -> 沒東西可搬，回報 nothing_to_migrate。
      - 舊路徑存在但檔案損壞（integrity_check 失敗）-> 不搬，留在原地，
        回報 legacy_corrupt_not_migrated；呼叫端（get_connection）看到這個
        狀態時**必須**中止並清楚指出舊檔位置，絕不能默默建立空的新 DB。
      - 驗證通過 -> 先建立新目錄（權限 0700，僅使用者可讀寫執行），
        搬移（`shutil.move`），回報 migrated。
    這個函式本身只回傳狀態字串，不丟例外（方便單獨測試每個分支）；
    fail-closed 的「中止啟動」邏輯放在呼叫端 get_connection()。
    """
    legacy = legacy_path if legacy_path is not None else LEGACY_DB_PATH
    new = new_path if new_path is not None else DEFAULT_DB_PATH

    if new.exists():
        return "skip_new_already_exists"
    if not legacy.exists():
        return "nothing_to_migrate"
    if not _verify_sqlite_integrity(legacy):
        return "legacy_corrupt_not_migrated"

    new.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(new.parent, stat.S_IRWXU)  # 0700：僅目前使用者可讀寫執行
    shutil.move(str(legacy), str(new))
    return "migrated"


def _ensure_readme(target_dir: Path) -> None:
    readme_path = target_dir / README_FILENAME
    if readme_path.exists():
        return  # 不覆蓋使用者可能已經看過/編輯過的版本
    readme_path.write_text(README_CONTENT, encoding="utf-8")


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    using_default = db_path is None
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if using_default:
        # 只有真的走預設路徑（不是測試傳進來的 tempfile 路徑）才做舊路徑
        # 一次性搬移；測試永遠帶明確 db_path，不會觸發這條。
        status = migrate_legacy_db()
        if status == "legacy_corrupt_not_migrated":
            # Fail-closed：絕不繼續往下建立一個空的新 DB 讓人誤以為歷史
            # 資料消失了——中止在這裡，還沒碰新路徑的任何檔案或目錄。
            raise WalletTrackerMigrationError(
                "舊的錢包追蹤資料庫損壞，搬移已中止（fail-closed，沒有建立空的新資料庫）。\n"
                f"  舊檔案位置：{LEGACY_DB_PATH}\n"
                "請先手動檢查／備份／修復這個檔案，再重新啟動追蹤；"
                "或確認這個檔案已經不需要了之後手動刪除，讓下次啟動視為「沒有舊資料」。"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 0700：僅目前使用者可讀寫執行
    if using_default:
        _ensure_readme(path.parent)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600：DB 檔案本身也鎖到僅使用者可讀寫
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_snapshot(
    conn: sqlite3.Connection,
    *,
    ts: int,
    wallet_addr: str,
    chain_id: int,
    token_id: int,
    pool_addr: str,
    tick_lower: int,
    tick_upper: int,
    liquidity: str,
    in_range: bool,
    fees_accrued_usd: float | None,
    position_value_usd: float | None,
) -> None:
    """寫入一筆快照。用 INSERT OR REPLACE：每日排程重跑同一天（同一
    ts/wallet/chain/token_id）視為修正，不視為錯誤——排程本來就可能重跑
    （例如前一次 RPC 失敗中斷），不應該因為主鍵衝突而整支腳本掛掉。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO wallet_position_snapshot
          (ts, wallet_addr, chain_id, token_id, pool_addr, tick_lower, tick_upper,
           liquidity, in_range, fees_accrued_usd, position_value_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts, wallet_addr.lower(), chain_id, token_id, pool_addr.lower(),
            tick_lower, tick_upper, liquidity, 1 if in_range else 0,
            fees_accrued_usd, position_value_usd,
        ),
    )
    conn.commit()


def fetch_position_history(
    conn: sqlite3.Connection, *, wallet_addr: str, chain_id: int, token_id: int
) -> list[dict]:
    """回傳指定部位的完整快照歷史，依 ts 由舊到新排序（給
    wallet_apr_calc.compute_wallet_position_metrics 直接使用）。"""
    cur = conn.execute(
        """
        SELECT ts, tick_lower, tick_upper, liquidity, in_range,
               fees_accrued_usd, position_value_usd
        FROM wallet_position_snapshot
        WHERE wallet_addr = ? AND chain_id = ? AND token_id = ?
        ORDER BY ts ASC
        """,
        (wallet_addr.lower(), chain_id, token_id),
    )
    return [dict(row) for row in cur.fetchall()]


def list_tracked_positions(conn: sqlite3.Connection, *, wallet_addr: str, chain_id: int) -> list[tuple[int, str]]:
    """回傳這個錢包在這條鏈上，目前資料庫裡有紀錄過的所有 (token_id, pool_addr)。"""
    cur = conn.execute(
        """
        SELECT DISTINCT token_id, pool_addr FROM wallet_position_snapshot
        WHERE wallet_addr = ? AND chain_id = ?
        ORDER BY token_id ASC
        """,
        (wallet_addr.lower(), chain_id),
    )
    return [(row["token_id"], row["pool_addr"]) for row in cur.fetchall()]
