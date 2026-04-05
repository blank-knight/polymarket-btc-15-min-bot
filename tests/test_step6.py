"""Step 6 测试: 信号引擎"""

import asyncio
import sys
sys.path.insert(0, ".")

from src.price.price_manager import PriceManager
from src.signal.signal_engine import generate_signal, SignalStrength


async def main():
    pm = PriceManager()
    await pm._refresh_klines()
    ind = pm.get_indicators()

    print("=== 当前 BTC 数据 ===")
    print(f"价格: ${ind.btc_price:,.2f}")
    print(f"4h:  {ind.trend_4h:+.2%}")
    print(f"12h: {ind.trend_12h:+.2%}")
    print(f"24h: {ind.trend_24h:+.2%}")
    print(f"RSI: {ind.rsi:.1f}")
    print(f"接近布林带: {ind.near_bollinger}")
    print(f"接近整数关口: {ind.near_round_number}")

    print("\n=== 场景测试 ===\n")

    # 场景 1: 市场均衡 (UP=0.50, DOWN=0.50)
    print("--- 场景 1: 市场均衡 ---")
    s1 = generate_signal(ind, up_price=0.50, down_price=0.50, btc_current=ind.btc_price)
    _print_signal(s1)

    # 场景 2: UP 被低估 (市场认为跌，但趋势偏下)
    print("\n--- 场景 2: UP=0.35 (市场看跌) ---")
    s2 = generate_signal(ind, up_price=0.35, down_price=0.65, btc_current=ind.btc_price)
    _print_signal(s2)

    # 场景 3: DOWN 被低估 (市场看涨，但我们趋势偏下)
    print("\n--- 场景 3: DOWN=0.30 (市场看涨) ---")
    s3 = generate_signal(ind, up_price=0.70, down_price=0.30, btc_current=ind.btc_price)
    _print_signal(s3)

    # 场景 4: 有 price to beat，当前已跌 0.8%
    print("\n--- 场景 4: 有 PTB，已跌 0.8% ---")
    ptb = ind.btc_price * 1.008  # PTB 比 current 高 0.8%
    s4 = generate_signal(ind, up_price=0.45, down_price=0.55, btc_current=ind.btc_price, price_to_beat=ptb)
    _print_signal(s4)

    # 场景 5: 有 PTB，已涨 1%
    print("\n--- 场景 5: 有 PTB，已涨 1% ---")
    ptb2 = ind.btc_price * 0.99  # PTB 比 current 低 1%
    s5 = generate_signal(ind, up_price=0.60, down_price=0.40, btc_current=ind.btc_price, price_to_beat=ptb2)
    _print_signal(s5)


def _print_signal(s):
    print(f"  方向: {s.direction or '无'}")
    print(f"  强度: {s.strength.value}")
    print(f"  置信度: {s.confidence:.1%}")
    print(f"  Edge: {s.edge:+.1%}")
    print(f"  Layer1 趋势: {s.layer1_trend} | {s.layer1_detail}")
    print(f"  Layer2 动量: {s.layer2_momentum} | {s.layer2_detail}")
    print(f"  Layer3 定价: {s.layer3_detail}")
    print(f"  安全阀: RSI={'✅' if s.safety_rsi_ok else '❌'} 速度={'✅' if s.safety_speed_ok else '❌'} 关键位={'✅' if s.safety_keylevel_ok else '❌'}")
    if s.safety_skip_reason:
        print(f"  ⚠️ 跳过原因: {s.safety_skip_reason}")
    print(f"  {'✅ 应该交易!' if s.should_trade else '❌ 不交易'}")


if __name__ == "__main__":
    asyncio.run(main())
