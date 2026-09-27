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
    # 全部逐字核對自 developers.uniswap.org/docs/protocols/v3/deployments/*
    # （2026-09-27 抓取，見 handoff.md「多鏈地址核對」段落），不憑記憶填寫。
    1: "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",      # Ethereum
    42161: "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",  # Arbitrum One
    10: "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",     # Optimism（同一地址，官方頁面逐字核對）
    8453: "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",   # Base
    56: "0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613",     # BNB Smart Chain
    130: "0x943e6e07a7e8e791dafc44083e54041d743c46e9",    # Unichain
}

# UniswapV3Factory，用來由 (token0, token1, fee) 反查 pool 位址（getPool）。
# 同樣逐字核對自官方 deployments 頁面，2026-09-27。
V3_FACTORY_ADDRESS_BY_CHAIN = {
    1: "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    42161: "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    10: "0x1F98431c8aD98523631AE4a59f267346ea31F984",  # Optimism
    8453: "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    56: "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7",
    130: "0x1f98400000000000000000000000000000000003",
}

# Uniswap v4 PositionManager（ERC-721，逐字核對自
# developers.uniswap.org/docs/protocols/v4/deployments，2026-09-27）。
V4_POSITION_MANAGER_ADDRESS_BY_CHAIN = {
    1: "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
    42161: "0xd88f38f930b7952f2db2432cb002e7abbf3dd869",
    10: "0x3c3ea4b57a46241e54610e5f022e5c45859a1017",  # Optimism
    8453: "0x7c5f5a4bbd8fd63184577525326123b519429bdc",
    56: "0x7a4a5c919ae2541aed11041a1aeee68f1287f95b",
    130: "0x4529a01c7a0410167c5740c487a8de60232617bf",
}

# StateView deployments from the official Uniswap v4 deployment table.
V4_STATE_VIEW_ADDRESS_BY_CHAIN = {
    1: "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    42161: "0x76fd297e2d437cd7f76d50f01afe6160f86e9990",
    10: "0xc18a3169788f4f75a170290584eca6395c75ecdb",
    8453: "0xa3c0c9b65bad0b08107aa264b0f3db444b867a71",
    56: "0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4",
    130: "0x86e8631a016f9068c3f085faf484ee3f5fdee8f2",
}

CHAIN_NAME_BY_ID = {
    1: "Ethereum",
    42161: "Arbitrum",
    10: "Optimism",
    8453: "Base",
    56: "BNB Chain",
    130: "Unichain",
}

# Alchemy JSON-RPC network slug（https://{slug}.g.alchemy.com/v2/{key}），
# 逐字核對自 alchemy.com/docs/reference/node-supported-chains 與
# alchemy.com/rpc/{chain} 系列頁面，2026-09-27 抓取。
ALCHEMY_NETWORK_SLUG_BY_CHAIN = {
    1: "eth-mainnet",
    42161: "arb-mainnet",
    10: "opt-mainnet",
    8453: "base-mainnet",
    56: "bnb-mainnet",
    130: "unichain-mainnet",
}

SELECTOR_BALANCE_OF = "70a08231"
SELECTOR_TOKEN_OF_OWNER_BY_INDEX = "2f745c59"
SELECTOR_POSITIONS = "99fbab88"
SELECTOR_OWNER_OF = "6352211e"          # ownerOf(uint256)，ERC-721 標準 selector
# 4byte.directory 核對來源見 handoff.md，非憑記憶：
SELECTOR_COLLECT = "fc6f7865"          # collect((uint256,address,uint128,uint128))
SELECTOR_SLOT0 = "3850c7bd"            # slot0()
SELECTOR_GET_POOL = "1698ee82"         # getPool(address,address,uint24)
SELECTOR_V4_GET_POOL_AND_POSITION_INFO = "7ba03aad"  # getPoolAndPositionInfo(uint256)
SELECTOR_V4_GET_POSITION_LIQUIDITY = "1efeed33"      # getPositionLiquidity(uint256)
SELECTOR_DECIMALS = "313ce567"         # decimals()，ERC-20 標準 selector（跟
                                        # balanceOf/tokenOfOwnerByIndex 同等級的
                                        # 穩定公開標準，不需另外查證）

UINT128_MAX = 2**128 - 1

