"""
Polymarket Gamma API — BTC 15 分钟市场发现

市场 slug 格式: btc-updown-15m-{unix_timestamp}
"""

import asyncio
import json
import aiohttp
import math
from datetime import datetime, timezone
from dataclasses import dataclass

from src.config.settings import GAMMA_API_URL
from src.utils.logger import setup_logger

logger = setup_logger("gamma_client")


@dataclass
class BTC15mMarket:
    """BTC 15 分钟市场"""
    slug: str
    question: str
    market_id: str
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    start_time: datetime
    end_time: datetime
    start_timestamp: int
    volume: float = 0.0
    liquidity: float = 0.0


def calc_current_market_slug() -> tuple[str, int, int]:
    """
    计算当前和下一个市场的 slug

    Returns:
        (slug, start_timestamp, end_timestamp)
    """
    now = datetime.now(timezone.utc)
    # 向下取整到 15 分钟
    minute_offset = (now.minute // 15) * 15
    start = now.replace(minute=minute_offset, second=0, microsecond=0)
    start_ts = int(start.timestamp())
    end_ts = start_ts + 900  # 15 分钟 = 900 秒
    slug = f"btc-updown-15m-{start_ts}"
    return slug, start_ts, end_ts


def calc_next_market_slug() -> tuple[str, int, int]:
    """下一个市场"""
    _, start_ts, _ = calc_current_market_slug()
    next_ts = start_ts + 900
    next_end = next_ts + 900
    slug = f"btc-updown-15m-{next_ts}"
    return slug, next_ts, next_end


async def discover_btc_markets(limit: int = 10) -> list[BTC15mMarket]:
    """
    发现 BTC 15 分钟市场

    通过 Gamma API 的 /markets 端点搜索 btc-updown-15m
    """
    url = f"{GAMMA_API_URL}/markets"
    params = {
        "slug_contains": "btc-updown-15m",
        "limit": limit,
        "active": "true",
        "order": "startDate",
        "ascending": "false",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Gamma API 失败 {resp.status}: {text[:200]}")
                    return []

                markets_data = await resp.json()

    except Exception as e:
        logger.error(f"Gamma API 异常: {e}")
        return []

    markets = []
    for m in markets_data:
        try:
            slug = m.get("slug", "")
            if "btc-updown-15m" not in slug:
                continue

            # 解析时间戳
            parts = slug.split("-")
            ts = int(parts[-1])
            start_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            end_time = datetime.fromtimestamp(ts + 900, tz=timezone.utc)

            # 解析 token
            tokens = m.get("tokens", [])
            up_token = ""
            down_token = ""
            up_price = 0.5
            down_price = 0.5

            for token in tokens:
                outcome = token.get("outcome", "").lower()
                token_id = token.get("token_id", "")
                price = float(token.get("price", 0.5))

                if outcome == "up":
                    up_token = token_id
                    up_price = price
                elif outcome == "down":
                    down_token = token_id
                    down_price = price

            if not up_token or not down_token:
                continue

            market = BTC15mMarket(
                slug=slug,
                question=m.get("question", ""),
                market_id=m.get("id", ""),
                up_token_id=up_token,
                down_token_id=down_token,
                up_price=up_price,
                down_price=down_price,
                start_time=start_time,
                end_time=end_time,
                start_timestamp=ts,
                volume=float(m.get("volume", 0)),
                liquidity=float(m.get("liquidity", 0)),
            )
            markets.append(market)

        except (ValueError, KeyError, IndexError) as e:
            logger.debug(f"解析市场跳过: {e}")
            continue

    logger.info(f"发现 {len(markets)} 个 BTC 15m 市场")
    return markets


async def get_market_by_slug(slug: str) -> BTC15mMarket | None:
    """根据 slug 获取单个市场"""
    url = f"{GAMMA_API_URL}/markets"
    params = {"slug": slug}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                if not data:
                    return None

                m = data[0]
                up_token = down_token = ""
                up_price = down_price = 0.5

                # 尝试从 clobTokenIds 解析
                clob_ids_raw = m.get("clobTokenIds", "[]")
                try:
                    clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
                except:
                    clob_ids = []

                # 尝试从 tokens 解析（旧 API 格式）
                tokens = m.get("tokens", [])
                
                if clob_ids and len(clob_ids) >= 2:
                    # 新格式：clobTokenIds 是 [up_id, down_id]
                    up_token = clob_ids[0]
                    down_token = clob_ids[1]
                elif tokens:
                    for token in tokens:
                        outcome = token.get("outcome", "").lower()
                        if outcome == "up":
                            up_token = token.get("token_id", "")
                            up_price = float(token.get("price", 0.5))
                        elif outcome == "down":
                            down_token = token.get("token_id", "")
                            down_price = float(token.get("price", 0.5))

                # 解析价格
                prices_raw = m.get("outcomePrices", "[]")
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    if prices and len(prices) >= 2:
                        up_price = float(prices[0])
                        down_price = float(prices[1])
                except:
                    pass

                parts = slug.split("-")
                ts = int(parts[-1])

                return BTC15mMarket(
                    slug=slug,
                    question=m.get("question", ""),
                    market_id=m.get("id", ""),
                    up_token_id=up_token,
                    down_token_id=down_token,
                    up_price=up_price,
                    down_price=down_price,
                    start_time=datetime.fromtimestamp(ts, tz=timezone.utc),
                    end_time=datetime.fromtimestamp(ts + 900, tz=timezone.utc),
                    start_timestamp=ts,
                )

    except Exception as e:
        logger.error(f"获取市场失败 ({slug}): {e}")
        return None
