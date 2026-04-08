"""
Polymarket BTC 15分钟交易 Bot

用法:
  python main.py              # 单次扫描
  python main.py --run        # 24/7 运行
  python main.py --backtest   # 历史回测
  python main.py --summary    # 交易汇总
"""

import asyncio
import argparse
import sys

# 加载 .env
from dotenv import load_dotenv
load_dotenv()

from src.config.settings import TRADING_MODE, INITIAL_BANKROLL
from src.utils.db import init_db
from src.utils.logger import setup_logger

logger = setup_logger("main")


async def run_once():
    """单次扫描"""
    from src.price.price_manager import PriceManager
    from src.market.gamma_client import calc_current_market_slug, get_market_by_slug
    from src.signal.signal_engine import generate_signal

    logger.info("=" * 60)
    logger.info("BTC 15M Bot — 单次扫描")
    logger.info(f"模式: {TRADING_MODE} | 资金: ${INITIAL_BANKROLL}")
    logger.info("=" * 60)

    pm = PriceManager()
    await pm._refresh_klines()
    ind = pm.get_indicators()

    slug, _, _ = calc_current_market_slug()
    market = await get_market_by_slug(slug)

    if market:
        signal = generate_signal(
            indicators=ind,
            up_price=market.up_price,
            down_price=market.down_price,
            btc_current=ind.btc_price,
        )

        print(f"\n📊 当前市场: {slug}")
        print(f"BTC: ${ind.btc_price:,.2f}")
        print(f"UP: {market.up_price:.2f} | DOWN: {market.down_price:.2f}")
        print(f"\n信号: {signal.direction or '无'} ({signal.strength.value})")
        print(f"置信度: {signal.confidence:.1%} | Edge: {signal.edge:+.1%}")
        print(f"趋势: {signal.layer1_detail}")
        if signal.safety_skip_reason:
            print(f"⚠️ {signal.safety_skip_reason}")
        print(f"{'✅ 交易' if signal.should_trade else '❌ 不交易'}")
    else:
        print(f"❌ 未找到市场: {slug}")

    await pm.stop()


async def run_continuous():
    """24/7 持续运行"""
    from src.scheduler import run
    await run()


def show_summary():
    """交易汇总"""
    from src.utils.db import get_daily_stats

    stats = get_daily_stats()
    print(f"\n📊 今日交易汇总")
    print(f"  日期: {stats['date']}")
    print(f"  总交易: {stats['total_trades']}")
    print(f"  胜: {stats['wins']} | 负: {stats['losses']}")
    print(f"  胜率: {stats['win_rate']:.1%}")
    print(f"  PnL: ${stats['total_pnl']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="BTC 15M Trading Bot")
    parser.add_argument("--run", action="store_true", help="24/7 持续运行")
    parser.add_argument("--summary", action="store_true", help="交易汇总")
    args = parser.parse_args()

    init_db()

    if args.summary:
        show_summary()
    elif args.run:
        asyncio.run(run_continuous())
    else:
        asyncio.run(run_once())


if __name__ == "__main__":
    main()
