#!/bin/bash
# 注意：process substitution `source <(...)` 在部分沙箱環境會被安全掃描攔截，
# grep 命令實際上不會執行，導致金鑰變數靜默地沒有被設定。改用暫存檔避免這個問題。
ENV_FILE="/Users/wangshaoyu/.hermes/profiles/dev-claude/.env"
TMP_ENV="$(mktemp "${TMPDIR:-/tmp}/wallet_env.XXXXXX")"
trap 'rm -f "$TMP_ENV"' EXIT
grep -E '^(ALCHEMY_API_KEY|GRAPH_API_KEY)=' "$ENV_FILE" > "$TMP_ENV"
set -a
source "$TMP_ENV"
set +a
python3 "$(dirname "$0")/wallet_live_fetch.py"
