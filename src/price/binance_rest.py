"""
Binance REST API — K 线数据获取

用于计算多时间框架趋势、RSI、布林带等技术指标。
"""

import asyncio
import aiohttp
from datetime import datetime
from typing import List

from src.config.settings import BINANCE_REST_URL, BINANCE_SYMBOL
from src.utils.logger import setup_logger

logger = setup_logger("binance_rest")


async def fetch_klines(interval: str = "15m", limit: int = 100) -> List[dict]:
    """
    获取 BTC/USDT K 线数据

    Args:
        interval: K 线周期 (1m/5m/15m/1h/4h/1d)
        limit: 数量

    Returns:
        [{"open_time", "open", "high", "low", "close", "volume", "close_time"}, ...]
    """
    url = f"{BINANCE_REST_URL}/klines"
    params = {
        "symbol": BINANCE_SYMBOL.upper(),
        "interval": interval,
        "limit": limit,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"K线请求失败 {resp.status}: {text[:200]}")
                    return []

                data = await resp.json()
                klines = []
                for k in data:
                    klines.append({
                        "open_time": k[0],
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "close_time": k[6],
                        "trades": k[8],
                    })
                logger.info(f"获取 {interval} K线 {len(klines)} 根")
                return klines

    except asyncio.TimeoutError:
        logger.warning(f"K线请求超时 ({interval})")
        return []
    except Exception as e:
        logger.error(f"K线请求异常: {e}")
        return []


async def fetch_price() -> float:
    """获取当前 BTC/USDT 价格"""
    url = f"{BINANCE_REST_URL}/ticker/price"
    params = {"symbol": BINANCE_SYMBOL.upper()}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return float(data["price"])
    except Exception as e:
        logger.error(f"获取价格失败: {e}")
        return 0.0


async def fetch_24h_ticker() -> dict:
    """获取 24 小时行情"""
    url = f"{BINANCE_REST_URL}/ticker/24h"
    params = {"symbol": BINANCE_SYMBOL.upper()}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return {
                    "price": float(data["lastPrice"]),
                    "change_pct": float(data["priceChangePercent"]),
                    "high": float(data["highPrice"]),
                    "low": float(data["lowPrice"]),
                    "volume": float(data["volume"]),
                    "trades": int(data["count"]),
                }
    except Exception as e:
        logger.error(f"获取 24h 行情失败: {e}")
        return {}
