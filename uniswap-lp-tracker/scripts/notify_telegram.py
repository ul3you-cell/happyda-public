#!/usr/bin/env python3
"""Telegram 每日更新通知（可驗證 delivery 路徑，目前未實測成功）。

安全規則：TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 只從環境變數讀取，絕不
print/log/寫入檔案。呼叫失敗時明確回傳非 0 並印出「未送達」，不假裝成功。

驗證 delivery 的方式：Telegram Bot API 成功時會回傳
`{"ok": true, "result": {"message_id": <int>, ...}}`；本腳本會把
message_id 印到 stdout（不含 token），呼叫方（run_daily_update.py 或
anne 的 cron 記錄）可用這個 message_id 當作「確實送達」的證據，而不是
單純假設沒有例外就等於送達。

本次 dev-claude 執行環境沒有 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，
未且不應該對外發出任何真實請求；`--dry-run`（預設，未設環境變數時自動生效）
只做參數檢查，不連網。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DASHBOARD_URL = "https://ul3you-cell.github.io/happyda-public/uniswap-lp-tracker-20260926.html"


def send_message(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": False}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("ok"):
                message_id = payload.get("result", {}).get("message_id")
                return True, f"送達，message_id={message_id}"
            return False, f"Telegram API 回傳 ok=false：{payload.get('description')}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}：{e.read().decode('utf-8', errors='replace')}"
    except urllib.error.URLError as e:
        return False, f"連線失敗：{e.reason}"


def main() -> int:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print(
            "SKIP：未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，"
            "不會假裝已送達。請由 anne/使用者提供憑證後再執行本腳本做真實 smoke test。"
        )
        return 3

    text = (
        "Uniswap 多鏈 LP／熱門池儀表板已更新\n"
        f"{DASHBOARD_URL}\n"
        "（由 uniswap-lp-tracker/scripts/run_daily_update.py 自動產生；資料限制見頁內免責聲明）"
    )
    ok, detail = send_message(bot_token, chat_id, text)
    print(("送達" if ok else "未送達") + f"：{detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
