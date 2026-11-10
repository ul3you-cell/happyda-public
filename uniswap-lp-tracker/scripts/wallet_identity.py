"""錢包位址的唯一來源（公開 repo 內不存放完整位址）。

讀取順序：環境變數 UNISWAP_WALLET_ADDRESS → 私有檔
~/.hermes/workspace/private/uniswap-lp/wallet_address.txt。
公開輸出（HTML、JSON、文件）一律只放遮罩形式，例：0x267EE342…1eFd。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PRIVATE_FILE = Path.home() / ".hermes/workspace/private/uniswap-lp/wallet_address.txt"
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def get_wallet_address() -> str:
    """回傳完整位址；找不到回傳空字串（呼叫端自行決定是否中止）。"""
    cand = os.environ.get("UNISWAP_WALLET_ADDRESS", "").strip()
    if not cand and PRIVATE_FILE.exists():
        cand = PRIVATE_FILE.read_text(encoding="utf-8").strip()
    return cand if _ADDR_RE.match(cand) else ""


def mask_wallet(addr: str) -> str:
    """0x + 前 8 碼 … 末 4 碼，例：0x267EE342…1eFd。"""
    return addr[:10] + "…" + addr[-4:] if len(addr) >= 20 else addr


def unmask_for_request(text: str) -> str:
    """送出 API 請求前，把遮罩位址還原成完整位址（僅記憶體內；落檔的 request_body 仍是遮罩）。"""
    full = get_wallet_address()
    return text.replace(mask_wallet(full), full) if full else text