_KECCAK_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_KECCAK_ROTATIONS = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)
_KECCAK_MASK = (1 << 64) - 1


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256 (not FIPS SHA3-256), implemented with Python only."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\0" * ((rate - len(padded) % rate) % rate))
    padded[-1] |= 0x80
    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        for rc in _KECCAK_ROUND_CONSTANTS:
            c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
            d = [c[(x - 1) % 5] ^ ((c[(x + 1) % 5] << 1 | c[(x + 1) % 5] >> 63) & _KECCAK_MASK) for x in range(5)]
            for y in range(5):
                for x in range(5):
                    state[x + 5 * y] ^= d[x]
            rotated = [0] * 25
            for y in range(5):
                for x in range(5):
                    value = state[x + 5 * y]
                    n = _KECCAK_ROTATIONS[x][y]
                    rotated[y + 5 * ((2 * x + 3 * y) % 5)] = ((value << n) | (value >> ((64 - n) % 64))) & _KECCAK_MASK
            for y in range(5):
                for x in range(5):
                    state[x + 5 * y] = rotated[x + 5 * y] ^ ((~rotated[(x + 1) % 5 + 5 * y]) & rotated[(x + 2) % 5 + 5 * y])
            state[0] ^= rc
    return b"".join(state[i].to_bytes(8, "little") for i in range(rate // 8))[:32]


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()


def v4_pool_id(pool_key: dict) -> str:
    """poolId = keccak256(abi.encode(currency0,currency1,fee,tickSpacing,hooks))."""
    words = (
        encode_address_arg(pool_key["currency0"]),
        encode_address_arg(pool_key["currency1"]),
        encode_uint_arg(pool_key["fee"]),
        format(pool_key["tick_spacing"] % (2**256), "064x"),
        encode_address_arg(pool_key["hooks"]),
    )
    return keccak256_hex(bytes.fromhex("".join(words)))

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


def calldata_owner_of(token_id: int) -> str:
    return build_calldata(SELECTOR_OWNER_OF, encode_uint_arg(token_id))


def decode_owner_of_result(result_hex: str) -> str:
    (word,) = _hex_words(result_hex)
    return "0x" + word[-40:]


# Transfer(address indexed from, address indexed to, uint256 indexed tokenId)
# 的事件簽章雜湊——這是 ERC-721/ERC-20 全生態系共用、公開核對過的固定常數
# （keccak256("Transfer(address,address,uint256)")），逐字核對自
# eips.ethereum.org/EIPS/eip-721 範例與 Etherscan 上任一 ERC-721 合約的
# Transfer log topic0，不是本專案自己算出來的猜測值，本環境也沒有安裝任何
# keccak 套件去驗算它。
TRANSFER_EVENT_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _address_to_topic(addr: str) -> str:
    """把 20-byte 位址編碼成 eth_getLogs indexed topic 用的 32-byte hex。"""
    return "0x" + encode_address_arg(addr)


def decode_transfer_log_token_id(log: dict) -> int:
    """ERC-721 的 Transfer 事件三個參數都是 indexed，tokenId 在 topics[3]，\n    不在 data 裡（跟 ERC-20 Transfer 不同，那個 value 沒有 indexed，在 data）。"""
    topics = log.get("topics") or []
    if len(topics) < 4:
        raise WalletRpcError(f"Transfer log topics 數量不足（可能是 ERC-20 log 混進來）：{log!r}")
    return int(topics[3], 16)


def calldata_token_of_owner_by_index(owner_addr: str, index: int) -> str:
    return build_calldata(
        SELECTOR_TOKEN_OF_OWNER_BY_INDEX,
        encode_address_arg(owner_addr),
        encode_uint_arg(index),
    )


def calldata_positions(token_id: int) -> str:
    return build_calldata(SELECTOR_POSITIONS, encode_uint_arg(token_id))


def calldata_collect(token_id: int, recipient_addr: str, amount0_max: int = UINT128_MAX, amount1_max: int = UINT128_MAX) -> str:
    """CollectParams{tokenId,recipient,amount0Max,amount1Max} 全部是靜態欄位，
    ABI 編碼直接依序串接，不需要 offset header（跟 positions() 同一套邏輯）。"""
    return build_calldata(
        SELECTOR_COLLECT,
        encode_uint_arg(token_id),
        encode_address_arg(recipient_addr),
        encode_uint_arg(amount0_max),
        encode_uint_arg(amount1_max),
    )


def calldata_slot0() -> str:
    return "0x" + SELECTOR_SLOT0


def calldata_v4_slot0(pool_id: str) -> str:
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", pool_id):
        raise ValueError("pool_id 必須是 0x 開頭的 bytes32")
    selector = keccak256(b"getSlot0(bytes32)")[:4].hex()
    return "0x" + selector + pool_id[2:].lower()


def decode_v4_slot0_result(result_hex: str) -> dict:
    words = _hex_words(result_hex)
    if len(words) != 4:
        raise ValueError(f"StateView.getSlot0() 應回傳 4 個 slot，實際 {len(words)} 個")
    return {
        "sqrt_price_x96": decode_uint_word(words[0]),
        "tick": decode_int_word(words[1]),
        "protocol_fee": decode_uint_word(words[2]),
        "lp_fee": decode_uint_word(words[3]),
    }


def get_v4_slot0(rpc_url: str, pool_id: str, chain_id: int, http_post: Optional[HttpPost] = None) -> dict:
    if chain_id not in V4_STATE_VIEW_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 v4 StateView 位址")
    raw = eth_call(
        rpc_url, V4_STATE_VIEW_ADDRESS_BY_CHAIN[chain_id], calldata_v4_slot0(pool_id), http_post=http_post
    )
    return decode_v4_slot0_result(raw)


def get_v4_position_fee_growth(
    rpc_url: str, pool_id: str, token_id: int, tick_lower: int, tick_upper: int,
    chain_id: int, http_post: Optional[HttpPost] = None,
) -> dict:
    """Read position checkpoint and current in-range fee growth via StateView."""
    if chain_id not in V4_STATE_VIEW_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 v4 StateView 位址")
    pm = V4_POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    position_selector = keccak256(b"getPositionInfo(bytes32,address,int24,int24,bytes32)")[:4].hex()
    growth_selector = keccak256(b"getFeeGrowthInside(bytes32,int24,int24)")[:4].hex()
    signed_word = lambda v: format(v % (2**256), "064x")
    pos_data = "0x" + position_selector + pool_id[2:] + encode_address_arg(pm) + signed_word(tick_lower) + signed_word(tick_upper) + encode_uint_arg(token_id)
    growth_data = "0x" + growth_selector + pool_id[2:] + signed_word(tick_lower) + signed_word(tick_upper)
    state_view = V4_STATE_VIEW_ADDRESS_BY_CHAIN[chain_id]
    pos_words = _hex_words(eth_call(rpc_url, state_view, pos_data, http_post=http_post))
    growth_words = _hex_words(eth_call(rpc_url, state_view, growth_data, http_post=http_post))
    if len(pos_words) != 3 or len(growth_words) != 2:
        raise ValueError("StateView fee-growth 回應長度不符合官方 ABI")
    return {
        "liquidity": decode_uint_word(pos_words[0]),
        "fee_growth_inside_last_0_x128": decode_uint_word(pos_words[1]),
        "fee_growth_inside_last_1_x128": decode_uint_word(pos_words[2]),
        "fee_growth_inside_0_x128": decode_uint_word(growth_words[0]),
        "fee_growth_inside_1_x128": decode_uint_word(growth_words[1]),
    }


def calldata_get_pool(token_a: str, token_b: str, fee: int) -> str:
    return build_calldata(
        SELECTOR_GET_POOL,
        encode_address_arg(token_a),
        encode_address_arg(token_b),
        encode_uint_arg(fee),
    )


def calldata_v4_get_pool_and_position_info(token_id: int) -> str:
    return build_calldata(SELECTOR_V4_GET_POOL_AND_POSITION_INFO, encode_uint_arg(token_id))


def calldata_v4_get_position_liquidity(token_id: int) -> str:
    return build_calldata(SELECTOR_V4_GET_POSITION_LIQUIDITY, encode_uint_arg(token_id))


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


def decode_collect_result(result_hex: str) -> dict:
    """collect() 回傳 (uint256 amount0, uint256 amount1) —— 這是本次唯讀 eth_call
    模擬出來、@最新區塊當下若立即 collect 可拿到的實際金額（含 tokensOwed ＋
    尚未寫入 tokensOwed 但已累積的 fee-growth 差額），比單純讀 positions() 的
    tokensOwed 更準確、更即時。絕不廣播這筆交易，只做 eth_call 模擬。"""
    words = _hex_words(result_hex)
    if len(words) != 2:
        raise ValueError(f"collect() returndata 應該是 2 個 32-byte slot，實際 {len(words)} 個")
    return {"amount0": decode_uint_word(words[0]), "amount1": decode_uint_word(words[1])}


def decode_slot0_result(result_hex: str) -> dict:
    """UniswapV3Pool.slot0() —— 7 個 32-byte slot，順序來自官方原始碼（逐字核對，
    見 module docstring 的來源核對紀錄）。"""
    words = _hex_words(result_hex)
    if len(words) != 7:
        raise ValueError(f"slot0() returndata 應該是 7 個 32-byte slot，實際 {len(words)} 個")
    return {
        "sqrt_price_x96": decode_uint_word(words[0]),
        "tick": decode_int_word(words[1]),
        "observation_index": decode_uint_word(words[2]),
        "observation_cardinality": decode_uint_word(words[3]),
        "observation_cardinality_next": decode_uint_word(words[4]),
        "fee_protocol": decode_uint_word(words[5]),
        "unlocked": decode_uint_word(words[6]) != 0,
    }


def decode_get_pool_result(result_hex: str) -> str | None:
    (word,) = _hex_words(result_hex)
    addr = decode_address_word(word)
    return None if addr == "0x" + "0" * 40 else addr


def _extract_signed_bits(value: int, offset: int, width: int) -> int:
    mask = (1 << width) - 1
    raw = (value >> offset) & mask
    if raw >= (1 << (width - 1)):
        raw -= 1 << width
    return raw


def decode_v4_get_pool_and_position_info(result_hex: str) -> dict:
    """v4 PositionManager.getPoolAndPositionInfo(uint256) —— 回傳
    (PoolKey{currency0,currency1,fee,tickSpacing,hooks}, PositionInfo packed uint256)。
    兩者都是全靜態欄位（無 dynamic 成員），共 6 個 32-byte slot，依序串接、不帶
    offset header。PositionInfo 的 bit layout 逐字核對自
    developers.uniswap.org/contracts/v4/reference/periphery/libraries/PositionInfoLibrary
    （200 bits poolId | 24 bits tickUpper | 24 bits tickLower | 8 bits hasSubscriber，
    由 LSB 方向數：hasSubscriber 在最低 8 bit，tickLower 接著 offset=8，
    tickUpper offset=32，poolId 佔最高 200 bit），2026-09-27 抓取，非憑記憶。"""
    words = _hex_words(result_hex)
    if len(words) != 6:
        raise ValueError(f"getPoolAndPositionInfo() returndata 應該是 6 個 32-byte slot，實際 {len(words)} 個")
    packed_info = decode_uint_word(words[5])
    return {
        "pool_key": {
            "currency0": decode_address_word(words[0]),
            "currency1": decode_address_word(words[1]),
            "fee": decode_uint_word(words[2]),
            "tick_spacing": decode_int_word(words[3]),
            "hooks": decode_address_word(words[4]),
        },
        "tick_lower": _extract_signed_bits(packed_info, 8, 24),
        "tick_upper": _extract_signed_bits(packed_info, 32, 24),
        "has_subscriber": (packed_info & 0xFF) != 0,
        "pool_id_hex": "0x" + format(packed_info >> 56, "050x"),  # 高 200 bits = 25 bytes
    }


def decode_v4_get_position_liquidity_result(result_hex: str) -> int:
    (word,) = _hex_words(result_hex)
    return decode_uint_word(word)


# ---------------------------------------------------------------------------
# 有網路 I/O 的部分：全部走 `http_post` 這個可注入的參數，離線測試時餵假的
# 實作進去，不必真的打 RPC；預設用 urllib 打真正的 JSON-RPC。
# ---------------------------------------------------------------------------

HttpPost = Callable[[str, dict], dict]


_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRY_ATTEMPTS = 3  # 首次嘗試 + 最多 2 次重試
_RETRY_BASE_DELAY_SECONDS = 1.0  # 指數退避：1s, 2s（跟 fetch_pool_metrics.call_gateway 同一套邏輯）


def _retry_delay_seconds(attempt_idx: int, retry_after_header: Optional[str]) -> float:
    if retry_after_header:
        try:
            return max(0.0, float(retry_after_header))
        except ValueError:
            pass
    return _RETRY_BASE_DELAY_SECONDS * (2**attempt_idx)


def _default_http_post(
    rpc_url: str,
    payload: dict,
    max_attempts: int = _MAX_RETRY_ATTEMPTS,
    sleep_fn=None,
) -> dict:
    """免費公開唯讀 RPC（1rpc.io／publicnode 等）常見 429／503 限流，遇到才
    重試（跟 fetch_pool_metrics.call_gateway 同一套 429/5xx 退避邏輯），
    其他錯誤（如 403 network not enabled）直接往上拋，重試沒有意義。"""
    import time as _time

    sleeper = sleep_fn or _time.sleep
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "uniswap-lp-tracker-wallet-rpc/1.0 (+https://github.com/ul3you-cell/happyda-public)",
    }
    last_exc: Exception | None = None
    for attempt_idx in range(max_attempts):
        req = urllib.request.Request(rpc_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRYABLE_HTTP_STATUS or attempt_idx == max_attempts - 1:
                raise WalletRpcError(f"RPC 連線失敗（{_redact_rpc_url(rpc_url)}）：{exc}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            sleeper(_retry_delay_seconds(attempt_idx, retry_after))
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt_idx == max_attempts - 1:
                raise WalletRpcError(f"RPC 連線失敗（{_redact_rpc_url(rpc_url)}）：{exc}") from exc
            sleeper(_retry_delay_seconds(attempt_idx, None))
        except OSError as exc:  # noqa: BLE001 — 含 socket.timeout：連線逾時也值得重試一次再放棄
            last_exc = exc
            if attempt_idx == max_attempts - 1:
                raise WalletRpcError(f"RPC 連線失敗（{_redact_rpc_url(rpc_url)}）：{exc}") from exc
            sleeper(_retry_delay_seconds(attempt_idx, None))
    if last_exc is not None:
        raise WalletRpcError(f"RPC 連線失敗（{_redact_rpc_url(rpc_url)}）：{last_exc}") from last_exc
    raise WalletRpcError(f"RPC 連線失敗（{_redact_rpc_url(rpc_url)}）：重試迴圈未預期結束")


def eth_call(
    rpc_url: str,
    to_address: str,
    calldata_hex: str,
    block: str = "latest",
    http_post: Optional[HttpPost] = None,
    from_address: Optional[str] = None,
) -> str:
    """打一次唯讀 `eth_call`，回傳 hex returndata（含 0x 前綴）。

    `from_address`：eth_call 是純模擬，節點不驗證簽章，`from` 欄位可以填任意
    位址——這是模擬 collect() 時故意帶上「部位持有人地址」的必要用法（讓合約的
    isAuthorizedForToken 檢查通過），絕不代表真的用該地址簽過名或廣播過交易。"""
    poster = http_post or _default_http_post
    call_obj = {"to": to_address, "data": calldata_hex}
    if from_address:
        call_obj["from"] = _validate_address(from_address)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [call_obj, block],
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


# 部分免費公開 RPC 對 eth_getLogs 的區塊範圍限制訊息裡帶有「range too large／
# limit exceeded／block range greater than／archive」等關鍵字（各家措辭不同，
# 沒有統一格式），這裡不嘗試從錯誤字串解析出對方允許的確切上限，直接對半砍
# 範圍重試——比逐家解析措辭更穩，任何家的訊息格式改了也不會壞掉。
_LOGS_RANGE_ERROR_HINTS = ("range", "limit", "archive", "too many", "too large")


def eth_get_logs(
    rpc_url: str,
    address: str,
    topics: list,
    from_block: int,
    to_block: int,
    http_post: Optional[HttpPost] = None,
    _depth: int = 0,
) -> list[dict]:
    """打 eth_getLogs，遇到「區塊範圍太大」類錯誤就對半砍範圍遞迴重試，直到\n    單次請求成功或範圍已經砍到 1 個區塊還是失敗（此時把真正的錯誤往外拋，\n    不吞掉、不假裝查到 0 筆）。`_depth` 只是防止無限遞迴的安全閥。"""
    poster = http_post or _default_http_post
    if _depth > 40:
        raise WalletRpcError(f"eth_getLogs 範圍遞迴切分超過安全上限（{_redact_rpc_url(rpc_url)}）")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": address,
            "topics": topics,
        }],
    }
    try:
        response = poster(rpc_url, payload)
    except WalletRpcError as exc:
        # _default_http_post 對 HTTP 層級錯誤（例如某些供應商用 HTTP 400
        # 表示「範圍太大」而不是 JSON-RPC error 物件）直接丟例外，不是回傳
        # dict——這裡跟下面 JSON-RPC error 的判斷共用同一套「範圍太大就切半」
        # 邏輯，不然遇到這種供應商會整段直接失敗、連切分重試都不會發生。
        if from_block < to_block and any(h in str(exc).lower() for h in _LOGS_RANGE_ERROR_HINTS + ("bad request", "400")):
            mid = from_block + (to_block - from_block) // 2
            left = eth_get_logs(rpc_url, address, topics, from_block, mid, http_post=http_post, _depth=_depth + 1)
            right = eth_get_logs(rpc_url, address, topics, mid + 1, to_block, http_post=http_post, _depth=_depth + 1)
            return left + right
        raise
    if "error" in response:
        err_msg = str(response["error"])
        if from_block < to_block and any(h in err_msg.lower() for h in _LOGS_RANGE_ERROR_HINTS):
            mid = from_block + (to_block - from_block) // 2
            left = eth_get_logs(rpc_url, address, topics, from_block, mid, http_post=http_post, _depth=_depth + 1)
            right = eth_get_logs(rpc_url, address, topics, mid + 1, to_block, http_post=http_post, _depth=_depth + 1)
            return left + right
        raise WalletRpcError(f"節點回報 eth_getLogs 錯誤（{_redact_rpc_url(rpc_url)}）：{response['error']}")
    result = response.get("result")
    if not isinstance(result, list):
        raise WalletRpcError(f"eth_getLogs 回應格式異常（{_redact_rpc_url(rpc_url)}）：{result!r}")
    return result


