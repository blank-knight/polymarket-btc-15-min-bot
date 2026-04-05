"""
风控模块
"""

from datetime import datetime
from src.config.settings import (
    TRADING_MODE,
    MAX_DAILY_TRADES,
    MAX_DAILY_LOSS_RATIO,
    CONSECUTIVE_LOSS_LIMIT,
    COOLDOWN_MINUTES,
)
from src.utils.db import get_daily_stats, get_open_trades
from src.utils.logger import setup_logger

logger = setup_logger("risk")


class RiskManager:
    def __init__(self, bankroll: float):
        self.bankroll = bankroll
        self.initial_bankroll = bankroll
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None

    def check_all(self, cost_usd: float) -> tuple[bool, str]:
        """执行所有风控检查"""

        # 1. 交易模式
        if TRADING_MODE != "LIVE":
            return True, "模拟模式通过"

        # 2. 连续亏损冷却
        if self.cooldown_until and datetime.utcnow() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.utcnow()).seconds // 60
            return False, f"冷却中 ({remaining} 分钟)"

        # 3. 日交易次数
        stats = get_daily_stats()
        if stats["total_trades"] >= MAX_DAILY_TRADES:
            return False, f"日交易次数上限 ({MAX_DAILY_TRADES})"

        # 4. 日亏损
        daily_loss = self.initial_bankroll - self.bankroll + stats["total_pnl"]
        if daily_loss >= self.initial_bankroll * MAX_DAILY_LOSS_RATIO:
            return False, f"日亏损上限 ({MAX_DAILY_LOSS_RATIO:.0%})"

        # 5. 单笔仓位
        if cost_usd > self.bankroll * 0.10:
            return False, f"单笔仓位过大 ({cost_usd:.2f} / {self.bankroll:.2f})"

        # 6. 未平仓数
        open_trades = get_open_trades()
        if len(open_trades) >= 5:
            return False, f"未平仓过多 ({len(open_trades)})"

        return True, "通过"

    def record_result(self, pnl: float):
        """记录交易结果"""
        self.bankroll += pnl

        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= CONSECUTIVE_LOSS_LIMIT:
                from datetime import timedelta
                self.cooldown_until = datetime.utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
                logger.warning(f"连亏 {CONSECUTIVE_LOSS_LIMIT} 次，冷却 {COOLDOWN_MINUTES} 分钟")
        else:
            self.consecutive_losses = 0
