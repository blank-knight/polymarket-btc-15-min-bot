"""Step 3-4 测试: Polymarket 市场发现 + Price to Beat"""

import asyncio
import sys
sys.path.insert(0, ".")

from src.market.gamma_client import (
    calc_current_market_slug,
    calc_next_market_slug,
    discover_btc_markets,
    get_market_by_slug,
)


async def main():
    print("=== 测试市场发现 ===\n")

    # 1. 当前和下一个市场
    slug, start_ts, end_ts = calc_current_market_slug()
    print(f"当前市场: {slug}")
    print(f"  开始: {start_ts}")
    print(f"  结束: {end_ts}")

    next_slug, next_start, next_end = calc_next_market_slug()
    print(f"\n下一个市场: {next_slug}")

    # 2. 发现活跃市场
    print("\n--- 发现市场 ---")
    markets = await discover_btc_markets(limit=10)

    if markets:
        for m in markets[:5]:
            print(f"\n  📊 {m.slug}")
            print(f"     UP:   {m.up_price:.2f} ({m.up_token_id[:16]}...)")
            print(f"     DOWN: {m.down_price:.2f} ({m.down_token_id[:16]}...)")
            print(f"     开始: {m.start_time.strftime('%H:%M')}")
    else:
        print("  ❌ 未发现市场")

    # 3. 单个市场查询
    print(f"\n--- 查询当前市场 ---")
    market = await get_market_by_slug(slug)
    if market:
        print(f"  ✅ {market.slug}")
        print(f"  UP: {market.up_price:.2f} | DOWN: {market.down_price:.2f}")
    else:
        print(f"  ❌ 未找到 (可能已过期)")

    # 4. 测试下一个市场
    print(f"\n--- 查询下一个市场 ---")
    next_market = await get_market_by_slug(next_slug)
    if next_market:
        print(f"  ✅ {next_market.slug}")
        print(f"  UP: {next_market.up_price:.2f} | DOWN: {next_market.down_price:.2f}")
    else:
        print(f"  ❌ 未找到")


if __name__ == "__main__":
    asyncio.run(main())
