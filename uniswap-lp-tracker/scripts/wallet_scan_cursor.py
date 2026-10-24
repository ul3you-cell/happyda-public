#!/usr/bin/env python3
"""所有鏈上事件／持倉掃描家族共用的「掃描游標」儲存層。

設計原則（這是先前分散在各處的邏輯集中後的單一實作）：

1. 每個掃描家族（scope）各自記住「上次掃到哪個區塊」與「累積到多少」，
   下一���只掃 `[last_scanned_block + 1, current_block]`，不再從區塊 0 重來。
2. 游標不存在、或與上一筆快照不一致（缺值／倒退／型別錯誤）時，
   **fail-closed 退回完整基準掃描**（from_block=0, base=0），
   絕不把「部分歷史」與「本次增量」混在一起低估。
3. 游標只���對應的成功快照一起寫入；快照沒寫成功的家族，游標不前進，
   下一��重掃同一段，不會跳過未提交的區塊。
4. scope_key 區分「每個部位一份」的家族（V3 事件、V4 活動）與
   「每條鏈一份」的家族（V4 持倉列舉）。

資料只存在本機 SQLite（wallet_snapshots.sqlite3），不進 git、不進公開頁。
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Iterable

SCAN_CURSOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_scan_cursor (
  wallet_addr TEXT NOT NULL,
  chain_id INTEGER NOT NULL,
  scope TEXT NOT NULL,             -- 掃描家族，例如 v3_collect / v3_decrease /
                                   -- v4_activity / v4_enumeration
  scope_key TEXT NOT NULL,         -- 每部位一份的家族放 token_id；每鏈一份放 ''
  last_scanned_block INTEGER,      -- 這次掃描「已完整掃過」的區塊高度
  total0_raw TEXT,                 -- 累積 raw 總數（token0 側）；不適用則 NULL
  total1_raw TEXT,                 -- 累積 raw 總數（token1 側）；不適用則 NULL
  updated_at INTEGER,
  PRIMARY KEY (wallet_addr, chain_id, scope, scope_key)
);

CREATE TABLE IF NOT EXISTS wallet_scan_token (
  wallet_addr TEXT NOT NULL,
  chain_id INTEGER NOT NULL,
  scope TEXT NOT NULL,
  token_id INTEGER NOT NULL,
  first_seen_block INTEGER,
  PRIMARY KEY (wallet_addr, chain_id, scope, token_id)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCAN_CURSOR_SCHEMA)
    conn.commit()


def read_cursor(
    conn: sqlite3.Connection,
    *,
    wallet_addr: str,
    chain_id: int,
    scope: str,
    scope_key: str = "",
) -> dict[str, Any] | None:
    """讀這個家族上次寫入的游標；沒有就回 None（呼叫端須退回基準掃描）。"""
    cur = conn.execute(
        """
        SELECT last_scanned_block, total0_raw, total1_raw, updated_at
        FROM wallet_scan_cursor
        WHERE wallet_addr = ? AND chain_id = ? AND scope = ? AND scope_key = ?
        """,
        (wallet_addr.lower(), chain_id, scope, scope_key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "last_scanned_block": row["last_scanned_block"],
        "total0_raw": row["total0_raw"],
        "total1_raw": row["total1_raw"],
        "updated_at": row["updated_at"],
    }


def write_cursor(
    conn: sqlite3.Connection,
    *,
    wallet_addr: str,
    chain_id: int,
    scope: str,
    scope_key: str = "",
    last_scanned_block: int | None,
    total0_raw: int | None = None,
    total1_raw: int | None = None,
) -> None:
    """寫入游標。只在對應的掃描與快照都成功之後呼叫。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO wallet_scan_cursor
          (wallet_addr, chain_id, scope, scope_key, last_scanned_block,
           total0_raw, total1_raw, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            wallet_addr.lower(), chain_id, scope, scope_key, last_scanned_block,
            None if total0_raw is None else str(total0_raw),
            None if total1_raw is None else str(total1_raw),
            int(time.time()),
        ),
    )
    conn.commit()


def list_tokens(
    conn: sqlite3.Connection, *, wallet_addr: str, chain_id: int, scope: str
) -> list[int]:
    """這個家族已知的 token_id 清單（已驗證或已發現的持倉集合）。"""
    cur = conn.execute(
        """
        SELECT token_id FROM wallet_scan_token
        WHERE wallet_addr = ? AND chain_id = ? AND scope = ?
        ORDER BY token_id ASC
        """,
        (wallet_addr.lower(), chain_id, scope),
    )
    return [int(row["token_id"]) for row in cur.fetchall()]


def add_tokens(
    conn: sqlite3.Connection,
    *,
    wallet_addr: str,
    chain_id: int,
    scope: str,
    token_ids: Iterable[int],
    first_seen_block: int | None = None,
) -> None:
    """把新發現的 token_id 併入已知集合（已存在的不覆寫 first_seen_block）。"""
    conn.executemany(
        """
        INSERT OR IGNORE INTO wallet_scan_token
          (wallet_addr, chain_id, scope, token_id, first_seen_block)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (wallet_addr.lower(), chain_id, scope, int(token_id), first_seen_block)
            for token_id in token_ids
        ],
    )
    conn.commit()


def plan_window(
    *,
    current_block: int | None,
    previous_block: int | None,
    base0: Any = None,
    base1: Any = None,
) -> tuple[int, int, int, bool]:
    """所有掃描家族共用的續掃計畫：回傳 (from_block, base0, base1, already_current)。

    - 缺 previous_block 或 base → 退回完整基準掃描 (0, 0, 0, False)。
    - current_block 比 previous_block 小（節點換了／資料倒退）→ 同樣退回基準。
    - 區塊完全相同 → already_current=True，不做任何網路掃描。
    """
    if previous_block is None or current_block is None:
        return 0, 0, 0, False
    try:
        previous_block = int(previous_block)
        base0 = int(base0)
        base1 = int(base1)
        current_block = int(current_block)
    except (TypeError, ValueError):
        return 0, 0, 0, False
    if previous_block < 0 or current_block < previous_block:
        return 0, 0, 0, False
    if current_block == previous_block:
        return current_block + 1, base0, base1, True
    return previous_block + 1, base0, base1, False
