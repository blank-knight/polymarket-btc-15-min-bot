"""
主循环 + 15 分钟轮转调度

核心流程:
1. 启动 Binance WS 价格流
2. 每 15 分钟整点:
   a. 计算新市场 slug
   b. 从 Polymarket 发现市场 (UP/DOWN token)
   c. 获取 Price to Beat
   d. 生成信号 (三层 + 安全阀)
   e. 计算仓位 (Kelly)
   f. 风控检查
   g. 执行交易
3. 每 15 分钟结束:
   a. 判断结算结果
   b. 更新 PnL
4. 结算前 60 秒: 最后狙击检查
"""

import asyncio
from datetime import datetime, timezone, timedelta

from src.price.price_manager import PriceManager
from src.market.gamma_client import (
    calc_current_market_slug,
    calc_next_market_slug,
    get_market_by_slug,
)
from src.market.price_beat_fetcher import PriceBeatFetcher
from src.signal.signal_engine import generate_signal, SignalStrength
from src.decision.kelly_sizer import calculate_position
from src.risk.risk_manager import RiskManager
from src.execution.trader import execute_trade, settle_trades, get_summary
from src.price.price_recorder import PriceRecorder
from src.signal.last_minute_sniper import evaluate_snipe
from src.signal.strategy_optimizer import StrategyOptimizer
from src.config.settings import INITIAL_BANKROLL, MIN_EDGE
from src.utils.db import insert_market, get_market
from src.utils.logger import setup_logger

logger = setup_logger("scheduler")


