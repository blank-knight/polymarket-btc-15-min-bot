"""
策略优化 + 自适应参数

根据历史交易表现动态调整:
- 趋势阈值
- RSI 超买超卖阈值
- Kelly 分数
- Edge 阈值
"""

from datetime import datetime, timezone
from dataclasses import dataclass

from src.utils.db import get_daily_stats, get_connection
from src.utils.logger import setup_logger

logger = setup_logger("optimizer")


@dataclass
class AdaptiveParams:
    """自适应参数"""
    trend_threshold_4h: float = 0.015
    trend_threshold_12h: float = 0.025
    trend_threshold_24h: float = 0.035
    rsi_overbought: float = 75.0
    rsi_oversold: float = 25.0
    kelly_fraction: float = 0.25
    min_edge: float = 0.03
    sniper_min_move: float = 0.003

    # 表现追踪
    total_wins: int = 0
    total_losses: int = 0
    recent_win_rate: float = 0.5


class StrategyOptimizer:
    """策略优化器"""

    def __init__(self):
        self.params = AdaptiveParams()
        self._last_update = None

    def should_update(self) -> bool:
        """是否需要更新参数（每天一次）"""
        if self._last_update is None:
            return True
        now = datetime.now(timezone.utc)
        return (now - self._last_update).days >= 1

    def update_from_history(self):
        """根据历史表现更新参数"""
        if not self.should_update():
            return

        conn = get_connection()
        c = conn.cursor()

        # 最近 7 天统计
        c.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl), 0) as avg_pnl
            FROM trades 
            WHERE status='settled' 
            AND settled_at > datetime('now', '-7 days')
        """)
        row = c.fetchone()

        if row["total"] < 10:
            conn.close()
            return  # 数据太少，不调整

        win_rate = row["wins"] / row["total"]
        avg_pnl = row["avg_pnl"]
        total_pnl = row["total_pnl"]

        self.params.total_wins = row["wins"]
        self.params.total_losses = row["losses"]
        self.params.recent_win_rate = win_rate

        logger.info(f"策略优化: {row['total']} 笔, 胜率={win_rate:.1%}, PnL=${total_pnl:+.2f}")

        # === 根据表现调整参数 ===

        # 1. 胜率太高 (>65%) → 可以更激进
        if win_rate > 0.65 and total_pnl > 0:
            self.params.kelly_fraction = min(0.40, self.params.kelly_fraction + 0.02)
            self.params.min_edge = max(0.02, self.params.min_edge - 0.005)
            logger.info(f"  → 胜率高，增加激进度: Kelly={self.params.kelly_fraction:.2f}, Edge={self.params.min_edge:.3f}")

        # 2. 胜率太低 (<45%) → 更保守
        elif win_rate < 0.45 or total_pnl < 0:
            self.params.kelly_fraction = max(0.10, self.params.kelly_fraction - 0.05)
            self.params.min_edge = min(0.08, self.params.min_edge + 0.01)
            # 收紧 RSI 阈值
            self.params.rsi_overbought = min(80, self.params.rsi_overbought + 2)
            self.params.rsi_oversold = max(20, self.params.rsi_oversold - 2)
            logger.info(f"  → 胜率低，增加保守度: Kelly={self.params.kelly_fraction:.2f}, Edge={self.params.min_edge:.3f}")

        # 3. RSI 过滤太严（交易太少）→ 放宽
        if row["total"] < 20 and win_rate > 0.50:
            self.params.rsi_overbought = min(82, self.params.rsi_overbought + 3)
            self.params.rsi_oversold = max(18, self.params.rsi_oversold - 3)
            logger.info(f"  → 交易太少，放宽RSI: {self.params.rsi_oversold:.0f}/{self.params.rsi_overbought:.0f}")

        # 4. 狙击表现
        c.execute("""
            SELECT COUNT(*) as total, 
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades 
            WHERE status='settled' 
            AND market_slug IN (SELECT market_slug FROM signals WHERE strategy='last_minute_snipe')
            AND settled_at > datetime('now', '-7 days')
        """)
        snipe_row = c.fetchone()
        if snipe_row["total"] > 5:
            snipe_wr = snipe_row["wins"] / snipe_row["total"]
            if snipe_wr < 0.50:
                self.params.sniper_min_move = min(0.005, self.params.sniper_min_move + 0.001)
                logger.info(f"  → 狙击胜率低，提高阈值: {self.params.sniper_min_move:.4f}")

        conn.close()
        self._last_update = datetime.now(timezone.utc)

    def get_params(self) -> AdaptiveParams:
        """获取当前参数"""
        self.update_from_history()
        return self.params

    def print_status(self):
        """打印优化状态"""
        p = self.params
        print(f"\n📊 策略参数状态")
        print(f"  趋势阈值: 4h={p.trend_threshold_4h:.1%} 12h={p.trend_threshold_12h:.1%} 24h={p.trend_threshold_24h:.1%}")
        print(f"  RSI: {p.rsi_oversold:.0f} / {p.rsi_overbought:.0f}")
        print(f"  Kelly: {p.kelly_fraction:.0%}")
        print(f"  最小Edge: {p.min_edge:.1%}")
        print(f"  狙击最小偏移: {p.sniper_min_move:.2%}")
        print(f"  近期胜率: {p.recent_win_rate:.1%} ({p.total_wins}W/{p.total_losses}L)")
