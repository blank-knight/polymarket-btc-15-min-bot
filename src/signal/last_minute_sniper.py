"""
最后时刻狙击模块

在市场结算前 60 秒:
- BTC 当前价格 vs Price to Beat 方向已明确
- Polymarket 价格还没完全调整
- 快速下注
"""

from dataclasses import dataclass

from src.config.settings import SNIPER_TRIGGER_SECONDS, SNIPER_MIN_MOVE_PCT, SNIPER_PRICE_LAG_PCT, MIN_EDGE
from src.decision.kelly_sizer import calculate_position
from src.execution.trader import execute_trade
from src.risk.risk_manager import RiskManager
from src.utils.db import insert_signal
from src.utils.logger import setup_logger
from src.market.orderbook import get_real_buy_price

logger = setup_logger("sniper")


@dataclass
class SnipeResult:
    direction: str
    edge: float
    btc_change: float
    pm_price: float
    should_snipe: bool
    reason: str = ""


async def evaluate_snipe(
    btc_current: float,
    price_to_beat: float,
    up_price: float,
    down_price: float,
    up_token: str,
    down_token: str,
    market_slug: str,
    risk_mgr: RiskManager,
    bankroll: float,
) -> SnipeResult | None:
    """评估最后时刻狙击机会"""
    if btc_current <= 0 or price_to_beat <= 0:
        return None

    change = (btc_current - price_to_beat) / price_to_beat

    # BTC 偏移太小，不值得狙击
    if abs(change) < SNIPER_MIN_MOVE_PCT:
        return SnipeResult(
            direction="none",
            edge=0,
            btc_change=change,
            pm_price=0,
            should_snipe=False,
            reason=f"BTC 偏移太小 ({change:+.3%} < {SNIPER_MIN_MOVE_PCT}%)",
        )

    direction = "up" if change > 0 else "down"

    # 检查 Polymarket 价格是否滞后
    if direction == "up":
        pm_price = up_price
        if up_price >= 0.60:
            return SnipeResult(
                direction="up",
                edge=0,
                btc_change=change,
                pm_price=up_price,
                should_snipe=False,
                reason=f"UP 价格已充分反映 ({up_price:.2f})",
            )
        edge = (0.60 + abs(change) * 5) - up_price
        edge = min(edge, 0.30)
    else:
        pm_price = down_price
        if down_price >= 0.60:
            return SnipeResult(
                direction="down",
                edge=0,
                btc_change=change,
                pm_price=down_price,
                should_snipe=False,
                reason=f"DOWN 价格已充分反映 ({down_price:.2f})",
            )
        edge = (0.60 + abs(change) * 5) - down_price
        edge = min(edge, 0.30)

    if edge < MIN_EDGE:
        return SnipeResult(
            direction=direction,
            edge=edge,
            btc_change=change,
            pm_price=pm_price,
            should_snipe=False,
            reason=f"Edge 不足 ({edge:+.1%})",
        )

    # 🆕 获取真实 orderbook 买入价（考虑滑点）— 同步调用
    target_token = up_token if direction == "up" else down_token
    real_price = get_real_buy_price(target_token, budget_usd=5.0)

    if real_price and real_price > 0:
        logger.info(
            f"📊 真实价格: {real_price:.3f} (Gamma中间价={pm_price:.3f}) "
            f"滑点={real_price - pm_price:.3f}"
        )

        if real_price >= 0.95:
            return SnipeResult(
                direction=direction,
                edge=edge,
                btc_change=change,
                pm_price=pm_price,
                should_snipe=False,
                reason=f"真实价格太高 ({real_price:.3f})，利润空间不足",
            )

        pm_price = real_price

    # 记录信号
    insert_signal(
        market_slug=market_slug,
        strategy="last_minute_snipe",
        direction=direction,
        confidence=0.60 + edge,
        edge=edge,
        filtered=0,
    )

    # 计算仓位 (保守: 标准 Kelly 的一半)
    confidence = 0.60 + edge * 0.5

    position = calculate_position(
        direction=direction,
        confidence=min(confidence, 0.75),
        up_price=real_price if (real_price and direction == "up") else up_price,
        down_price=real_price if (real_price and direction == "down") else down_price,
        up_token=up_token,
        down_token=down_token,
        bankroll=bankroll,
        edge=edge,
    )

    if not position or not position.should_trade:
        return SnipeResult(
            direction=direction,
            edge=edge,
            btc_change=change,
            pm_price=pm_price,
            should_snipe=False,
            reason="仓位计算不通过",
        )

    # 风控
    ok, reason = risk_mgr.check_all(position.cost_usd)
    if not ok:
        return SnipeResult(
            direction=direction,
            edge=edge,
            btc_change=change,
            pm_price=pm_price,
            should_snipe=False,
            reason=f"风控: {reason}",
        )

    # 执行!
    execute_trade(
        market_slug=market_slug,
        side=position.side,
        token_id=position.token,
        shares=position.shares,
        price=position.price,
        cost_usd=position.cost_usd,
        edge=position.edge,
    )

    logger.info(
        f"🎯 狙击! {direction.upper()} BTC已{('涨' if change > 0 else '跌')}{abs(change):.2%} "
        f"PM价格={pm_price:.2f} Edge={edge:+.1%} 投入=${position.cost_usd:.2f}"
    )

    return SnipeResult(
        direction=direction,
        edge=edge,
        btc_change=change,
        pm_price=pm_price,
        should_snipe=True,
    )