class TradingLoop:
    """15 分钟轮转交易主循环"""

    def __init__(self):
        self.price_manager = PriceManager()
        self.price_beat = PriceBeatFetcher()
        self.price_recorder = None
        self.risk_mgr = RiskManager(bankroll=INITIAL_BANKROLL)
        self.optimizer = StrategyOptimizer()
        self.running = False

        # 当前市场状态
        self.current_slug = None
        self.current_market = None
        self.current_ptb = None
        self._traded_slugs: set[str] = set()  # 已交易过的市场，防止重复下单

    async def start(self):
        """启动交易循环"""
        logger.info("=" * 60)
        logger.info("BTC 15M Bot 启动")
        logger.info("=" * 60)

        # 启动价格管理器
        pm_ok = await self.price_manager.start()
        if not pm_ok:
            logger.error("价格管理器启动失败")
            return

        # 启动价格记录器
        self.price_recorder = PriceRecorder(self.price_manager, interval_seconds=60)
        await self.price_recorder.start()

        # 启动 Price to Beat 获取器
        pb_ok = await self.price_beat.start()
        if not pb_ok:
            logger.warning("Price to Beat 获取器不可用，将使用 K 线动量替代")

        # 策略优化器
        self.optimizer.update_from_history()

        self.running = True

        # 启动主循环
        await self._main_loop()

    def _last_slug(self) -> str | None:
        """获取上一个市场的 slug"""
        if self.current_slug:
            ts = int(self.current_slug.split("-")[-1])
            return f"btc-updown-15m-{ts - 900}"
        return None

    async def _main_loop(self):
        """主事件循环"""
        while self.running:
            try:
                # 计算当前市场信息
                slug, start_ts, end_ts = calc_current_market_slug()
                now = datetime.now(timezone.utc)
                start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc)
                end_time = datetime.fromtimestamp(end_ts, tz=timezone.utc)

                # 检查是否需要初始化新市场
                if slug != self.current_slug:
                    # 先结算上一个市场
                    if self.current_slug:
                        await self._on_market_settle()
                    await self._on_new_market(slug, start_ts, end_ts)

                # 计算当前时间在 15 分钟窗口中的位置
                elapsed = (now - start_time).total_seconds()
                remaining = (end_time - now).total_seconds()

                # 结算前 60 秒: 最后狙击
                if 0 < remaining <= 60:
                    await self._last_minute_snipe()

                # 中期: 策略分析 (在第 5-10 分钟)
                if 300 <= elapsed <= 600 and self.current_market:
                    await self._evaluate_strategy()

                # 实时显示 BTC 价格
                btc = self.price_manager.ws.current_price if self.price_manager.ws else 0
                if btc > 0:
                    ptb_str = f" | PTB=${self.current_ptb:,.2f}" if self.current_ptb else ""
                    remain_str = f" | 剩余{remaining:.0f}s" if remaining > 0 else ""
                    logger.info(f"💰 BTC=${btc:,.2f}{ptb_str}{remain_str}")

                # 每 10 秒检查一次
                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                await asyncio.sleep(10)

    async def _on_new_market(self, slug: str, start_ts: int, end_ts: int):
        """新市场开始"""
        self.current_slug = slug
        logger.info(f"\n{'─' * 60}")
        logger.info(f"📊 新市场: {slug}")

        # 从 Gamma API 获取市场信息
        market = await get_market_by_slug(slug)
        if market:
            self.current_market = market
            logger.info(f"  UP: {market.up_price:.2f} | DOWN: {market.down_price:.2f}")

            # 存入 DB
            insert_market(
                slug=slug,
                start_time=datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
                end_time=datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
                price_to_beat=self.current_ptb,
                up_token=market.up_token_id,
                down_token=market.down_token_id,
            )
        else:
            logger.warning(f"  市场未找到: {slug}")
            self.current_market = None

        # 获取 Price to Beat
        ptb = await self.price_beat.fetch_price_to_beat(slug)
        if ptb:
            self.current_ptb = ptb
        else:
            logger.warning("  Price to Beat 获取失败，使用 K 线动量替代")
            self.current_ptb = None

    async def _evaluate_strategy(self):
        """策略分析"""
        if not self.current_market:
            return

        # 检查当前市场是否已下过单（防止重复）
        if self.current_slug in self._traded_slugs:
            return

        ind = self.price_manager.get_indicators()

        # 生成信号
        signal = generate_signal(
            indicators=ind,
            up_price=self.current_market.up_price,
            down_price=self.current_market.down_price,
            btc_current=ind.btc_price,
            price_to_beat=self.current_ptb,
        )

        # 记录信号
        from src.utils.db import insert_signal
        insert_signal(
            market_slug=self.current_slug,
            strategy="multi_timeframe",
            direction=signal.direction or "none",
            confidence=signal.confidence,
            layer1_trend=signal.layer1_trend,
            layer2_momentum=signal.layer2_momentum,
            layer3_deviation=signal.layer3_deviation,
            rsi=ind.rsi,
            edge=signal.edge,
            filtered=0 if signal.should_trade else 1,
        )

        if not signal.should_trade:
            if signal.safety_skip_reason:
                logger.info(f"  ⏭️ 跳过: {signal.safety_skip_reason}")
            return

        # 计算仓位
        position = calculate_position(
            direction=signal.direction,
            confidence=signal.confidence,
            up_price=self.current_market.up_price,
            down_price=self.current_market.down_price,
            up_token=self.current_market.up_token_id,
            down_token=self.current_market.down_token_id,
            bankroll=self.risk_mgr.bankroll,
            edge=signal.edge,
        )

        if not position or not position.should_trade:
            return

        # 风控检查
        ok, reason = self.risk_mgr.check_all(position.cost_usd)
        if not ok:
            logger.info(f"  🛡️ 风控拦截: {reason}")
            return

        # 执行交易
        result = execute_trade(
            market_slug=self.current_slug,
            side=position.side,
            token_id=position.token,
            shares=position.shares,
            price=position.price,
            cost_usd=position.cost_usd,
            edge=position.edge,
        )

        logger.info(
            f"  📈 {signal.strength.value} 信号: {signal.direction.upper()} "
            f"置信度={signal.confidence:.1%} Edge={signal.edge:+.1%}"
        )

        # 标记已交易
        self._traded_slugs.add(self.current_slug)

    async def _last_minute_snipe(self):
        """最后 60 秒狙击"""
        # 已经交易过则跳过
        if self.current_slug in self._traded_slugs:
            return

        if not self.current_market or not self.current_ptb:
            return

        ind = self.price_manager.get_indicators()
        btc = ind.btc_price

        if btc <= 0:
            return

        result = evaluate_snipe(
            btc_current=btc,
            price_to_beat=self.current_ptb,
            up_price=self.current_market.up_price,
            down_price=self.current_market.down_price,
            up_token=self.current_market.up_token_id,
            down_token=self.current_market.down_token_id,
            market_slug=self.current_slug,
            risk_mgr=self.risk_mgr,
            bankroll=self.risk_mgr.bankroll,
        )

        if result and result.should_snipe:
            logger.info(f"  🎯 狙击执行: {result.direction.upper()} Edge={result.edge:+.1%}")
            self._traded_slugs.add(self.current_slug)

    async def _on_market_settle(self):
        """市场结算"""
        if not self.current_market or not self.current_ptb:
            self.current_slug = None
            self.current_market = None
            self.current_ptb = None
            return

        btc = self.price_manager.ws.current_price
        if btc <= 0:
            self.current_slug = None
            self.current_market = None
            self.current_ptb = None
            return

        # 判断结果
        result = "up" if btc >= self.current_ptb else "down"
        logger.info(f"  ⚖️ 结算: {result.upper()} (BTC=${btc:,.2f} vs PTB=${self.current_ptb:,.2f})")

        # 更新市场结果
        from src.utils.db import get_connection
        conn = get_connection()
        conn.execute("UPDATE markets SET result=?, settled_at=datetime('now') WHERE slug=?", (result, self.current_slug))
        conn.commit()
        conn.close()

        # 结算交易
        settle_trades(self.current_slug, result)

        # 记录 PnL 汇总
        summary = get_summary()
        from src.utils.db import get_connection as gc
        conn2 = gc()
        conn2.execute(
            "INSERT INTO pnl_log (total_pnl, bankroll, trade_count, win_count, loss_count) VALUES (?, ?, ?, ?, ?)",
            (summary["total_pnl"], self.risk_mgr.bankroll + summary["total_pnl"],
             summary["total_trades"], summary["wins"], summary["losses"])
        )
        conn2.commit()
        conn2.close()

        logger.info(
            f"  📊 累计: {summary['total_trades']}笔交易 | "
            f"胜率{summary['win_rate']:.0%} | "
            f"总PnL ${summary['total_pnl']:+.2f}"
        )

        # 重置
        self.current_slug = None
        self.current_market = None
        self.current_ptb = None

    async def stop(self):
        self.running = False
        await self.price_manager.stop()
        await self.price_beat.stop()
        logger.info("Bot 已停止")


async def run():
    loop = TradingLoop()
    try:
        await loop.start()
    except KeyboardInterrupt:
        await loop.stop()
