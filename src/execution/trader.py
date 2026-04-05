"""
交易执行引擎

模拟模式: 记录到 DB
实盘模式: 通过 py_clob_client 下单
"""

from src.config.settings import TRADING_MODE
from src.utils.db import insert_trade, update_trade_pnl, get_open_trades, get_daily_stats
from src.utils.logger import setup_logger

logger = setup_logger("trader")


def execute_trade(
    market_slug: str,
    side: str,
    token_id: str,
    shares: float,
    price: float,
    cost_usd: float,
    edge: float,
) -> dict:
    """执行交易"""

    if TRADING_MODE == "SIMULATION":
        trade_id = insert_trade(
            market_slug=market_slug,
            side=side,
            token_id=token_id,
            shares=shares,
            price=price,
            cost_usd=cost_usd,
        )
        logger.info(
            f"📝 模拟交易 #{trade_id}: {side.upper()} {shares:.1f}股 "
            f"@ ${price:.3f} = ${cost_usd:.2f} (Edge={edge:+.1%})"
        )
        return {
            "status": "simulated",
            "trade_id": trade_id,
            "side": side,
            "shares": shares,
            "price": price,
            "cost": cost_usd,
        }

    else:
        # TODO: 实盘下单
        logger.info(f"实盘下单: {side} {shares} @ {price}")
        return {"status": "live_placeholder"}


def settle_trades(market_slug: str, result: str):
    """
    结算市场相关交易

    Args:
        market_slug: 市场 slug
        result: "up" / "down"
    """
    open_trades = get_open_trades()

    for trade in open_trades:
        if trade["market_slug"] != market_slug:
            continue

        if trade["side"] == result:
            # 赢了
            pnl = trade["shares"] * (1 - trade["price"])
            update_trade_pnl(trade["id"], pnl=round(pnl, 2), settled_price=1.0)
            logger.info(f"✅ 交易 #{trade['id']} 赢了! PnL: +${pnl:.2f}")
        else:
            # 输了
            pnl = -trade["cost_usd"]
            update_trade_pnl(trade["id"], pnl=round(pnl, 2), settled_price=0.0)
            logger.info(f"❌ 交易 #{trade['id']} 输了. PnL: ${pnl:.2f}")


def get_summary() -> dict:
    """交易汇总"""
    stats = get_daily_stats()
    return {
        "total_trades": stats["total_trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": stats["win_rate"],
        "total_pnl": stats["total_pnl"],
    }
