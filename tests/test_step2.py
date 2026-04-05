"""Step 2 测试: Binance 价格数据"""

import asyncio
import sys
sys.path.insert(0, ".")


async def test_binance_rest():
    from src.price.binance_rest import fetch_klines, fetch_price, fetch_24h_ticker

    print("=== 测试 Binance REST API ===\n")

    # 当前价格
    price = await fetch_price()
    print(f"✅ BTC 当前价格: ${price:,.2f}")

    # 24h 行情
    ticker = await fetch_24h_ticker()
    if ticker:
        print(f"✅ 24h: {ticker['change_pct']:+.2f}% | 高 ${ticker['high']:,.2f} | 低 ${ticker['low']:,.2f}")

    # 多时间框架 K 线
    for interval in ["15m", "1h", "4h", "1d"]:
        klines = await fetch_klines(interval=interval, limit=5)
        if klines:
            k = klines[-1]
            change = (k["close"] - k["open"]) / k["open"] * 100
            print(f"✅ {interval} K线 (最近1根): ${k['close']:,.2f} ({change:+.2f}%)")
        else:
            print(f"❌ {interval} K线获取失败")


async def test_price_manager():
    from src.price.price_manager import PriceManager

    print("\n=== 测试价格管理器 ===\n")

    pm = PriceManager()

    # 先用 REST 数据测试指标计算
    await pm._refresh_klines()
    indicators = pm.get_indicators()

    print(f"BTC 价格: ${indicators.btc_price:,.2f}")
    print(f"\n📊 趋势:")
    print(f"  4h:  {indicators.trend_4h:+.2%}")
    print(f"  12h: {indicators.trend_12h:+.2%}")
    print(f"  24h: {indicators.trend_24h:+.2%}")

    print(f"\n📈 指标:")
    print(f"  RSI:  {indicators.rsi:.1f}")
    print(f"  布林: ${indicators.bollinger_lower:,.0f} ~ ${indicators.bollinger_mid:,.0f} ~ ${indicators.bollinger_upper:,.0f}")
    print(f"  MA20: ${indicators.ma20:,.2f}")
    print(f"  MA50: ${indicators.ma50:,.2f}")

    print(f"\n⚠️  安全阀:")
    print(f"  接近整数关口: {indicators.near_round_number}")
    print(f"  接近前高:     {indicators.near_previous_high}")
    print(f"  接近前低:     {indicators.near_previous_low}")
    print(f"  接近布林带:   {indicators.near_bollinger}")

    # 判断 RSI 状态
    if indicators.rsi >= 75:
        print(f"  ⚠️ 超买！RSI = {indicators.rsi}")
    elif indicators.rsi <= 25:
        print(f"  ⚠️ 超卖！RSI = {indicators.rsi}")
    else:
        print(f"  ✅ RSI 正常范围")


async def main():
    await test_binance_rest()
    await test_price_manager()


if __name__ == "__main__":
    asyncio.run(main())
