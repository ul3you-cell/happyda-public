#!/usr/bin/env python3
"""每日更新串接：fetch -> normalize -> build_dashboard -> git commit/push。

排程本身（cron/launchd）不在本腳本範圍內 —— 依 dev-claude профile 的硬性限制，
建立/修改排程是使用者或 @anne 的動作，這裡只提供「跑起來會做什麼」的單一入口。

用法（由 @anne 帶自己的 key 執行，或掛到 anne 的 cron）：
    UNISWAP_API_KEY=xxx python3 run_daily_update.py [--no-push] [--telegram]

--no-push   只產生檔案、本機 commit，不 push（預設會 push 到 origin main）
--telegram  執行完後嘗試呼叫 notify_telegram.py（需另外設定
            TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID；未設定則自動略過並回報，
            不會假裝已送達）。notify_telegram.py 的非 0 exit code 會被本腳本
            原樣往外傳（不吞掉），排程據此判斷「Telegram 真的沒送達」。

安全規則（git 提交範圍）：git add / status / commit 一律限定在本案的
DASHBOARD_PATHSPECS（發布用 HTML + uniswap-lp-tracker/data/），不使用
不帶 pathspec 的 `git status --porcelain` 或 `git commit` 判斷/提交整個工作
目錄 —— 工作目錄可能同時存在其他任務尚未提交的異動（例如既有的
obsidian-vault-second-brain-20260925.html、未追蹤的 scripts/），本腳本絕不
能把它們一併提交。見 tests/test_scoped_git_commit.py 的隔離 git repo 驗證。
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

DASHBOARD_HTML_NAME = "uniswap-lp-tracker-20260926.html"
DATA_PATHSPEC = "uniswap-lp-tracker/data"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def dashboard_pathspecs() -> list[str]:
    return [DASHBOARD_HTML_NAME, DATA_PATHSPEC]


def commit_and_maybe_push(
    repo_root: Path,
    pathspecs: list[str],
    message: str,
    push: bool,
    run_fn=run,
) -> int:
    """只針對 pathspecs 做 add/status/commit（可選 push），
    絕不觸碰工作目錄裡 pathspecs 以外的既有異動（tracked 或 untracked）。"""
    add = run_fn(["git", "add", "--", *pathspecs], repo_root)
    print(add.stdout, add.stderr)

    status = run_fn(["git", "status", "--porcelain", "--", *pathspecs], repo_root)
    if not status.stdout.strip():
        print(f"無資料變動（僅檢查 pathspec：{', '.join(pathspecs)}），略過 commit/push。")
        return 0

    commit = run_fn(["git", "commit", "-m", message, "--", *pathspecs], repo_root)
    print(commit.stdout, commit.stderr)
    if commit.returncode != 0:
        print("git commit 失敗，中止。", file=sys.stderr)
        return commit.returncode

    if push:
        push_result = run_fn(["git", "push", "origin", "HEAD"], repo_root)
        print(push_result.stdout, push_result.stderr)
        if push_result.returncode != 0:
            print("git push 失敗（可能需要重新驗證 GitHub 憑證），中止。", file=sys.stderr)
            return push_result.returncode
        print("已 push。")

    return 0


def run_telegram_notification(scripts_dir: Path, run_fn=run) -> int:
    notify = run_fn([sys.executable, str(scripts_dir / "notify_telegram.py")], scripts_dir)
    print(notify.stdout, notify.stderr)
    return notify.returncode


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
        result = run(step, SCRIPTS_DIR)
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            print(f"步驟失敗：{' '.join(step)}，中止（不 commit/push 半成品資料）。", file=sys.stderr)
            return result.returncode

    commit_rc = commit_and_maybe_push(
        REPO_ROOT,
        dashboard_pathspecs(),
        "data: daily Uniswap LP pool_info update",
        push=not args.no_push,
    )
    if commit_rc != 0:
        return commit_rc

    if args.telegram:
        telegram_rc = run_telegram_notification(SCRIPTS_DIR)
        if telegram_rc != 0:
            print(
                f"Telegram 通知未送達（notify_telegram.py exit={telegram_rc}）；"
                "commit/push 已完成，但本次執行整體視為未達成通知目標，"
                "排程請依此 exit code 判斷，不要當作成功。",
                file=sys.stderr,
            )
            return telegram_rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
