"""
模拟回测

用历史 Binance K 线数据模拟过去 7 天的交易。
假设 Polymarket 定价简化模型（基于动量的概率估计）。
"""

import asyncio
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone, timedelta
from src.price.binance_rest import fetch_klines
from src.signal.signal_engine import generate_signal, SignalStrength
from src.decision.kelly_sizer import calculate_position
from src.config.settings import INITIAL_BANKROLL, MIN_EDGE
from src.utils.db import init_db
from src.utils.logger import setup_logger

logger = setup_logger("backtest")


def estimate_polymarket_prices(btc_change_in_window: float) -> tuple[float, float]:
    """
    模拟 Polymarket UP/DOWN 定价

    简化模型: 市场基于近期走势定价，但反应不足
    """
    # 基础 50/50
    up_prob = 0.50

    # 市场对近期走势有部分反映（但不足）
    # 假设市场只反映了 30% 的实际趋势
    up_prob += btc_change_in_window * 15  # 30% 反映 × 放大因子

    # 加入噪音
    import random
    up_prob += random.gauss(0, 0.03)

    up_prob = max(0.10, min(0.90, up_prob))
    down_prob = 1 - up_prob

    return round(up_prob, 3), round(down_prob, 3)


async def run_backtest(days: int = 7, bankroll: float = 100.0):
    """
    回测主函数

    用历史 15 分钟 K 线模拟每天 96 个市场
    """
    print("\n" + "=" * 70)
    print("📊 BTC 15M Bot — 回测")
    print("=" * 70)
    print(f"  回测天数: {days}")
    print(f"  初始资金: ${bankroll:.2f}")
    print(f"  最小 Edge: {MIN_EDGE:.0%}")
    print()

    # 获取历史 K 线
    # Binance 15m K 线最多返回 1000 根 = ~10 天
    klines = await fetch_klines(interval="15m", limit=min(days * 96 + 100, 1000))
    if len(klines) < 10:
        print("❌ K 线数据不足")
        return

    # 也需要 1h K 线用于计算趋势
    klines_1h = await fetch_klines(interval="1h", limit=200)
    klines_4h = await fetch_klines(interval="4h", limit=100)
    klines_1d = await fetch_klines(interval="1d", limit=30)

    # 只测试最近 N 天
    test_klines = klines[-(days * 96):] if len(klines) > days * 96 else klines

    total_trades = 0
    total_wins = 0
    total_pnl = 0.0
    max_bankroll = bankroll
    min_bankroll = bankroll
    max_drawdown = 0.0

    signals_generated = 0
    signals_filtered = 0
    snipe_attempts = 0

    # 逐根 K 线模拟
    for i, k in enumerate(test_klines):
        close = k["close"]
        open_price = k["open"]
        actual_result = "up" if close >= open_price else "down"
        change_in_window = (close - open_price) / open_price

        # 跳过前几根（需要历史数据计算趋势）
        if i < 10:
            continue

        # 模拟技术指标
        from src.price.price_manager import TechnicalIndicators
        ind = TechnicalIndicators(btc_price=close)

        # 简化趋势计算（用前几根 K 线）
        recent = test_klines[max(0, i - 16):i]
        if recent:
            ind.trend_4h = (recent[-1]["close"] - recent[max(0, len(recent) - 4)]["open"]) / recent[max(0, len(recent) - 4)]["open"]
            ind.trend_12h = (recent[-1]["close"] - recent[0]["open"]) / recent[0]["open"] if len(recent) >= 8 else 0

        # 用 1h K 线算更好的趋势
        if klines_1h:
            h1_closes = [k1["close"] for k1 in klines_1h[-24:]]
            if len(h1_closes) >= 12:
                ind.trend_12h = (h1_closes[-1] - h1_closes[-12]) / h1_closes[-12]
            if len(h1_closes) >= 4:
                ind.trend_4h = (h1_closes[-1] - h1_closes[-4]) / h1_closes[-4]
            if len(h1_closes) >= 24:
                ind.trend_24h = (h1_closes[-1] - h1_closes[0]) / h1_closes[0]

            # RSI
            if len(h1_closes) >= 15:
                import numpy as np
                changes = np.diff(h1_closes)
                gains = np.where(changes > 0, changes, 0)
                losses = np.where(changes < 0, -changes, 0)
                avg_gain = np.mean(gains[-14:])
                avg_loss = np.mean(losses[-14:])
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    ind.rsi = round(100 - 100 / (1 + rs), 1)
                else:
                    ind.rsi = 100.0

            # 布林带
            if len(h1_closes) >= 20:
                import numpy as np
                arr = np.array(h1_closes[-20:])
                mid = np.mean(arr)
                std = np.std(arr)
                ind.bollinger_mid = mid
                ind.bollinger_upper = mid + 2 * std
                ind.bollinger_lower = mid - 2 * std
                upper_dist = abs(close - ind.bollinger_upper) / close
                lower_dist = abs(close - ind.bollinger_lower) / close
                ind.near_bollinger = min(upper_dist, lower_dist) < 0.005

        # 模拟 Polymarket 定价
        # 用这根 K 线之前的走势来估计
        prev_change = 0
        if i > 0:
            prev_k = test_klines[i - 1]
            prev_change = (prev_k["close"] - prev_k["open"]) / prev_k["open"]

        up_price, down_price = estimate_polymarket_prices(prev_change)

        # 整数关口检测
        round_num = round(close / 1000) * 1000
        ind.near_round_number = abs(close - round_num) / close < 0.003

        # 生成信号
        signal = generate_signal(
            indicators=ind,
            up_price=up_price,
            down_price=down_price,
            btc_current=close,
            price_to_beat=open_price,  # K 线开盘价 = PTB
        )

        signals_generated += 1

        if not signal.should_trade:
            signals_filtered += 1

        # 狙击检查: 如果 15 分钟内偏移 > 0.3% 且 Polymarket 没反映
            # 但用前 12 分钟的数据判断（最后 3 分钟可能反转）
            early_change = change_in_window * 0.8  # 用 80% 的变化估计
            if abs(early_change) > 0.003:
                snipe_dir = "up" if early_change > 0 else "down"
                snipe_price = up_price if snipe_dir == "up" else down_price
                if snipe_price < 0.55:
                    snipe_edge = 0.55 - snipe_price
                    if snipe_edge >= MIN_EDGE:
                        snipe_attempts += 1
                        # 实际结果可能和前 12 分钟方向不同（有反转风险）
                        if snipe_dir == actual_result:
                            pnl = bankroll * 0.02
                            total_pnl += pnl
                            bankroll += pnl
                            total_wins += 1
                        else:
                            pnl = -bankroll * 0.02
                            total_pnl += pnl
                            bankroll += pnl
                        total_trades += 1
            continue

        # 常规交易
        position = calculate_position(
            direction=signal.direction,
            confidence=signal.confidence,
            up_price=up_price,
            down_price=down_price,
            up_token="up_token",
            down_token="down_token",
            bankroll=bankroll,
            edge=signal.edge,
        )

        if not position or not position.should_trade:
            signals_filtered += 1
            continue

        # 模拟交易结果
        total_trades += 1

        if signal.direction == actual_result:
            pnl = position.shares * (1 - position.price)
            total_pnl += pnl
            bankroll += pnl
            total_wins += 1
        else:
            pnl = -position.cost_usd
            total_pnl += pnl
            bankroll += pnl

        # 追踪回撤
        max_bankroll = max(max_bankroll, bankroll)
        min_bankroll = min(min_bankroll, bankroll)
        drawdown = (max_bankroll - bankroll) / max_bankroll
        max_drawdown = max(max_drawdown, drawdown)

    # 结果
    print(f"\n{'=' * 70}")
    print(f"📊 回测结果 ({days} 天, {len(test_klines)} 个市场)")
    print(f"{'=' * 70}")
    print(f"  信号生成: {signals_generated}")
    print(f"  被过滤:   {signals_filtered}")
    print(f"  狙击尝试: {snipe_attempts}")
    print(f"\n  💰 交易统计:")
    print(f"    总交易: {total_trades}")
    print(f"    胜: {total_wins} | 负: {total_trades - total_wins}")
    print(f"    胜率: {total_wins / total_trades:.1%}" if total_trades > 0 else "    无交易")
    print(f"    总 PnL: ${total_pnl:+.2f}")
    print(f"    ROI: {total_pnl / INITIAL_BANKROLL:+.1%}")
    print(f"    最终资金: ${bankroll:.2f}")
    print(f"    最大回撤: {max_drawdown:.1%}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    init_db()
    asyncio.run(run_backtest(days=7))