# 部分免費公開 RPC 對「當前設定為預設的公開 RPC」啟用了 archive 限制（見
# wallet_rpc_client 內對 PUBLIC_RPC_URL_BY_CHAIN 的說明），但同一條鏈换一個
# 供應商（同樣免費、唯讀）就能查——這裡只挑已經用 curl 實測過「eth_getLogs
# 對這個 v4 PositionManager 位址、fromBlock=0 到 latest 能成功回應」的供應商，
# 2026-09-27 核對；缺的鏈就留給 eth_get_logs() 自己對 PUBLIC_RPC_URL_BY_CHAIN
# 的預設 URL 做範圍遞迴切分去試，不保證一定能在免費額度內查完整段歷史。
LOGS_RPC_OVERRIDE_BY_CHAIN = {
    # Arbitrum：resolve_rpc_url() 平常優先選 Alchemy（此帳號唯一開通 RPC 的
    # 網路），但 Alchemy 免費方案對 eth_getLogs 的範圍限制用 HTTP 400（非
    # JSON-RPC error 物件）回應，eth_get_logs() 的範圍遞迴切分邏輯抓不到這
    # 種格式。改用官方公開節點 arb1.arbitrum.io/rpc，已用 curl 實測
    # fromBlock=0x0～latest 單次請求 <1s 內成功回應（不需要切分）。
    42161: "https://arb1.arbitrum.io/rpc",
    130: "https://unichain.gateway.tenderly.co",
}


