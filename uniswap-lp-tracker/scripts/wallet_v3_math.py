#!/usr/bin/env python3
"""Uniswap V3 純數學：由 liquidity + tick range + 目前 sqrtPriceX96 換算成
token0/token1 的實際持有數量（raw base units，尚未除以 decimals）。

公式逐字對照 Uniswap v3 白皮書 6.29-6.30（"Technical Advantage" 一節，
amount0/amount1 given liquidity），是業界標準公式，非本專案發明：

  sqrtA = sqrt(1.0001 ** tickLower)
  sqrtB = sqrt(1.0001 ** tickUpper)
  sqrtP = sqrtPriceX96 / 2**96          （目前價格）

  若 currentTick < tickLower（價格低於區間，倉位全部是 token0）：
      amount0 = liquidity * (1/sqrtA - 1/sqrtB)
      amount1 = 0
  若 currentTick >= tickUpper（價格高於區間，倉位全部是 token1）：
      amount0 = 0
      amount1 = liquidity * (sqrtB - sqrtA)
  否則（in range）：
      amount0 = liquidity * (1/sqrtP - 1/sqrtB)
      amount1 = liquidity * (sqrtP - sqrtA)

純函式、無網路 I/O，方便單元測試。
"""
from __future__ import annotations

Q96 = 2**96


def tick_to_sqrt_ratio(tick: int) -> float:
    return (1.0001 ** tick) ** 0.5


def amounts_for_liquidity(
    liquidity: int,
    current_tick: int,
    sqrt_price_x96: int,
    tick_lower: int,
    tick_upper: int,
) -> tuple[int, int]:
    """回傳 (amount0_raw, amount1_raw)，皆為非負整數（raw base units）。"""
    if tick_lower >= tick_upper:
        raise ValueError(f"tick_lower({tick_lower}) 必須小於 tick_upper({tick_upper})")
    if liquidity <= 0:
        return 0, 0

    sqrt_a = tick_to_sqrt_ratio(tick_lower)
    sqrt_b = tick_to_sqrt_ratio(tick_upper)
    sqrt_p = sqrt_price_x96 / Q96

    if current_tick < tick_lower:
        amount0 = liquidity * (1.0 / sqrt_a - 1.0 / sqrt_b)
        amount1 = 0.0
    elif current_tick >= tick_upper:
        amount0 = 0.0
        amount1 = liquidity * (sqrt_b - sqrt_a)
    else:
        amount0 = liquidity * (1.0 / sqrt_p - 1.0 / sqrt_b)
        amount1 = liquidity * (sqrt_p - sqrt_a)

    return max(0, int(amount0)), max(0, int(amount1))


def in_range(current_tick: int, tick_lower: int, tick_upper: int) -> bool:
    return tick_lower <= current_tick < tick_upper
