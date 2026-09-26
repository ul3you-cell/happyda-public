#!/usr/bin/env python3
"""每日更新串接：fetch -> normalize -> build_dashboard -> git commit/push。

排程本身（cron/launchd）不在本腳本範圍內 —— 依 dev-claude профile 的硬性限制，
建立/修改排程是使用者或 @anne 的動作，這裡只提供「跑起來會做什麼」的單一入口。

用法（由 @anne 帶自己的 key 執行，或掛到 anne 的 cron）：
    UNISWAP_API_KEY=xxx python3 run_daily_update.py [--no-push] [--telegram]

--no-push   只產生檔案、本機 commit，不 push（預設會 push 到 origin main）
--telegram  執行完後嘗試呼叫 notify_telegram.py（需另外設定
            TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID；未設定則自動略過並回報，
            不會假裝已送達）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
REPO_ROOT = PROJECT_ROOT.parent


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("UNISWAP_API_KEY"):
        print("ERROR: 未設定 UNISWAP_API_KEY，中止（不會用假資料代替）。", file=sys.stderr)
        return 2

    steps = [
        [sys.executable, str(SCRIPTS_DIR / "fetch_pool_info.py")],
        [sys.executable, str(SCRIPTS_DIR / "normalize.py")],
        [sys.executable, str(SCRIPTS_DIR / "build_dashboard.py")],
    ]
    for step in steps:
        result = run(step, cwd=SCRIPTS_DIR)
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            print(f"步驟失敗：{' '.join(step)}，中止（不 commit/push 半成品資料）。", file=sys.stderr)
            return result.returncode

    dashboard_html = "uniswap-lp-tracker-20260926.html"
    add = run(["git", "add", dashboard_html, "uniswap-lp-tracker/data"], cwd=REPO_ROOT)
    print(add.stdout, add.stderr)

    status = run(["git", "status", "--porcelain"], cwd=REPO_ROOT)
    if not status.stdout.strip():
        print("無資料變動，略過 commit/push。")
        return 0

    commit = run(
        ["git", "commit", "-m", "data: daily Uniswap LP pool_info update"],
        cwd=REPO_ROOT,
    )
    print(commit.stdout, commit.stderr)
    if commit.returncode != 0:
        print("git commit 失敗，中止。", file=sys.stderr)
        return commit.returncode

    if not args.no_push:
        push = run(["git", "push", "origin", "HEAD"], cwd=REPO_ROOT)
        print(push.stdout, push.stderr)
        if push.returncode != 0:
            print("git push 失敗（可能需要重新驗證 GitHub 憑證），中止。", file=sys.stderr)
            return push.returncode
        print("已 push。")

    if args.telegram:
        notify = run([sys.executable, str(SCRIPTS_DIR / "notify_telegram.py")], cwd=SCRIPTS_DIR)
        print(notify.stdout, notify.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
