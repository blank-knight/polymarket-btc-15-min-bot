"""
价格管理器

统一管理实时价格、K 线数据、技术指标缓存。
"""

import asyncio
from datetime import datetime
from dataclasses import dataclass

from src.price.binance_ws import BinancePriceStream
from src.price.binance_rest import fetch_klines
from src.utils.logger import setup_logger

logger = setup_logger("price_manager")


@dataclass
class TechnicalIndicators:
    """技术指标快照"""
    # 趋势
    trend_4h: float = 0.0       # 4 小时涨跌幅
    trend_12h: float = 0.0      # 12 小时涨跌幅
    trend_24h: float = 0.0      # 24 小时涨跌幅

    # 动量
    momentum_15m: float = 0.0   # 当前 15 分钟内涨跌幅
    speed_recent: float = 0.0   # 最近 30 分钟速度 (%/h)
    speed_older: float = 0.0    # 30-60 分钟前速度 (%/h)

    # 指标
    rsi: float = 50.0
    bollinger_upper: float = 0.0
    bollinger_lower: float = 0.0
    bollinger_mid: float = 0.0
    ma20: float = 0.0
    ma50: float = 0.0

    # 关键价位
    near_round_number: bool = False
    near_previous_high: bool = False
    near_previous_low: bool = False
    near_bollinger: bool = False

    # 当前价
    btc_price: float = 0.0


class PriceManager:
    """价格管理器"""

    def __init__(self):
        self.ws = BinancePriceStream()
        self.indicators = TechnicalIndicators()
        self._kline_cache: dict[str, list] = {}  # interval → klines
        self._update_task = None

    async def start(self):
        """启动价格流"""
        connected = await self.ws.connect()
        if not connected:
            logger.error("Binance WS 连接失败")
            return False

        # 初始加载 K 线
        await self._refresh_klines()

        # 启动后台任务
        self._update_task = asyncio.create_task(self._background_update())
        logger.info("价格管理器已启动")
        return True

    async def _background_update(self):
        """后台定期刷新 K 线数据"""
        while self.ws.running:
            try:
                # 同时监听 WS
                await self.ws.listen()
            except Exception as e:
                logger.error(f"WS 监听异常: {e}")

            # 断连后尝试重连
            if not self.ws.running:
                logger.info("尝试重连...")
                await asyncio.sleep(5)
                success = await self.ws.connect()
                if success:
                    await self._refresh_klines()

    async def _refresh_klines(self):
        """刷新 K 线缓存"""
        for interval in ["15m", "1h", "4h", "1d"]:
            klines = await fetch_klines(interval=interval, limit=100)
            if klines:
                self._kline_cache[interval] = klines

        self._update_indicators()

    def _update_indicators(self):
        """根据 K 线计算技术指标"""
        import numpy as np

        price = self.ws.current_price
        if price <= 0:
            # WS 未启动，用最近 K 线收盘价
            klines_15m = self._kline_cache.get("15m", [])
            if klines_15m:
                price = klines_15m[-1]["close"]
            if price <= 0:
                return

        self.indicators.btc_price = price

        # === 趋势 ===
        self.indicators.trend_4h = self._calc_trend("1h", 4)
        self.indicators.trend_12h = self._calc_trend("1h", 12)
        self.indicators.trend_24h = self._calc_trend("1d", 1) if "1d" in self._kline_cache else self._calc_trend("1h", 24)

        # === 动量 ===
        self.indicators.momentum_15m = self.ws.get_intra_15m_change()

        # 速度对比
        klines_15m = self._kline_cache.get("15m", [])
        if len(klines_15m) >= 4:
            recent_2 = klines_15m[-2:]  # 最近 30 分钟
            older_2 = klines_15m[-4:-2]  # 30-60 分钟前

            recent_change = sum(
                (k["close"] - k["open"]) / k["open"] for k in recent_2
            ) / len(recent_2) * 4  # %/h

            older_change = sum(
                (k["close"] - k["open"]) / k["open"] for k in older_2
            ) / len(older_2) * 4

            self.indicators.speed_recent = recent_change
            self.indicators.speed_older = older_change

        # === RSI ===
        klines_1h = self._kline_cache.get("1h", [])
        if len(klines_1h) >= 15:
            closes = [k["close"] for k in klines_1h[-15:]]
            self.indicators.rsi = self._calc_rsi(closes, period=14)

        # === 布林带 (1h K 线, 20 周期) ===
        if len(klines_1h) >= 20:
            closes = [k["close"] for k in klines_1h[-20:]]
            arr = np.array(closes)
            mid = np.mean(arr)
            std = np.std(arr)
            self.indicators.bollinger_mid = mid
            self.indicators.bollinger_upper = mid + 2 * std
            self.indicators.bollinger_lower = mid - 2 * std

            # 检查是否接近布林带
            from src.config.settings import BOLLINGER_TOUCH_RATIO
            upper_dist = abs(price - self.indicators.bollinger_upper) / price
            lower_dist = abs(price - self.indicators.bollinger_lower) / price
            self.indicators.near_bollinger = min(upper_dist, lower_dist) < BOLLINGER_TOUCH_RATIO

        # === MA ===
        if len(klines_1h) >= 50:
            closes = [k["close"] for k in klines_1h]
            self.indicators.ma20 = np.mean(closes[-20:])
            self.indicators.ma50 = np.mean(closes[-50:])

        # === 关键价位 ===
        from src.config.settings import ROUND_NUMBER_PCT

        # 整数关口
        round_num = round(price / 1000) * 1000
        self.indicators.near_round_number = abs(price - round_num) / price < ROUND_NUMBER_PCT

        # 前高前低 (24h)
        if len(klines_1h) >= 24:
            recent_24 = klines_1h[-24:]
            prev_high = max(k["high"] for k in recent_24)
            prev_low = min(k["low"] for k in recent_24)
            self.indicators.near_previous_high = abs(price - prev_high) / price < 0.005
            self.indicators.near_previous_low = abs(price - prev_low) / price < 0.005

    def _calc_trend(self, interval: str, periods: int) -> float:
        """计算 N 根 K 线的涨跌幅"""
        klines = self._kline_cache.get(interval, [])
        if len(klines) < periods:
            return 0.0

        recent = klines[-periods:]
        start_price = recent[0]["open"]
        end_price = recent[-1]["close"]

        if start_price <= 0:
            return 0.0

        return (end_price - start_price) / start_price

    @staticmethod
    def _calc_rsi(closes: list, period: int = 14) -> float:
        """计算 RSI"""
        import numpy as np

        if len(closes) < period + 1:
            return 50.0

        changes = np.diff(closes)
        gains = np.where(changes > 0, changes, 0)
        losses = np.where(changes < 0, -changes, 0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        if avg_loss == 0:
            return 100.0

        for i in range(period, len(changes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def get_indicators(self) -> TechnicalIndicators:
        """获取最新技术指标"""
        self._update_indicators()
        return self.indicators

    async def stop(self):
        await self.ws.close()
        if self._update_task:
            self._update_task.cancel()