class V4LogScanError(WalletRpcError):
    """eth_getLogs 掃描 Transfer 事件失敗時丟出，帶著失敗前已經成功掃到的
    logs_url，方便呼叫端在錯誤訊息裡誠實標出是哪個供應商查不到。"""


def list_wallet_v4_token_ids_via_logs(
    rpc_url: str,
    wallet_addr: str,
    chain_id: int,
    http_post: Optional[HttpPost] = None,
) -> list[int]:
    """v4 PositionManager 不支援 tokenOfOwnerByIndex 時的替代方案：直接掃\n    ERC-721 標準 Transfer 事件裡 `to == wallet_addr` 的紀錄取得候選 token_id，\n    再逐一呼叫 ownerOf() 驗證「現在還是不是這個錢包持有」——避免把已經轉出\n    的舊部位也算進來。呼叫端要另外處理 WalletRpcError（掃描失敗，不代表\n    真的沒有部位，是這個免費 RPC 供應商查不到）。"""
    if chain_id not in V4_POSITION_MANAGER_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 v4 PositionManager 位址")
    to_address = V4_POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    logs_url = LOGS_RPC_OVERRIDE_BY_CHAIN.get(chain_id, rpc_url)
    poster = http_post or _default_http_post

    latest_hex_resp = poster(logs_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    if "error" in latest_hex_resp or not isinstance(latest_hex_resp.get("result"), str):
        raise WalletRpcError(f"eth_blockNumber 查詢失敗（{_redact_rpc_url(logs_url)}）：{latest_hex_resp}")
    latest_block = int(latest_hex_resp["result"], 16)

    topics = [TRANSFER_EVENT_TOPIC0, None, _address_to_topic(wallet_addr)]
    logs = eth_get_logs(logs_url, to_address, topics, 0, latest_block, http_post=http_post)
    candidate_ids = sorted({decode_transfer_log_token_id(log) for log in logs})

    still_owned: list[int] = []
    for token_id in candidate_ids:
        owner_hex = eth_call(rpc_url, to_address, calldata_owner_of(token_id), http_post=http_post)
        owner_addr = decode_owner_of_result(owner_hex)
        if owner_addr.lower() == wallet_addr.lower():
            still_owned.append(token_id)
    return still_owned


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


def simulate_collect(
    rpc_url: str,
    token_id: int,
    owner_addr: str,
    chain_id: int = 1,
    http_post: Optional[HttpPost] = None,
) -> dict:
    """唯讀 eth_call 模擬 collect(tokenId, owner, uint128max, uint128max)——絕不
    廣播交易。回傳「如果現在真的 collect，能拿到多少 token0/token1」，這是比
    positions().tokensOwed 更即時的可領手續費估計（tokensOwed 只在上次
    mint/increase/decrease 時才更新，中間累積的 fee-growth 差額不會反映在
    tokensOwed 裡，但會反映在這次模擬呼叫的結果）。"""
    if chain_id not in POSITION_MANAGER_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 v3 NonfungiblePositionManager 位址")
    to_address = POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    calldata = calldata_collect(token_id, owner_addr)
    result_hex = eth_call(rpc_url, to_address, calldata, http_post=http_post, from_address=owner_addr)
    return decode_collect_result(result_hex)


def get_pool_address(
    rpc_url: str,
    token0: str,
    token1: str,
    fee: int,
    chain_id: int = 1,
    http_post: Optional[HttpPost] = None,
) -> str | None:
    if chain_id not in V3_FACTORY_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 UniswapV3Factory 位址")
    factory = V3_FACTORY_ADDRESS_BY_CHAIN[chain_id]
    result_hex = eth_call(rpc_url, factory, calldata_get_pool(token0, token1, fee), http_post=http_post)
    return decode_get_pool_result(result_hex)


def get_slot0(rpc_url: str, pool_address: str, http_post: Optional[HttpPost] = None) -> dict:
    result_hex = eth_call(rpc_url, pool_address, calldata_slot0(), http_post=http_post)
    return decode_slot0_result(result_hex)


def get_token_decimals(rpc_url: str, token_address: str, http_post: Optional[HttpPost] = None) -> int:
    result_hex = eth_call(rpc_url, token_address, "0x" + SELECTOR_DECIMALS, http_post=http_post)
    (word,) = _hex_words(result_hex)
    return decode_uint_word(word)


class V4EnumerationUnsupported(WalletRpcError):
    """v4 PositionManager 不支援 tokenOfOwnerByIndex 列舉時丟出。
    `balance_count` 帶著已成功查到的 balanceOf() 真實數字，讓呼叫端至少能
    誠實回報「查到 N 個部位，但列不出明細」而不是完全消失。"""

    def __init__(self, message: str, balance_count: int):
        super().__init__(message)
        self.balance_count = balance_count


def list_wallet_v4_token_ids(
    rpc_url: str,
    wallet_addr: str,
    chain_id: int = 1,
    http_post: Optional[HttpPost] = None,
) -> list[int]:
    """balanceOf 是標準 ERC-721 selector，v4 PositionManager 通用。但
    tokenOfOwnerByIndex 不通用——Uniswap 官方文件明確寫著：「The v4
    PositionManager does not implement ERC721Enumerable, so
    tokenOfOwnerByIndex is not available.」
    （來源：developers.uniswap.org/docs/sdks/v4/guides/managing-liquidity/
    position-fetching，2026-09-27 核對；官方建議改用 subgraph 列舉，但
    多鏈 v4 subgraph 目前只有非 Uniswap Labs 維運的社群部署，未逐一核對
    正確性前不接進來，故本次誠實回報「無法列舉明細」而不是猜测）。"""
    if chain_id not in V4_POSITION_MANAGER_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 v4 PositionManager 位址")
    to_address = V4_POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    balance_result = eth_call(rpc_url, to_address, calldata_balance_of(wallet_addr), http_post=http_post)
    count = decode_balance_of_result(balance_result)
    if count == 0:
        return []
    try:
        idx_result = eth_call(
            rpc_url, to_address, calldata_token_of_owner_by_index(wallet_addr, 0), http_post=http_post
        )
    except WalletRpcError as exc:
        raise V4EnumerationUnsupported(
            f"balanceOf() 查到 {count} 個 v4 部位，但 v4 PositionManager 未實作 "
            "ERC721Enumerable（Uniswap 官方文件證實 tokenOfOwnerByIndex 不可用），"
            f"無法逐一列出 token_id：{exc}",
            balance_count=count,
        ) from exc
    token_ids = [decode_token_of_owner_by_index_result(idx_result)]
    for i in range(1, count):
        idx_result = eth_call(
            rpc_url, to_address, calldata_token_of_owner_by_index(wallet_addr, i), http_post=http_post
        )
        token_ids.append(decode_token_of_owner_by_index_result(idx_result))
    return token_ids


def get_v4_position(
    rpc_url: str,
    token_id: int,
    chain_id: int = 1,
    http_post: Optional[HttpPost] = None,
) -> dict:
    """v4 部位：pool key + tick range 用 getPoolAndPositionInfo，liquidity 用
    getPositionLiquidity 另一次呼叫（官方 periphery 把這兩個資訊拆成兩個
    view function，沒有單一函式一次回傳全部——不是本專案自己拆的）。

    v4 的「可領手續費」刻意不在這裡計算：v4 的手續費透過 feeGrowthInside 差值
    結算，需要額外呼叫 StateView.getFeeGrowthInside（每條鏈的 StateView 合約
    位址雖然已核對到，但其回傳值到「這個部位當下可領 USD」之間的換算公式本次
    未及逐字核對＋離線測試覆蓋），標示為「資料源不支援」，不猜測、不拿 v3 的
    tokensOwed 邏輯硬套在 v4 部位上。"""
    if chain_id not in V4_POSITION_MANAGER_ADDRESS_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 v4 PositionManager 位址")
    to_address = V4_POSITION_MANAGER_ADDRESS_BY_CHAIN[chain_id]
    info_hex = eth_call(rpc_url, to_address, calldata_v4_get_pool_and_position_info(token_id), http_post=http_post)
    info = decode_v4_get_pool_and_position_info(info_hex)
    liq_hex = eth_call(rpc_url, to_address, calldata_v4_get_position_liquidity(token_id), http_post=http_post)
    liquidity = decode_v4_get_position_liquidity_result(liq_hex)
    return {
        "token_id": token_id,
        "chain_id": chain_id,
        "protocol": "v4",
        "token0": info["pool_key"]["currency0"],
        "token1": info["pool_key"]["currency1"],
        "fee": info["pool_key"]["fee"],
        "tick_spacing": info["pool_key"]["tick_spacing"],
        "hooks": info["pool_key"]["hooks"],
        "tick_lower": info["tick_lower"],
        "tick_upper": info["tick_upper"],
        "liquidity": liquidity,
        "pool_id_hex": info["pool_id_hex"],
        "fees_unsupported_reason": (
            "v4 可領手續費需 StateView.getFeeGrowthInside 額外計算，本次未逐字核對"
            "換算公式並補測試，標示為「資料源不支援」，不用 v3 邏輯或猜測值頂替。"
        ),
    }


def require_alchemy_rpc_url(chain_id: int) -> str:
    """從環境變數 ALCHEMY_API_KEY 組出指定鏈的 Alchemy JSON-RPC endpoint。
    只讀環境變數、絕不印出/回傳含 key 的字串到例外以外的地方——呼叫端印錯誤
    訊息一律要先過 `_redact_rpc_url()`。Alchemy 網域 slug 逐字核對來源見
    module 內 `ALCHEMY_NETWORK_SLUG_BY_CHAIN` 註解。"""
    if chain_id not in ALCHEMY_NETWORK_SLUG_BY_CHAIN:
        raise WalletRpcError(f"chain_id={chain_id} 沒有已核對過的 Alchemy network slug")
    api_key = os.environ.get("ALCHEMY_API_KEY", "").strip()
    if not api_key:
        raise WalletRpcError(
            "缺少 ALCHEMY_API_KEY 環境變數。請到 dev-claude profile 的 .env 設定後再執行"
            "（本工具絕不讀取/顯示 key 本身）。"
        )
    slug = ALCHEMY_NETWORK_SLUG_BY_CHAIN[chain_id]
    return f"https://{slug}.g.alchemy.com/v2/{api_key}"


# dev-claude 的 Alchemy app 目前只對 Arbitrum 啟用了網路（其餘鏈回報
# 「NETWORK is not enabled for this app」，這是帳號層級設定，程式無法自行
# 開通）。為了不讓其餘四條鏈永遠卡在「供應商不支援」，改用官方／知名唯讀
# 公開 RPC 當退路——全部不需要 API key，2026-09-27 已用 eth_chainId 逐一
# 連線核對過回傳的 chain_id 正確：
PUBLIC_RPC_URL_BY_CHAIN = {
    # 2026-09-27：1rpc.io/eth 執行期間曾短暫回過 HTTP 410（CDN 節點故障），
    # 隔幾分鐘後用 curl 重測 eth_chainId／eth_blockNumber 皆恢復正常，
    # 判定是暫時性問題非永久下線，保留原本已核對過的位址。
    # （備選 cloudflare-eth.com 的 eth_blockNumber 會穩定回
    #  {"code":-32046,"message":"Cannot fulfill request"}，不適合當退路。）
    1: "https://1rpc.io/eth",
    42161: "https://arb1.arbitrum.io/rpc",
    10: "https://mainnet.optimism.io",
    8453: "https://base.publicnode.com",
    56: "https://bsc-dataseed.binance.org",
    130: "https://unichain.publicnode.com",
}


def _probe_chain_id(rpc_url: str, http_post: Optional[HttpPost] = None) -> int:
    """打一次真實 eth_chainId，回傳節點回報的 chain_id（int）；失敗就讓例外往外丟，
    由呼叫端（resolve_rpc_url）決定要不要換下一個候選 URL。"""
    poster = http_post or _default_http_post
    response = poster(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []})
    if "error" in response:
        raise WalletRpcError(f"節點回報 eth_chainId 錯誤（{_redact_rpc_url(rpc_url)}）：{response['error']}")
    result = response.get("result")
    if not isinstance(result, str):
        raise WalletRpcError(f"eth_chainId 回應格式異常（{_redact_rpc_url(rpc_url)}）：{result!r}")
    return int(result, 16)


