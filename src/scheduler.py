"""
主循环 + 5 分钟轮转调度

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
from src.execution.trader import execute_trade, settle_trades, get_summary, place_passive_orders, cancel_all_orders
from src.price.price_recorder import PriceRecorder
from src.signal.last_minute_sniper import evaluate_snipe
from src.signal.strategy_optimizer import StrategyOptimizer
from src.config.settings import (
    INITIAL_BANKROLL, MIN_EDGE_BASE, MIN_EDGE_MIN, MIN_EDGE_MAX, VOLATILITY_LOOKBACK,
    STRONG_EDGE_THRESHOLD, MAKER_PRICE_OFFSET, PASSIVE_MM_ENABLED, PASSIVE_MM_SPREAD,
    PASSIVE_MM_SIZE_USD, TAKE_PROFIT_PRICE, TAKE_PROFIT_PCT,
    SMART_WALLET_ENABLED, SMART_WALLET_LIST, SMART_WALLET_POLL_INTERVAL,
    SMART_WALLET_MIN_CONFIDENCE, SMART_WALLET_BOOST_KELLY, SMART_WALLET_MAX_COPY_USD,
)
from src.utils.db import insert_market, get_market
from src.utils.logger import setup_logger
from src.signal.smart_wallet_tracker import create_tracker, get_tracker

logger = setup_logger("scheduler")

# 秒开策略阈值：BTC 与 PTB 偏移超过此值则开盘秒下
INSTANT_TRADE_MIN_GAP_PCT = 0.0002  # 0.02% ≈ $14 at $71K（从0.05%降低）
INSTANT_TRADE_MAX_WAIT_SECONDS = 60  # 最多等 60 秒（等 PTB 获取）


class TradingLoop:
    """5 分钟轮转交易主循环"""

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
        
        # 持仓跟踪（止盈卖出用）
        self._open_position = None  # {"side": "up", "token_id": "...", "shares": 4.0, "buy_price": 0.50, "cost_usd": 2.0}
        self._pause_until = 0  # v0.3: 连亏暂停时间戳
        
        # v0.4: 被动做市状态
        self._mm_orders = []  # 当前被动做市挂单 [{"token_id", "order_id", ...}]
        self._mm_market_slug = None  # 当前做市的市场 slug
        
        # v0.8: 聪明钱包跟踪器
        self._smart_wallet_tracker = None
        self._last_wallet_poll = 0  # 上次轮询时间
        self._cached_wallet_signals = []  # 缓存的跟单信号

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

        # v0.8: 初始化聪明钱包跟踪器
        if SMART_WALLET_ENABLED and SMART_WALLET_LIST:
            try:
                tracker = create_tracker(SMART_WALLET_LIST)
                await tracker.initialize()
                self._smart_wallet_tracker = tracker
                logger.info(f"🧠 跟踪 {len(SMART_WALLET_LIST)} 个聪明钱包")
            except Exception as e:
                logger.warning(f"聪明钱包跟踪器初始化失败: {e}")
        else:
            logger.info("🧠 聪明钱包跟单未启用 (SMART_WALLET_ENABLED=False 或无钱包配置)")

        self.running = True

        # 启动主循环
        await self._main_loop()

    def _last_slug(self) -> str | None:
        """获取上一个市场的 slug"""
        if self.current_slug:
            ts = int(self.current_slug.split("-")[-1])
            return f"btc-updown-5m-{ts - 300}"
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

                # 计算当前时间在 5 分钟窗口中的位置
                elapsed = (now - start_time).total_seconds()
                remaining = (end_time - now).total_seconds()

                # v0.8: 聪明钱包轮询
                if self._smart_wallet_tracker:
                    await self._poll_smart_wallets()

                # 🆕 全程策略监控：趋势共振 + 动量 + 定价偏差 + PTB差值
                # 不限制时间窗口，只要信号够强就下单
                if self.current_slug not in self._traded_slugs:
                    # v0.8: 先检查聪明钱包信号（可独立触发交易）
                    if self._cached_wallet_signals:
                        await self._execute_copy_trade()
                    if self.current_slug not in self._traded_slugs:
                        await self._instant_open_trade()   # PTB 差值快速检查
                        if self.current_slug not in self._traded_slugs:
                            await self._evaluate_strategy()  # 三层信号引擎

                # v0.4: 被动做市 — 无信号交易时挂双面单赚 Liquidity Rewards
                if PASSIVE_MM_ENABLED and not self._mm_orders and self.current_market:
                    if self.current_slug not in self._traded_slugs:
                        await self._place_passive_mm()

                # 结算前 60 秒: 最后狙击（仅当未交易时）
                if 0 < remaining <= 60 and self.current_slug not in self._traded_slugs:
                    await self._last_minute_snipe()

                # 实时显示 BTC 价格
                btc = self.price_manager.ws.current_price if self.price_manager.ws else 0
                if btc > 0:
                    ptb_str = f" | PTB=${self.current_ptb:,.2f}" if self.current_ptb else ""
                    remain_str = f" | 剩余{remaining:.0f}s" if remaining > 0 else ""
                    logger.info(f"💰 BTC=${btc:,.2f}{ptb_str}{remain_str}")

                # v0.4: 止盈检查 — 激活！
                if self._open_position and self.current_market and btc > 0:
                    await self._check_take_profit(btc)

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
        # v0.4: 先撤掉上一个市场的被动做市单
        if self._mm_orders and self._mm_market_slug != slug:
            for order in self._mm_orders:
                cancel_all_orders(order.get("token_id"))
            self._mm_orders = []

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

    async def _instant_open_trade(self):
        """差值狙击：全程监控 BTC vs PTB，差值够大就下单"""
        if not self.current_market or not self.current_ptb:
            return

        # v0.3: 连亏暂停检查
        import time as _t
        if _t.time() < self._pause_until:
            remaining = int((self._pause_until - _t.time()) / 60)
            if _t.time() % 300 < 10:  # 每5分钟打印一次
                logger.info(f"  ⏸️ 连亏暂停中，还需等待 {remaining} 分钟")
            return

        btc = self.price_manager.ws.current_price if self.price_manager.ws else 0
        if btc <= 0:
            return

        change = (btc - self.current_ptb) / self.current_ptb

        # 偏移太小不交易（BTC 和 PTB 太接近 = 50/50 赌博）
        if abs(change) < INSTANT_TRADE_MIN_GAP_PCT:
            return  # 差值太小，静默跳过

        direction = "up" if change > 0 else "down"

        # 简单 edge 估算
        if direction == "up":
            pm_price = self.current_market.up_price
            token = self.current_market.up_token_id
        else:
            pm_price = self.current_market.down_price
            token = self.current_market.down_token_id

        # v0.3: 价格超过买入上限则放弃（赔率不够）
        from src.config.settings import MAX_BUY_PRICE
        if pm_price >= MAX_BUY_PRICE:
            logger.info(f"  ⏭️ 秒开跳过: {direction.upper()} 价格太高 ({pm_price:.2f} > {MAX_BUY_PRICE})")
            return

        # === 流动性检查（挂限价单，只需确认 orderbook 存在）===
        try:
            from src.execution.trader import _get_clob_client
            clob = _get_clob_client()
            if clob:
                ob = clob.get_order_book(token)
                bids = ob.bids if hasattr(ob, 'bids') else []
                asks = ob.asks if hasattr(ob, 'asks') else []
                # 有 orderbook 数据就行（我们是挂限价单，不需要吃单深度）
                if not bids and not asks:
                    logger.info(f"  ⏭️ 秒开跳过: {direction.upper()} orderbook 为空")
                    return
                logger.info(f"  📊 流动性 ✅ bids={len(bids)} asks={len(asks)} levels")
        except Exception as e:
            logger.warning(f"  ⚠️ 流动性检查跳过: {e}")

        # v0.7: 动态 Edge 门槛 — 基于近期波动率自适应
        min_edge = self._calc_dynamic_edge()

        # Edge = 差值百分比放大（更敏感）
        # 0.15% 差值 → edge=0.03, 0.1% 差值 → edge=0.02
        edge = abs(change) * 20
        edge = min(edge, 0.50)

        logger.info(f"  📐 edge={edge:.3f} (change={change:+.3%}, pm={pm_price:.2f}, threshold={min_edge:.3f})")

        if edge < min_edge:
            logger.info(f"  ⏭️ 秒开跳过: Edge 不足 ({edge:.3f} < {min_edge:.3f})")
            return

        # 记录信号
        from src.utils.db import insert_signal
        insert_signal(
            market_slug=self.current_slug,
            strategy="instant_open",
            direction=direction,
            confidence=0.55 + edge * 0.3,
            edge=edge,
            filtered=0,
        )

        # Polymarket 最小下单量 5 股，确保 cost >= price * 5
        min_cost = pm_price * 5.1  # 留点余量
        cost_usd = max(min_cost, min(2.0, self.risk_mgr.bankroll * 0.2))
        cost_usd = round(cost_usd, 2)
        shares = cost_usd / pm_price

        # === 二次价格确认：下单前重新获取 BTC 价格，防止反转 ===
        btc_confirm = self.price_manager.ws.current_price if self.price_manager.ws else 0
        if btc_confirm > 0 and btc > 0:
            change_confirm = (btc_confirm - self.current_ptb) / self.current_ptb
            direction_confirm = "up" if change_confirm > 0 else "down"
            if direction != direction_confirm:
                logger.info(
                    f"  ⏭️ 秒开跳过: 方向反转! 初始{direction.upper()}(${btc:,.2f}) "
                    f"→ 当前{direction_confirm.upper()}(${btc_confirm:,.2f}) PTB=${self.current_ptb:,.2f}"
                )
                return
            # 如果差值缩小超过 50%，也放弃
            if abs(change_confirm) < abs(change) * 0.5:
                logger.info(
                    f"  ⏭️ 秒开跳过: 差值大幅缩小 {change:+.3%} → {change_confirm:+.3%}"
                )
                return

        # v0.6: 全部用 Taker 直接吃单，$52 本金挂 Maker 基本不成交
        is_maker = False
        actual_price = pm_price
        cost_usd = round(actual_price * shares, 2)

        logger.info(
            f"  ⚡ 🔴 Taker 秒开! "
            f"{direction.upper()} BTC=${btc:,.2f} PTB=${self.current_ptb:,.2f} "
            f"差={change:+.2%} @ ${pm_price:.2f} 投入=${cost_usd:.2f}"
        )

        result = execute_trade(
            market_slug=self.current_slug,
            side=direction,
            token_id=token,
            shares=shares,
            price=actual_price,
            cost_usd=cost_usd,
            edge=edge,
            maker_mode=is_maker,
        )

        # 记录持仓（用于止盈卖出）
        if result.get("status") in ("live", "simulated"):
            self._open_position = {
                "side": direction,
                "token_id": token,
                "shares": result.get("shares", shares),
                "buy_price": result.get("price", pm_price),
                "cost_usd": result.get("cost", cost_usd),
                "market_slug": self.current_slug,
            }
            logger.info(
                f"  📦 持仓记录: {direction.upper()} {self._open_position['shares']:.1f}股 "
                f"@ ${self._open_position['buy_price']:.2f}"
            )

        self._traded_slugs.add(self.current_slug)

    async def _poll_smart_wallets(self):
        """v0.8: 轮询聪明钱包，检查新交易"""
        import time as _t
        now = _t.time()
        if now - self._last_wallet_poll < SMART_WALLET_POLL_INTERVAL:
            return
        self._last_wallet_poll = now

        try:
            tracker = get_tracker()
            if not tracker:
                return
            signals = await tracker.check_for_new_trades()
            # 过滤低信心信号
            signals = [s for s in signals if s["confidence"] >= SMART_WALLET_MIN_CONFIDENCE]
            self._cached_wallet_signals = signals
            if signals:
                for s in signals:
                    logger.info(
                        f"  🧠 跟单信号: {s['wallet_name']} → {s['direction']} "
                        f"信心={s['confidence']:.2f} 胜率={s['win_rate']:.1%}"
                    )
        except Exception as e:
            logger.error(f"聪明钱包轮询失败: {e}")

    async def _execute_copy_trade(self):
        """v0.8: 执行跟单交易（聪明钱包信号触发）"""
        if not self._cached_wallet_signals or not self.current_market:
            return
        if self.current_slug in self._traded_slugs:
            return

        import time as _t
        if _t.time() < self._pause_until:
            return

        # 统计方向投票
        up_votes = sum(1 for s in self._cached_wallet_signals if s["direction"] == "UP")
        down_votes = sum(1 for s in self._cached_wallet_signals if s["direction"] == "DOWN")

        if up_votes == down_votes:
            logger.info("  ⏭️ 跟单跳过: 聪明钱包方向分歧")
            self._cached_wallet_signals = []
            return

        direction = "up" if up_votes > down_votes else "down"
        matching = [s for s in self._cached_wallet_signals if s["direction"] == direction.upper()]
        avg_confidence = sum(s["confidence"] for s in matching) / len(matching)
        max_weight = max(s["weight"] for s in matching)

        # 信号叠加：如果我们的 edge 也同方向，加大仓位
        btc = self.price_manager.ws.current_price if self.price_manager.ws else 0
        our_direction = None
        our_edge = 0
        if btc > 0 and self.current_ptb:
            change = (btc - self.current_ptb) / self.current_ptb
            our_direction = "up" if change > 0 else "down"
            our_edge = abs(change) * 20

        same_direction = our_direction == direction and our_edge > 0.01

        if direction == "up":
            pm_price = self.current_market.up_price
            token = self.current_market.up_token_id
        else:
            pm_price = self.current_market.down_price
            token = self.current_market.down_token_id

        from src.config.settings import MAX_BUY_PRICE
        if pm_price >= MAX_BUY_PRICE:
            logger.info(f"  ⏭️ 跟单跳过: {direction.upper()} 价格太高 ({pm_price:.2f})")
            self._cached_wallet_signals = []
            return

        # 仓位计算
        base_usd = min(SMART_WALLET_MAX_COPY_USD, self.risk_mgr.bankroll * 0.15)
        if same_direction:
            base_usd *= SMART_WALLET_BOOST_KELLY  # 信号叠加放大
            logger.info(f"  🚀 信号叠加! 我们的 edge={our_edge:.3f} 同方向，仓位放大 {SMART_WALLET_BOOST_KELLY}x")

        min_shares = 5.1
        cost_usd = max(pm_price * min_shares, base_usd)
        cost_usd = round(cost_usd, 2)
        shares = cost_usd / pm_price

        # Edge 估算：基于跟单信心
        edge = avg_confidence * 0.5  # 简化

        source = "combined" if same_direction else "copy"
        logger.info(
            f"  🧠 跟单执行! {direction.upper()} "
            f"来源={source} 信心={avg_confidence:.2f} "
            f"投票={up_votes}UP/{down_votes}DOWN "
            f"@ ${pm_price:.2f} 投入=${cost_usd:.2f}"
        )

        result = execute_trade(
            market_slug=self.current_slug,
            side=direction,
            token_id=token,
            shares=shares,
            price=pm_price,
            cost_usd=cost_usd,
            edge=edge,
            maker_mode=False,
        )

        if result.get("status") in ("live", "simulated"):
            self._open_position = {
                "side": direction,
                "token_id": token,
                "shares": result.get("shares", shares),
                "buy_price": result.get("price", pm_price),
                "cost_usd": result.get("cost", cost_usd),
                "market_slug": self.current_slug,
            }

        self._traded_slugs.add(self.current_slug)
        self._cached_wallet_signals = []  # 清空缓存

    def _calc_dynamic_edge(self) -> float:
        """v0.7: 基于近期波动率动态调整 edge 门槛
        波动大 → 门槛低（更容易开仓）
        波动小 → 门槛高（过滤噪音）
        """
        import numpy as np
        klines = self.price_manager._kline_cache.get("15m", [])
        if len(klines) < VOLATILITY_LOOKBACK:
            return MIN_EDGE_BASE  # 数据不够用默认值

        recent = klines[-VOLATILITY_LOOKBACK:]
        # 每根K线的振幅 (high-low)/close
        ranges = [(k["high"] - k["low"]) / k["close"] for k in recent]
        volatility = np.mean(ranges)  # 平均振幅

        # 基准: BTC 15m 平均振幅 ~0.15%
        # 波动 > 基准 → 降低门槛，波动 < 基准 → 提高门槛
        baseline_vol = 0.0015  # 0.15%
        vol_ratio = volatility / baseline_vol

        # 反比例调整：vol_ratio=2 (高波动) → edge 降低, vol_ratio=0.5 (低波动) → edge 升高
        dynamic_edge = MIN_EDGE_BASE / vol_ratio
        dynamic_edge = max(MIN_EDGE_MIN, min(MIN_EDGE_MAX, dynamic_edge))

        return dynamic_edge

    async def _check_take_profit(self, btc: float):
        """v0.4 止盈检查：持仓价格上涨就卖出锁定利润"""
        pos = self._open_position
        if not pos:
            return

        # 获取当前 Polymarket 价格
        if pos["side"] == "up":
            current_pm = self.current_market.up_price
        else:
            current_pm = self.current_market.down_price

        buy_price = pos["buy_price"]

        if buy_price <= 0:
            return

        # v0.4: 用 settings 参数
        profit_pct = (current_pm - buy_price) / buy_price

        # 条件1：盈利超过 TAKE_PROFIT_PCT
        take_profit = profit_pct >= TAKE_PROFIT_PCT
        # 条件2：价格绝对值超过 TAKE_PROFIT_PRICE
        take_profit = take_profit or current_pm >= TAKE_PROFIT_PRICE

        if not take_profit:
            return

        # 卖出！
        from src.execution.trader import sell_position
        sell_result = sell_position(
            market_slug=pos["market_slug"],
            token_id=pos["token_id"],
            shares=pos["shares"],
            sell_price=current_pm,
        )

        if sell_result.get("status") in ("live_sell", "simulated_sell"):
            revenue = sell_result.get("revenue", pos["shares"] * current_pm)
            profit = revenue - pos["cost_usd"]
            logger.info(
                f"  🎉 止盈卖出! {pos['side'].upper()} {pos['shares']:.1f}股 "
                f"买=${buy_price:.2f} → 卖=${current_pm:.2f} "
                f"利润=${profit:.2f} ({profit_pct:+.0%})"
            )
            # 清除持仓 + 标记已交易
            self._open_position = None
            self._traded_slugs.add(self.current_slug)

    async def _place_passive_mm(self):
        """v0.4: 被动做市 — 无信号时挂双面单赚 Liquidity Rewards + Spread"""
        if not self.current_market:
            return
        
        # 同一个市场只挂一次
        if self._mm_market_slug == self.current_slug:
            return
        
        results = place_passive_orders(
            market_slug=self.current_slug,
            up_token_id=self.current_market.up_token_id,
            down_token_id=self.current_market.down_token_id,
            up_price=self.current_market.up_price,
            down_price=self.current_market.down_price,
            spread=PASSIVE_MM_SPREAD,
            size_usd=PASSIVE_MM_SIZE_USD,
        )
        
        self._mm_orders = results
        self._mm_market_slug = self.current_slug

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

        result = await evaluate_snipe(
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
        # v0.4: 撤掉当前市场的被动做市单
        if self._mm_orders:
            for order in self._mm_orders:
                cancel_all_orders(order.get("token_id"))
            self._mm_orders = []

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

        # 结算交易
        settle_trades(self.current_slug, result)

        # v0.3: 连亏检测 — 连亏 N 次触发暂停
        from src.config.settings import CONSECUTIVE_LOSS_PAUSE, COOLDOWN_AFTER_LOSS
        recent = conn.execute(
            "SELECT pnl FROM trades WHERE status='settled' ORDER BY settled_at DESC LIMIT ?",
            (CONSECUTIVE_LOSS_PAUSE,)
        ).fetchall()
        conn.commit()
        recent_losses = [r["pnl"] for r in recent if r["pnl"] is not None]
        if len(recent_losses) >= CONSECUTIVE_LOSS_PAUSE and all(p < 0 for p in recent_losses[-CONSECUTIVE_LOSS_PAUSE:]):
            import time as _t
            self._pause_until = _t.time() + COOLDOWN_AFTER_LOSS * 60
            logger.warning(
                f"  🛑 连亏{CONSECUTIVE_LOSS_PAUSE}次! 暂停{COOLDOWN_AFTER_LOSS}分钟 "
                f"(恢复时间: {_t.strftime('%H:%M:%S', _t.localtime(self._pause_until))})"
            )

        # 记录 PnL 汇总
        summary = get_summary()
        conn.execute(
            "INSERT INTO pnl_log (total_pnl, bankroll, trade_count, win_count, loss_count) VALUES (?, ?, ?, ?, ?)",
            (summary["total_pnl"], self.risk_mgr.bankroll + summary["total_pnl"],
             summary["total_trades"], summary["wins"], summary["losses"])
        )
        conn.commit()
        conn.close()

        logger.info(
            f"  📊 累计: {summary['total_trades']}笔交易 | "
            f"胜率{summary['win_rate']:.0%} | "
            f"总PnL ${summary['total_pnl']:+.2f}"
        )

        # 重置
        self.current_slug = None
        self.current_market = None
        self.current_ptb = None
        self._open_position = None  # 清空持仓

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
