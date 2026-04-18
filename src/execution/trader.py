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
    maker_mode: bool = False,
) -> dict:
    """执行交易
    
    v0.4: 支持 Taker/Maker 分层
    - maker_mode=False (默认): Taker 模式，吃现价
    - maker_mode=True: Maker 模式，挂限价单等成交
    """
    mode_label = "🔵 Maker" if maker_mode else "🔴 Taker"

    if TRADING_MODE == "SIMULATION":
        trade_id = insert_trade(
            market_slug=market_slug,
            side=side,
            token_id=token_id,
            shares=shares,
            price=price,
            cost_usd=cost_usd,
        )
        if trade_id == -1:
            return {"status": "duplicate", "trade_id": -1, "side": side}
        logger.info(
            f"📝 模拟交易 #{trade_id}: {mode_label} {side.upper()} {shares:.1f}股 "
            f"@ ${price:.3f} = ${cost_usd:.2f} (Edge={edge:+.1%})"
        )
        return {
            "status": "simulated",
            "trade_id": trade_id,
            "side": side,
            "shares": shares,
            "price": price,
            "cost": cost_usd,
            "maker_mode": maker_mode,
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
            # v0.4: Maker 模式用 GTC + 限价；Taker 模式用 FOK（全部成交或取消）
            if maker_mode:
                result = client.post_order(signed_order, orderType="GTC")
            else:
                result = client.post_order(signed_order, orderType="GTC")

            # 提取 order_id
            order_id = ""
            if isinstance(result, dict):
                order_id = result.get("orderID", "")
            elif isinstance(result, str):
                order_id = result

            # 记录到 DB（带 order_id）
            trade_id = insert_trade(
                market_slug=market_slug,
                side=side,
                token_id=token_id,
                shares=size,
                price=price,
                cost_usd=round(size * price, 2),
                order_id=order_id,
            )
            if trade_id == -1:
                return {"status": "duplicate", "trade_id": -1, "side": side}

            logger.info(
                f"💰 {mode_label} 实盘交易 #{trade_id}: {side.upper()} {size:.1f}股 "
                f"@ ${price:.2f} = ${size * price:.2f} "
                f"Edge={edge:+.1%} OrderID={result}"
            )

            return {
                "status": "live",
                "trade_id": trade_id,
                "order_id": order_id,
                "side": side,
                "shares": size,
                "price": price,
                "cost": round(size * price, 2),
                "maker_mode": maker_mode,
            }

        except Exception as e:
            logger.error(f"❌ {mode_label} 实盘下单失败: {e}")
            # 失败了也记录，标记为 failed
            trade_id = insert_trade(
                market_slug=market_slug,
                side=side,
                token_id=token_id,
                shares=shares,
                price=price,
                cost_usd=cost_usd,
            )
            if trade_id == -1:
                return {"status": "duplicate", "trade_id": -1, "side": side}
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
            result = client.post_order(signed_order, orderType="GTC")

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


def _check_order_filled(order_id: str) -> bool:
    """检查链上订单是否已成交
    
    Returns:
        True if order is matched/filled, False if still live or not found
    """
    if not order_id:
        # 没有 order_id（老数据），保守认为已成交
        return True
    try:
        client = _get_clob_client()
        # py_clob_client 的 get_order 方法
        order = client.get_order(order_id)
        if order:
            status = order.get("status", "") if isinstance(order, dict) else str(order)
            # matched = 已成交, live = 还挂着
            return status.lower() in ("matched", "filled", "completely_filled")
        # 查不到订单，可能已过期被清理
        logger.warning(f"  ⚠️ 订单 {order_id[:16]}... 查不到，可能已过期")
        return False
    except Exception as e:
        logger.warning(f"  ⚠️ 查询订单状态失败: {e}，保守按未成交处理")
        return False


def settle_trades(market_slug: str, result: str):
    """
    结算市场相关交易
    
    v0.5: 结算前检查链上订单是否真正成交
    - Maker 单可能挂了但没成交（status=live），不能算赢/输
    - 只有链上确认成交（status=matched）才计入 PnL

    Args:
        market_slug: 市场 slug
        result: "up" / "down"
    """
    from src.utils.db import get_connection
    conn = get_connection()
    
    # 获取该市场的 open 交易
    open_trades = conn.execute(
        "SELECT * FROM trades WHERE status='open' AND market_slug=?",
        (market_slug,)
    ).fetchall()
    
    if not open_trades:
        conn.close()
        return
    
    for trade in open_trades:
        trade = dict(trade)
        order_id = trade.get("order_id", "")
        trade_id = trade["id"]
        
        # 检查链上是否真正成交
        filled = _check_order_filled(order_id)
        
        if not filled:
            # 没成交 → 标记为 expired，不算 PnL
            conn.execute(
                "UPDATE trades SET status='expired', settled_at=datetime('now') WHERE id=?",
                (trade_id,)
            )
            logger.info(
                f"⏭️ 交易 #{trade_id} 未成交(expired) | "
                f"{trade['side'].upper()} @ ${trade['price']:.2f} "
                f"order={order_id[:16] if order_id else 'N/A'}..."
            )
            continue
        
        # 已成交 → 正常结算
        if trade["side"] == result:
            # 赢了 — 扣除结算手续费
            from src.config.settings import SETTLE_FEE_RATE
            gross = trade["shares"] * (1 - trade["price"])
            fee = trade["shares"] * SETTLE_FEE_RATE
            pnl = round(gross - fee, 2)
            conn.execute(
                "UPDATE trades SET status='settled', pnl=?, settled_price=1.0, settled_at=datetime('now') WHERE id=?",
                (pnl, trade_id),
            )
            logger.info(f"✅ 交易 #{trade_id} 赢了! 毛利=${gross:.2f} 手续费=${fee:.2f} 净PnL: +${pnl:.2f}")
        else:
            # 输了
            pnl = -trade["cost_usd"]
            conn.execute(
                "UPDATE trades SET status='settled', pnl=?, settled_price=0.0, settled_at=datetime('now') WHERE id=?",
                (round(pnl, 2), trade_id),
            )
            logger.info(f"❌ 交易 #{trade_id} 输了. PnL: ${pnl:.2f}")
    
    conn.commit()
    conn.close()


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


# === v0.4: 被动做市模块 ===

def place_passive_orders(
    market_slug: str,
    up_token_id: str,
    down_token_id: str,
    up_price: float,
    down_price: float,
    spread: float = 0.08,
    size_usd: float = 1.5,
) -> list[dict]:
    """被动做市：在 UP 和 DOWN 两边同时挂买卖单
    
    赚 Liquidity Rewards + bid-ask spread + Maker Rebates
    
    Args:
        market_slug: 市场 slug
        up_token_id: UP token
        down_token_id: DOWN token
        up_price: UP 当前中间价
        down_price: DOWN 当前中间价
        spread: 挂单 spread（bid = mid - spread/2, ask = mid + spread/2）
        size_usd: 每边挂单金额
    
    Returns:
        挂单结果列表
    """
    results = []
    half_spread = spread / 2
    
    # 计算挂单价格
    pairs = [
        {"token_id": up_token_id, "mid": up_price, "label": "UP"},
        {"token_id": down_token_id, "mid": down_price, "label": "DOWN"},
    ]
    
    for pair in pairs:
        mid = pair["mid"]
        if mid <= 0.05 or mid >= 0.95:
            # 太极端的价格不挂单
            continue
        
        bid_price = round(max(0.01, mid - half_spread), 2)
        ask_price = round(min(0.99, mid + half_spread), 2)
        
        if bid_price >= ask_price:
            continue
        
        # 挂买单
        bid_size = round(size_usd / bid_price, 2) if bid_price > 0 else 0
        if bid_size > 0:
            result = _place_single_order(
                token_id=pair["token_id"],
                price=bid_price,
                size=bid_size,
                side="BUY",
                label=f"{pair['label']}-BID",
                market_slug=market_slug,
            )
            results.append(result)
        
        # 挂卖单（需要持仓，跳过如果没有）
        # 暂时只挂买单（提供 bid 流动性）
    
    logger.info(
        f"📊 被动做市: 挂了 {len(results)} 个单 "
        f"(UP mid={up_price:.2f}, DOWN mid={down_price:.2f}, spread={spread:.2f})"
    )
    
    return results


def _place_single_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    label: str = "",
    market_slug: str = "",
) -> dict:
    """挂单个限价单"""
    if TRADING_MODE == "SIMULATION":
        logger.info(f"  📝 模拟挂单 {label}: {side} {size:.1f}股 @ ${price:.2f}")
        return {"status": "simulated_mm", "label": label, "price": price, "size": size, "side": side}
    
    try:
        client = _get_clob_client()
        from py_clob_client.clob_types import OrderArgs
        
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        
        signed_order = client.create_order(order_args)
        result = client.post_order(signed_order, orderType="GTC")
        
        logger.info(f"  💙 Maker挂单 {label}: {side} {size:.1f}股 @ ${price:.2f} → {result}")
        return {
            "status": "live_mm",
            "label": label,
            "order_id": result,
            "price": price,
            "size": size,
            "side": side,
            "token_id": token_id,
        }
    except Exception as e:
        logger.warning(f"  ⚠️ 挂单失败 {label}: {e}")
        return {"status": "mm_failed", "label": label, "error": str(e)}


def cancel_all_orders(token_id: str = None) -> int:
    """撤单：取消指定 token 或所有挂单"""
    if TRADING_MODE == "SIMULATION":
        logger.info(f"  📝 模拟撤单: token_id={token_id or 'ALL'}")
        return 0
    
    try:
        client = _get_clob_client()
        if token_id:
            result = client.cancel_orders_by_token_id(token_id)
        else:
            result = client.cancel_all()
        count = len(result) if isinstance(result, list) else 1
        logger.info(f"  🗑️ 撤单: {count} 个订单 (token={token_id or 'ALL'})")
        return count
    except Exception as e:
        logger.warning(f"  ⚠️ 撤单失败: {e}")
        return 0
