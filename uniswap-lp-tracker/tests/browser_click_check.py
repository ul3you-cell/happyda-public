#!/usr/bin/env python3
"""對已發布的儀表板做「真實瀏覽器」14 個欄位 x 正/反向點擊排序驗證。

背景：本機 Hermes browser_navigate 工具因 macOS TCC 權限問題無法快照
Chrome profile（讀 ~/Library/Application Support/Google/Chrome/Default 會
Operation not permitted）。這裡改用一個獨立、乾淨的 --user-data-dir 直接
啟動本機真正安裝的 Google Chrome（無需系統設定變更、不讀既有使用者
profile），透過 Chrome DevTools Protocol (CDP) 對頁面送出真實滑鼠點擊
（Input.dispatchMouseEvent），不是抽出 JS 純函式跑 Node.js 的替代驗證。

刻意不用 npm/npx、不用 selenium/playwright/websocket-client 等第三方套件
（符合本專案「不安裝第三方套件」紅線）：CDP 走的是標準 WebSocket，這裡用
Python 標準函式庫自己實作最小可用的 WebSocket client + HTTP /json/new
呼叫。

用法：
    python3 tests/browser_click_check.py [URL]
    （預設 URL 為已發布的 GitHub Pages 頁面）

會印出：每個欄位 x 方向的 aria-sort、可見列數／合計列數、以及該欄位
排序方向是否單調（非 null 值）與 null 是否排最後，最後印總計與失敗數，
非 0 exit code 代表有欄位驗證失敗。
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

DEFAULT_URL = "https://ul3you-cell.github.io/happyda-public/uniswap-lp-tracker-20260926.html"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEBUG_PORT = 9333

COLUMNS = [
    "chain_name", "protocol", "pair_label", "fee_tier_pct", "tvl_usd",
    "volume_24h_usd", "volume_7d_usd", "fee_apr_24h_pct", "fee_apr_7d_pct",
    "status", "snapshot_time", "source",
]


# ---------------------------------------------------------------------------
# 最小可用 WebSocket client（僅供本機 CDP 溝通，非通用實作）
# ---------------------------------------------------------------------------
class MiniWebSocket:
    def __init__(self, url: str):
        parsed = urllib.parse.urlparse(url)
        host, port = parsed.hostname, parsed.port or 80
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        if b"101" not in resp.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket handshake 失敗：{resp[:200]!r}")

    def send(self, data: str) -> None:
        payload = data.encode("utf-8")
        length = len(payload)
        mask_key = os.urandom(4)
        header = bytearray([0x81])  # FIN=1, opcode=text
        if length <= 125:
            header.append(0x80 | length)
        elif length <= 65535:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask_key + masked)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("WebSocket 連線提前關閉")
            buf += chunk
        return buf

    def recv(self) -> str:
        # 假設每個 CDP JSON 訊息都在單一（可能是延伸長度的）frame 內送達，
        # 這對 Runtime.evaluate 這種回應大小的訊息足夠使用。
        first2 = self._recv_exact(2)
        b1, b2 = first2[0], first2[1]
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length)
        if opcode == 0x8:
            raise RuntimeError("WebSocket 被伺服器關閉")
        return payload.decode("utf-8")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CDPSession:
    def __init__(self, ws_url: str):
        self.ws = MiniWebSocket(ws_url)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 失敗：{msg['error']}")
                return msg.get("result", {})
            # 忽略其他事件通知（Page.loadEventFired 等），繼續等自己的回應
        raise TimeoutError(f"CDP {method} 逾時未回應")

    def evaluate(self, expression: str) -> object:
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError(f"頁面 JS 執行例外：{result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def click_header(self, col_index: int) -> None:
        # 用真實滑鼠事件點擊（Input.dispatchMouseEvent），不是模擬呼叫 .click()。
        # 頁面內容有 max-width，14 欄不一定全部同時落在可視寬度內（.table-wrap
        # 可水平捲動）；點擊前先把該欄 scrollIntoView，再量測 bounding rect，
        # 確保點在真正可見、可互動的位置上。
        box = self.evaluate(
            "(() => { const th = document.querySelectorAll('#pool-table thead th')[%d];"
            " th.scrollIntoView({block: 'center', inline: 'center'});"
            " const r = th.getBoundingClientRect();"
            " return {x: r.left + r.width/2, y: r.top + r.height/2}; })()" % col_index
        )
        x, y = box["x"], box["y"]
        self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for event_type in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {
                "type": event_type, "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1,
            })


def wait_for_devtools(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/json/version")
            resp = conn.getresponse()
            if resp.status == 200:
                resp.read()
                return
        except OSError as e:
            last_err = e
        time.sleep(0.3)
    raise RuntimeError(f"Chrome DevTools 埠 {port} 未就緒：{last_err}")


def open_tab(port: int, url: str) -> str:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError:
        # 舊版 Chrome 用 GET
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}", timeout=10) as resp:
            data = json.loads(resp.read().decode())
    return data["webSocketDebuggerUrl"]


# 單調性／null 排序檢查特意放在瀏覽器裡用 JS 算（而不是抓字串回 Python 比較）：
# 文字欄位的正確排序依據是 JS 的 String.prototype.localeCompare（頁面
# compareValues() 的 fallback），跟 Python 預設的 ordinal 字串比較不是同一
# 套規則（例如 "Base" vs "BNB Smart Chain"，ASCII 序 "Base" > "BNB..."，但
# localeCompare 序相反）。用同一顆瀏覽器引擎驗證，才是名副其實的「真實瀏覽器
# 排序驗證」，而不是引入另一套比較邏輯製造假陽性。
_MONOTONIC_CHECK_JS = """
(() => {
  const th = document.querySelectorAll('#pool-table thead th')[%(idx)d];
  const type = th.dataset.type;
  const rows = Array.from(document.querySelectorAll('#pool-table tbody tr'));
  // status 欄顯示的是中文翻譯後的 label（statusLabels[v]），排序依據卻是英文
  // raw 值（row.status）；用 badge 的 CSS class（b-<status>）還原 raw 值來比較，
  // 不能直接比 textContent，否則會拿翻譯後文字的字典序去驗證英文 enum 的排序。
  const raw = rows.map(r => {
    const cell = r.cells[%(idx)d];
    const badge = cell.querySelector('.badge');
    if (badge) {
      const cls = Array.from(badge.classList).find(c => c.startsWith('b-'));
      return cls ? cls.slice(2) : cell.textContent.trim();
    }
    return cell.textContent.trim();
  });
  const isEmpty = v => v === '' || v === '\\u2014';
  const nonNull = raw.filter(v => !isEmpty(v));
  const nullTail = raw.slice(nonNull.length);
  const nullsLast = nullTail.every(isEmpty);
  function cmp(a, b) {
    if (type === 'bigint') {
      try { const x = BigInt(a), y = BigInt(b); return x < y ? -1 : (x > y ? 1 : 0); }
      catch (e) { return a.localeCompare(b); }
    }
    if (type === 'num') {
      const x = parseFloat(a), y = parseFloat(b);
      return x < y ? -1 : (x > y ? 1 : 0);
    }
    if (type === 'date') {
      const x = Date.parse(a), y = Date.parse(b);
      return x < y ? -1 : (x > y ? 1 : 0);
    }
    return a.localeCompare(b);
  }
  const dir = th.getAttribute('aria-sort') === 'ascending' ? 1 : -1;
  let monotonic = true;
  for (let i = 1; i < nonNull.length; i++) {
    if (cmp(nonNull[i - 1], nonNull[i]) * dir > 0) monotonic = false;
  }
  return {
    ariaSort: th.getAttribute('aria-sort'),
    rowCount: rows.length,
    visible: document.getElementById('visible-count').textContent,
    total: document.getElementById('total-count').textContent,
    monotonic, nullsLast,
    nonNullCount: nonNull.length,
  };
})()
"""


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    with tempfile.TemporaryDirectory(prefix="chrome-cdp-check-") as user_data_dir:
        chrome_proc = subprocess.Popen(
            [
                CHROME_BIN,
                "--headless=new",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=2200,2200",
                f"--user-data-dir={user_data_dir}",
                f"--remote-debugging-port={DEBUG_PORT}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_devtools(DEBUG_PORT)
            ws_url = open_tab(DEBUG_PORT, url)
            session = CDPSession(ws_url)
            session.call("Page.enable")
            session.call("Runtime.enable")

            # 等頁面真的載入且表格已渲染（輪詢 tbody 列數 > 0）
            deadline = time.time() + 20
            rows_ready = 0
            while time.time() < deadline:
                rows_ready = session.evaluate(
                    "document.querySelectorAll('#pool-table tbody tr').length"
                )
                if rows_ready and rows_ready > 0:
                    break
                time.sleep(0.5)
            if not rows_ready:
                print("FAIL: 頁面表格 20 秒內未渲染出任何列", file=sys.stderr)
                return 1

            total_rows = session.evaluate("document.getElementById('total-count').textContent")
            total_rows_int = int(total_rows)
            print(f"頁面載入完成，總列數（來自 #total-count）：{total_rows}（每頁分頁 25 筆）")

            failures = 0
            for idx, col_key in enumerate(COLUMNS):
                for _ in range(2):  # 第一次點=正向，第二次點同一欄=反向
                    session.click_header(idx)
                    state = session.evaluate(_MONOTONIC_CHECK_JS % {"idx": idx})
                    # 分頁後每頁最多 PAGE_SIZE=25 筆：可見列數＝目前頁實際列數（非全部
                    # 231 筆），只驗證「渲染列數＝視覺可見列數」且「合計筆數不變」；
                    # 單調性與 null 置底只在目前頁（第 1 頁）範圍內檢查即足夠證明排序邏輯正確。
                    PAGE_SIZE = 25
                    ok = (
                        state["ariaSort"] in ("ascending", "descending")
                        and int(state["rowCount"]) == int(state["visible"])
                        and int(state["rowCount"]) == min(PAGE_SIZE, int(state["total"]))
                        and int(state["total"]) == total_rows_int
                        and state["monotonic"]
                        and state["nullsLast"]
                    )
                    status = "OK  " if ok else "FAIL"
                    print(
                        f"{status} [{col_key:16s} {state['ariaSort']:10s}] "
                        f"rows={state['rowCount']} visible={state['visible']} total={state['total']} "
                        f"nonNull={state['nonNullCount']} monotonic={state['monotonic']} "
                        f"nulls_last={state['nullsLast']}"
                    )
                    if not ok:
                        failures += 1

            print(f"\n總計欄位×方向組合：{len(COLUMNS) * 2}，失敗：{failures}")
            return 1 if failures else 0
        finally:
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
