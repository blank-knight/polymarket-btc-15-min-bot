"""
Edge 计算和 Kelly 仓位
"""

from dataclasses import dataclass

from src.config.settings import (
    KELLY_FRACTION,
    MAX_POSITION_RATIO,
    MIN_EDGE_BASE,
    MIN_TRADE_USD,
    INITIAL_BANKROLL,
)
from src.utils.logger import setup_logger

logger = setup_logger("kelly")


@dataclass
class PositionSizing:
    """仓位计算结果"""
    should_trade: bool
    side: str          # "up" / "down"
    token: str         # token_id
    prob: float        # 估计概率
    price: float       # 市场价格
    edge: float        # Edge
    shares: float      # 股数
    cost_usd: float    # 投入金额
    expected_return: float  # 期望收益


def calculate_position(
    direction: str,
    confidence: float,
    up_price: float,
    down_price: float,
    up_token: str,
    down_token: str,
    bankroll: float,
    edge: float,
) -> PositionSizing | None:
    """
    计算仓位

    Args:
        direction: "up" / "down"
        confidence: 模型估计概率
        up_price: UP token 价格
        down_price: DOWN token 价格
        up_token: UP token ID
        down_token: DOWN token ID
        bankroll: 当前资金
        edge: Edge

    Returns:
        PositionSizing or None
    """
    if direction == "up":
        prob = confidence
        price = up_price
        token = up_token
    else:
        prob = confidence
        price = down_price
        token = down_token

    if prob <= 0 or price <= 0:
        return None

    # Kelly: f* = (bp - q) / b
    # b = 赔率 = (1 - price) / price
    # p = prob, q = 1 - prob
    b = (1 - price) / price if price > 0 else 0
    q = 1 - prob

    kelly_f = (b * prob - q) / b if b > 0 else 0

    # Quarter-Kelly
    fraction = kelly_f * KELLY_FRACTION

    if fraction <= 0:
        return None

    # 限制最大仓位
    fraction = min(fraction, MAX_POSITION_RATIO)

    # 计算投入
    cost_usd = bankroll * fraction

    if cost_usd < MIN_TRADE_USD:
        return None

    shares = cost_usd / price

    # 期望收益
    expected_return = prob * shares * (1 - price) - (1 - prob) * cost_usd

    return PositionSizing(
        should_trade=True,
        side=direction,
        token=token,
        prob=prob,
        price=price,
        edge=edge,
        shares=round(shares, 2),
        cost_usd=round(cost_usd, 2),
        expected_return=round(expected_return, 2),
    )
