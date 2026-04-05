"""
Binance WebSocket 实时价格流

连接 Binance WebSocket，实时接收 BTC/USD 价格和 K 线数据。
"""

import json
import asyncio
from datetime import datetime
from collections import deque

import websockets

from src.config.settings import BINANCE_WS_URL, BINANCE_SYMBOL
from src.utils.logger import setup_logger

logger = setup_logger("binance_ws")


class BinancePriceStream:
    """
    Binance WebSocket 价格流

    同时订阅:
    - btcusdt@trade: 实时成交价
    - btcusdt@kline_15m: 15 分钟 K 线
    """

    def __init__(self):
        self.ws = None
        self.running = False

        # 当前价格
        self.current_price: float = 0.0
        self.last_trade_time: str = ""

        # 15 分钟 K 线缓存
        self.kline_15m = {
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "start_time": 0,
            "is_closed": False,
        }

        # 最近成交价队列（用于短期动量）
        self.recent_trades: deque = deque(maxlen=1000)
        # 最近完成的 15 分钟 K 线
        self.completed_klines: deque = deque(maxlen=200)

        # 回调
        self.on_price_update = None
        self.on_kline_close = None

    async def connect(self):
        """连接 WebSocket"""
        streams = [
            f"{BINANCE_SYMBOL}@trade",
            f"{BINANCE_SYMBOL}@kline_15m",
        ]
        url = f"{BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"

        try:
            self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            self.running = True
            logger.info(f"Binance WS 已连接")
            return True
        except Exception as e:
            logger.error(f"Binance WS 连接失败: {e}")
            return False

    async def listen(self):
        """监听消息"""
        if not self.ws:
            return

        try:
            async for raw_msg in self.ws:
                if not self.running:
                    break
                try:
                    msg = json.loads(raw_msg)
                    data = msg.get("data", msg)
                    await self._handle_message(data)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"消息解析跳过: {e}")
        except websockets.ConnectionClosed:
            logger.warning("Binance WS 断开连接")
            self.running = False
        except Exception as e:
            logger.error(f"WS 监听异常: {e}")
            self.running = False

    async def _handle_message(self, data: dict):
        """处理单条消息"""
        event_type = data.get("e", "")

        if event_type == "trade":
            await self._handle_trade(data)
        elif event_type == "kline":
            await self._handle_kline(data)

    async def _handle_trade(self, data: dict):
        """处理实时成交"""
        price = float(data["p"])
        timestamp = data["T"]

        self.current_price = price
        self.last_trade_time = datetime.utcfromtimestamp(timestamp / 1000).isoformat()
        self.recent_trades.append({
            "price": price,
            "timestamp": timestamp,
            "qty": float(data["q"]),
        })

        if self.on_price_update:
            self.on_price_update(price, timestamp)

    async def _handle_kline(self, data: dict):
        """处理 15 分钟 K 线"""
        k = data["k"]
        self.kline_15m = {
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "start_time": k["t"],
            "end_time": k["T"],
            "is_closed": k["x"],
            "volume": float(k["v"]),
        }

        if k["x"]:  # K 线收盘
            self.completed_klines.append(self.kline_15m.copy())
            logger.info(
                f"15m K线收盘: O={self.kline_15m['open']:.2f} "
                f"H={self.kline_15m['high']:.2f} "
                f"L={self.kline_15m['low']:.2f} "
                f"C={self.kline_15m['close']:.2f}"
            )
            if self.on_kline_close:
                self.on_kline_close(self.kline_15m.copy())

    def get_intra_15m_change(self) -> float:
        """当前 15 分钟内的涨跌幅（百分比）"""
        if self.kline_15m["open"] <= 0:
            return 0.0
        return (self.current_price - self.kline_15m["open"]) / self.kline_15m["open"]

    def get_recent_trades_change(self, seconds: int = 60) -> float:
        """最近 N 秒的涨跌幅"""
        if len(self.recent_trades) < 2:
            return 0.0

        now_ms = self.recent_trades[-1]["timestamp"]
        cutoff = now_ms - seconds * 1000

        # 找到 cutoff 之后的第一个成交
        old_price = None
        for t in self.recent_trades:
            if t["timestamp"] >= cutoff:
                old_price = t["price"]
                break

        if old_price is None or old_price == 0:
            return 0.0

        return (self.current_price - old_price) / old_price

    async def close(self):
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Binance WS 已关闭")

    async def reconnect(self):
        """重新连接"""
        logger.info("尝试重连 Binance WS...")
        await self.close()
        await asyncio.sleep(2)
        return await self.connect()