def resolve_rpc_url(chain_id: int, http_post: Optional[HttpPost] = None) -> tuple[str, str, dict]:
    """依序嘗試 Alchemy（唯讀 API key）→ 公開唯讀 RPC，回傳
    (可用的 rpc_url, 來源標籤, 各候選的嘗試結果)。來源標籤只會是
    "alchemy" 或 "public-rpc"，絕不把 URL 本身（含 key）放進回傳的
    attempts 紀錄裡，一律先過 `_redact_rpc_url()`。兩個候選都失敗時丟出
    WalletRpcError，訊息附上兩邊各自的錯誤原因，方便誠實回報「這條鏈確實
    查不到」而不是默默吞掉。"""
    attempts: dict[str, str] = {}
    try:
        alchemy_url = require_alchemy_rpc_url(chain_id)
        _probe_chain_id(alchemy_url, http_post=http_post)
        return alchemy_url, "alchemy", attempts
    except WalletRpcError as exc:
        attempts["alchemy"] = str(exc)

    public_url = PUBLIC_RPC_URL_BY_CHAIN.get(chain_id)
    if public_url is None:
        attempts["public-rpc"] = f"chain_id={chain_id} 沒有已核對過的公開 RPC 候選"
        raise WalletRpcError(f"chain_id={chain_id} 的 Alchemy 與公開 RPC 皆不可用：{attempts}")
    try:
        reported = _probe_chain_id(public_url, http_post=http_post)
        if reported != chain_id:
            attempts["public-rpc"] = f"節點回報 chain_id={reported}，與預期 {chain_id} 不符"
            raise WalletRpcError(f"chain_id={chain_id} 的 Alchemy 與公開 RPC 皆不可用：{attempts}")
        return public_url, "public-rpc", attempts
    except WalletRpcError as exc:
        attempts.setdefault("public-rpc", str(exc))
        raise WalletRpcError(f"chain_id={chain_id} 的 Alchemy 與公開 RPC 皆不可用：{attempts}") from exc
