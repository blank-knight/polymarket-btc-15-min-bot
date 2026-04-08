"""
交易执行引擎

模拟模式: 记录到 DB
实盘模式: 通过 py_clob_client 下单
"""

import os
from src.config.settings import TRADING_MODE
from src.utils.db import insert_trade, update_trade_pnl, get_open_trades, get_daily_stats
from src.utils.logger import setup_logger

logger = setup_logger("trader")

# 实盘客户端（懒加载）
_clob_client = None


def _get_clob_client():
    """获取 CLOB 客户端（单例）"""
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    host = "https://clob.polymarket.com"
    chain_id = 137  # Polygon

    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_FUNDER")
    api_key = os.getenv("CLOB_API_KEY")
    api_secret = os.getenv("CLOB_API_SECRET")
    api_passphrase = os.getenv("CLOB_API_PASSPHRASE")
    sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))

    if not pk:
        raise RuntimeError("缺少 POLYGON_PRIVATE_KEY，请检查 .env")

    creds = None
    if all([api_key, api_secret, api_passphrase]):
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )

    _clob_client = ClobClient(
        host=host,
        chain_id=chain_id,
        key=pk,
        signature_type=sig_type,
        funder=funder,
        creds=creds,
    )

    logger.info(f"🔗 CLOB 客户端初始化成功 (sig_type={sig_type}, funder={funder[:10]}...)")
    return _clob_client


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
        # === 实盘下单 ===
        try:
            client = _get_clob_client()
            from py_clob_client.clob_types import OrderArgs

            # Polymarket 的 side: BUY 对应我们看涨/看跌
            order_side = "BUY"
            # 限制价格精度（Polymarket 要求 0.01 步进）
            price = round(max(0.01, min(0.99, price)), 2)
            size = round(cost_usd / price, 2)

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )

            signed_order = client.create_order(order_args)
            result = client.post_order(signed_order, options={"type": "GTC"})

            # 记录到 DB
            trade_id = insert_trade(
                market_slug=market_slug,
                side=side,
                token_id=token_id,
                shares=size,
                price=price,
                cost_usd=round(size * price, 2),
            )

            logger.info(
                f"💰 实盘交易 #{trade_id}: {side.upper()} {size:.1f}股 "
                f"@ ${price:.2f} = ${size * price:.2f} "
                f"Edge={edge:+.1%} OrderID={result}"
            )

            return {
                "status": "live",
                "trade_id": trade_id,
                "order_id": result,
                "side": side,
                "shares": size,
                "price": price,
                "cost": round(size * price, 2),
            }

        except Exception as e:
            logger.error(f"❌ 实盘下单失败: {e}")
            # 失败了也记录，标记为 failed
            trade_id = insert_trade(
                market_slug=market_slug,
                side=side,
                token_id=token_id,
                shares=shares,
                price=price,
                cost_usd=cost_usd,
            )
            return {
                "status": "failed",
                "trade_id": trade_id,
                "error": str(e),
            }


def sell_position(
    market_slug: str,
    token_id: str,
    shares: float,
    sell_price: float,
) -> dict:
    """卖出持仓（止盈）"""
    if TRADING_MODE == "SIMULATION":
        logger.info(
            f"📤 模拟卖出: {shares:.1f}股 @ ${sell_price:.3f} "
            f"= ${shares * sell_price:.2f}"
        )
        return {"status": "simulated_sell", "sell_price": sell_price, "revenue": shares * sell_price}

    else:
        try:
            client = _get_clob_client()
            from py_clob_client.clob_types import OrderArgs

            sell_price = round(max(0.01, min(0.99, sell_price)), 2)

            order_args = OrderArgs(
                token_id=token_id,
                price=sell_price,
                size=round(shares, 2),
                side="SELL",
            )

            signed_order = client.create_order(order_args)
            result = client.post_order(signed_order, options={"type": "GTC"})

            revenue = round(shares * sell_price, 2)
            logger.info(
                f"💰 实盘卖出: {shares:.1f}股 @ ${sell_price:.2f} "
                f"= ${revenue:.2f} OrderID={result}"
            )

            return {
                "status": "live_sell",
                "order_id": result,
                "sell_price": sell_price,
                "revenue": revenue,
            }

        except Exception as e:
            logger.error(f"❌ 实盘卖出失败: {e}")
            return {"status": "sell_failed", "error": str(e)}


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
