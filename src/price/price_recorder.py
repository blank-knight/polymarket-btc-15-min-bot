"""
价格快照自动保存

定期将 BTC 价格、K 线数据保存到 SQLite，用于回测和分析。
"""

import asyncio
from datetime import datetime, timezone

from src.price.price_manager import PriceManager
from src.utils.db import get_connection
from src.utils.logger import setup_logger

logger = setup_logger("price_recorder")


class PriceRecorder:
    """价格快照记录器"""

    def __init__(self, pm: PriceManager, interval_seconds: int = 60):
        self.pm = pm
        self.interval = interval_seconds
        self.running = False
        self._task = None

    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"价格记录器启动 (每 {self.interval}s)")

    async def _loop(self):
        while self.running:
            try:
                price = self.pm.ws.current_price
                if price <= 0:
                    # 用 K 线兜底
                    klines = self.pm._kline_cache.get("15m", [])
                    if klines:
                        price = klines[-1]["close"]

                if price > 0:
                    conn = get_connection()
                    conn.execute(
                        "INSERT INTO prices (timestamp, btc_price, source) VALUES (?, ?, ?)",
                        (datetime.now(timezone.utc).isoformat(), price, "binance"),
                    )
                    conn.commit()
                    conn.close()

            except Exception as e:
                logger.debug(f"价格记录失败: {e}")

            await asyncio.sleep(self.interval)

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
