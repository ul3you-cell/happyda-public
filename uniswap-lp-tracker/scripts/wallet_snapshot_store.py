#!/usr/bin/env python3
"""方案 B（本機排程追蹤）的最小化 SQLite 儲存層。

隱私邊界（照 anne 的更正版說法，不誇大也不假裝）：
- 錢包位址**不進 git、不送外部後端、不寫入 log**。
- 錢包位址**會**進入本機這支追蹤 process 的記憶體、也會寫進本機這個
  SQLite 檔案（這就是「本機排程追蹤」存在的目的：明天要對得起今天的
  基準，才能算得出每日 delta／7d／30d APR）——不能承諾「完全不進
  process」，那種說法已經被 anne 糾正過一次，不再重複那個錯誤。
- DB 檔案預設路徑在使用者 home 目錄下、**完全在這個 git repo 之外**
  （結構上就不可能被誤 commit），可用環境變數 WALLET_TRACKER_DB_PATH 覆蓋
  （測試會覆蓋成 tempfile 路徑，不會碰到使用者的真實檔案）。

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
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.environ.get("WALLET_TRACKER_DB_PATH", str(Path.home() / ".uniswap_tracker" / "wallet_snapshots.sqlite3"))
)

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


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
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
