#!/usr/bin/env python3
"""方案 B 錢包 LP 部位：唯讀 RPC client（Research spec 的 Endpoint 1 + 2）。

範圍（這支檔案故意只做這兩件事，不做更多）：
  Endpoint 1：`balanceOf(address)` + `tokenOfOwnerByIndex(address,uint256)`
              列舉一個錢包持有的 NonfungiblePositionManager NFT tokenId。
  Endpoint 2：`positions(uint256 tokenId)` 拿單一部位的詳細資料
              （token0/token1/fee/tickLower/tickUpper/liquidity/
              feeGrowthInside0LastX128/feeGrowthInside1LastX128/
              tokensOwed0/tokensOwed1）。

尚未做（下一階段，需要先確認 Pool 合約的 `getPool`/`feeGrowthGlobal`/
`ticks` 三個 selector 來源，不能憑記憶硬填）：
  - 用 feeGrowthInside 差值估算「未領手續費目前 USD 價值」。
  - 寫入 wallet_snapshot_store 的每日快照流程。
Research spec 明確要求**不要**用 `eth_call` 模擬 `collect()`（節省節點
throttle 風險），改用 tokensOwed + feeGrowth 估算——這支檔案先把
tokensOwed/feeGrowthInside 這兩個必要輸入的讀取層做完、做對、做測試。

合約與 selector 來源（逐字核對，不憑記憶）：
  - NonfungiblePositionManager (Ethereum mainnet)：
    0xC36442b4a4522E871399CD717aBDD847Ab11FE88
    —— Research 2026-09-26 spec 訊息直接交付，並註明「已從 v3-periphery
    repo 確認」；之後擴多鏈要查 `Uniswap/v3-periphery/deployments`，不可
    憑記憶抄別鏈位址。
  - `positions(uint256)` selector = 0x99fbab88
    —— 2026-09-26 web_search 命中一份審計過的合約原始碼
    （IPOR-Labs/ipor-fusion, UniswapV3Balance.sol）內明確寫著
    `// 0x99fbab88 = bytes4(keccak256("positions(uint256)"))`，逐字核對。
  - `balanceOf(address)` selector = 0x70a08231
    —— ERC-20/721 標準讀取函式，是 EVM 生態最常見、最穩定的 4-byte
    selector 之一（Etherscan「Read Contract」對任何 balanceOf(address)
    永遠顯示這個值），不需另外查證。
  - `tokenOfOwnerByIndex(address,uint256)` selector = 0x2f745c59
    —— ERC-721Enumerable 標準函式，同樣是穩定不變的公開標準 selector。

隱私邊界（沿用 wallet_snapshot_store.py 的誠實版）：
  - 這支檔案的函式**只接受**已經算好的 `wallet_addr` 字串當參數，不去讀
    localStorage、不去讀任何 log／設定檔挖錢包位址；呼叫端（排程主程式）
    負責這件事。
  - `rpc_url` 只從環境變數 `ETH_RPC_URL` 讀（`require_eth_rpc_url()`），
    絕不 print/log 完整 RPC URL——很多 RPC 供應商把 API key 直接嵌在 URL
    路徑裡（例如 `https://mainnet.infura.io/v3/<KEY>`），任何要輸出的錯誤
    訊息都要先過 `_redact_rpc_url()`。
  - 呼叫這些函式本身就會把 wallet_addr 送給你選的 RPC 供應商——這是打
    `eth_call` 這件事本身的必然代價，不是這支程式額外洩漏出去的（同一句
    誠實聲明已經寫在 wallet_snapshot_store.py 的 README 段落）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable, Optional

POSITION_MANAGER_ADDRESS_BY_CHAIN = {
    # Ethereum mainnet；逐字核對來源見上方 module docstring。
    1: "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
}

SELECTOR_BALANCE_OF = "70a08231"
SELECTOR_TOKEN_OF_OWNER_BY_INDEX = "2f745c59"
SELECTOR_POSITIONS = "99fbab88"

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class WalletRpcError(RuntimeError):
    """RPC 呼叫失敗（HTTP 層或節點回 JSON-RPC error 物件）時丟出。"""


def require_eth_rpc_url() -> str:
    """從環境變數讀 RPC endpoint。跟 GRAPH_API_KEY 同規則：只從環境變數讀，
    絕不 hardcode、絕不印出完整內容。"""
    url = os.environ.get("ETH_RPC_URL", "").strip()
    if not url:
        raise WalletRpcError(
            "缺少 ETH_RPC_URL 環境變數。請自行選擇信任的 RPC 供應商"
            "（例如你自己的 Infura／Alchemy 專案），把完整 endpoint URL"
            "存進環境變數，不要寫進任何設定檔或程式碼。"
        )
    return url


def _redact_rpc_url(url: str) -> str:
    """很多 RPC 供應商把 key 直接嵌在 URL 路徑或 query string 裡；任何要
    輸出到訊息/例外的地方一律先過這裡，只留 host，不留 path/query。"""
    m = re.match(r"^(https?://[^/]+)", url)
    return (m.group(1) + "/…（路徑與參數已遮蔽）") if m else "（RPC URL 格式無法解析，已完全遮蔽）"


# ---------------------------------------------------------------------------
# 純函式：ABI 編碼／解碼（無網路、無副作用，可直接單元測試）
# ---------------------------------------------------------------------------

def _validate_address(addr: str) -> str:
    if not _ADDRESS_RE.match(addr):
        raise ValueError(f"不是合法的 0x + 40 hex 位址：{addr!r}")
    return addr.lower()


def encode_address_arg(addr: str) -> str:
    """address 參數編碼成 32-byte（64 hex）ABI slot：12 byte 補零 + 20 byte 位址。"""
    addr = _validate_address(addr)
    return addr[2:].rjust(64, "0")


def encode_uint_arg(value: int) -> str:
    """任何 uintN 參數（在函式參數位置一律佔滿 32 byte slot）編碼成 64 hex。"""
    if value < 0:
        raise ValueError(f"uint 參數不可為負數：{value}")
    return format(value, "x").rjust(64, "0")


def build_calldata(selector_hex: str, *encoded_args: str) -> str:
    """組出完整 calldata：0x + 4-byte selector + 依序串接的 32-byte 參數。"""
    return "0x" + selector_hex + "".join(encoded_args)


def calldata_balance_of(owner_addr: str) -> str:
    return build_calldata(SELECTOR_BALANCE_OF, encode_address_arg(owner_addr))


def calldata_token_of_owner_by_index(owner_addr: str, index: int) -> str:
    return build_calldata(
        SELECTOR_TOKEN_OF_OWNER_BY_INDEX,
        encode_address_arg(owner_addr),
        encode_uint_arg(index),
    )


def calldata_positions(token_id: int) -> str:
    return build_calldata(SELECTOR_POSITIONS, encode_uint_arg(token_id))


def _hex_words(result_hex: str) -> list[str]:
    """把 0x 開頭的 returndata 依 32-byte（64 hex）切成一格一格的 word 清單。"""
    body = result_hex[2:] if result_hex.startswith("0x") else result_hex
    if len(body) % 64 != 0:
        raise ValueError(f"returndata 長度不是 32-byte 的整數倍：{len(body)} hex chars")
    return [body[i : i + 64] for i in range(0, len(body), 64)]


def decode_uint_word(word_hex: str) -> int:
    return int(word_hex, 16)


def decode_int_word(word_hex: str) -> int:
    """ABI 對有號整數（int24/int128/...）一律做全 32-byte 的二補數符號延伸，
    所以直接把整個 word 當成有號 256-bit 整數解就是對的，不用另外對 24-bit
    做遮罩。"""
    raw = int(word_hex, 16)
    if raw >= 2**255:
        raw -= 2**256
    return raw


def decode_address_word(word_hex: str) -> str:
    return "0x" + word_hex[-40:]


def decode_balance_of_result(result_hex: str) -> int:
    (word,) = _hex_words(result_hex)
    return decode_uint_word(word)


def decode_token_of_owner_by_index_result(result_hex: str) -> int:
    (word,) = _hex_words(result_hex)
    return decode_uint_word(word)


def decode_positions_result(result_hex: str) -> dict:
    """依 INonfungiblePositionManager.positions() 的回傳順序解碼（12 個 32-byte
    slot，順序來自官方 interface，見 module docstring 的來源核對）。"""
    words = _hex_words(result_hex)
    if len(words) != 12:
        raise ValueError(f"positions() returndata 應該是 12 個 32-byte slot，實際 {len(words)} 個")
    return {
        "nonce": decode_uint_word(words[0]),
        "operator": decode_address_word(words[1]),
        "token0": decode_address_word(words[2]),
        "token1": decode_address_word(words[3]),
        "fee": decode_uint_word(words[4]),
        "tick_lower": decode_int_word(words[5]),
        "tick_upper": decode_int_word(words[6]),
        "liquidity": decode_uint_word(words[7]),
        "fee_growth_inside_0_last_x128": decode_uint_word(words[8]),
        "fee_growth_inside_1_last_x128": decode_uint_word(words[9]),
        "tokens_owed_0": decode_uint_word(words[10]),
        "tokens_owed_1": decode_uint_word(words[11]),
    }


# ---------------------------------------------------------------------------
# 有網路 I/O 的部分：全部走 `http_post` 這個可注入的參數，離線測試時餵假的
# 實作進去，不必真的打 RPC；預設用 urllib 打真正的 JSON-RPC。
# ---------------------------------------------------------------------------

HttpPost = Callable[[str, dict], dict]


def _default_http_post(rpc_url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "uniswap-lp-tracker-wallet-rpc/1.0 (+https://github.com/ul3you-cell/happyda-public)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise WalletRpcError(f"RPC 連線失敗（{_redact_rpc_url(rpc_url)}）：{exc}") from exc


def eth_call(
    rpc_url: str,
    to_address: str,
    calldata_hex: str,
    block: str = "latest",
    http_post: Optional[HttpPost] = None,
) -> str:
    """打一次唯讀 `eth_call`，回傳 hex returndata（含 0x 前綴）。"""
    poster = http_post or _default_http_post
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to_address, "data": calldata_hex}, block],
    }
    response = poster(rpc_url, payload)
    if "error" in response:
        raise WalletRpcError(
            f"節點回報 eth_call 錯誤（{_redact_rpc_url(rpc_url)}）：{response['error']}"
        )
    result = response.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise WalletRpcError(
            f"eth_call 回應格式不是預期的 hex 字串（{_redact_rpc_url(rpc_url)}）：{result!r}"
        )
    return result


def list_wallet_token_ids(
    rpc_url: str,
    wallet_addr: str,
    chain_id: int = 1,
    http_post: Optional[HttpPost] = None,
) -> list[int]:
    """Endpoint 1：列出一個錢包持有的所有 NonfungiblePositionManager tokenId。"""
    if chain_id not in POSITION_MANAGER_ADDRESS_BY_CHAIN:
        raise WalletRpcError(
            f"chain_id={chain_id} 沒有已核對過的 NonfungiblePositionManager 位址"
            "——缺的鏈就不猜，等 @research 交付並核對來源後再加。"
        )
    to_address = POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    balance_result = eth_call(rpc_url, to_address, calldata_balance_of(wallet_addr), http_post=http_post)
    count = decode_balance_of_result(balance_result)
    token_ids = []
    for i in range(count):
        idx_result = eth_call(
            rpc_url,
            to_address,
            calldata_token_of_owner_by_index(wallet_addr, i),
            http_post=http_post,
        )
        token_ids.append(decode_token_of_owner_by_index_result(idx_result))
    return token_ids


def get_position(
    rpc_url: str,
    token_id: int,
    chain_id: int = 1,
    http_post: Optional[HttpPost] = None,
) -> dict:
    """Endpoint 2：拿單一 tokenId 的部位詳細資料。"""
    if chain_id not in POSITION_MANAGER_ADDRESS_BY_CHAIN:
        raise WalletRpcError(
            f"chain_id={chain_id} 沒有已核對過的 NonfungiblePositionManager 位址"
            "——缺的鏈就不猜，等 @research 交付並核對來源後再加。"
        )
    to_address = POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    result_hex = eth_call(rpc_url, to_address, calldata_positions(token_id), http_post=http_post)
    decoded = decode_positions_result(result_hex)
    decoded["token_id"] = token_id
    decoded["chain_id"] = chain_id
    return decoded
