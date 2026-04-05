# Implementation Plan — BTC 15分钟交易 Bot

## Phase 1: 数据基础设施 (Step 1-5)

### Step 1: 项目骨架 + 配置
- 目录结构: src/{price,market,signal,decision,execution,risk,config,utils}
- settings.py: API 端点、交易参数、风控参数
- logger.py: 日志系统
- db.py: SQLite (trades, signals, prices, pnl, markets)
- requirements.txt
- main.py
- Python venv

### Step 2: Binance 价格流
- binance_ws.py: WebSocket 连接 BTC/USD 实时价格
  - `wss://stream.binance.com/ws/btcusdt@trade`
  - `wss://stream.binance.com/ws/btcusdt@kline_15m`
  - 自动重连
- binance_rest.py: K线数据获取
  - GET /api/v3/klines (1m/5m/15m/1h/4h/1d)
  - 用于计算趋势、RSI、布林带
- price_manager.py: 统一价格管理
  - 维护当前价格、最近 N 根 K线、技术指标缓存

### Step 3: Polymarket 市场发现
- gamma_client.py: 发现 btc-updown-15m 市场
  - GET /markets?slug_contains=btc-updown-15m
  - 解析 UP/DOWN token ID、当前价格
  - 自动计算当前/下一个市场 slug

### Step 4: Price to Beat 获取
- price_beat_fetcher.py: Playwright 常驻浏览器
  - 启动时创建 headless Chrome
  - 每 15 分钟 navigate 到新市场页面
  - CSS 选择器提取 "Price to beat" 文本
  - 解析失败则跳过该市场

### Step 5: 数据存储 + CLOB 价格
- db.py: SQLite 5 张表
  - markets: market_id, slug, start_time, end_time, price_to_beat, up_token, down_token
  - prices: timestamp, btc_price, source
  - signals: timestamp, market_id, strategy, direction, confidence
  - trades: market_id, side, price, shares, cost, pnl, status
  - pnl_log: timestamp, total_pnl, bankroll, trade_count
- clob_client.py: UP/DOWN token 价格查询
  - 复用天气 Bot 的 clob_client 逻辑

## Phase 2: 策略 + 决策 (Step 6-10)

### Step 6: 信号引擎
- trend_analyzer.py: 多时间框架趋势分析
  - Layer 1: 4h/12h/24h 趋势方向 + 涨跌幅
  - Layer 2: 当前 15 分钟内动量确认
  - Layer 3: Polymarket 定价偏差检测
- indicators.py: 技术指标
  - RSI(14): 超买超卖
  - Bollinger Bands: 上轨/下轨/中轨
  - MA(20)/MA(50): 移动均线
  - 跌速/涨速: 最近 30 分钟速度变化
- signal_generator.py: 三层信号合成
  - 三层都同意 → 强信号
  - 两层同意 → 弱信号
  - 冲突 → 不入场

### Step 7: Edge + Kelly
- edge_calculator.py: Edge = P_估计 - P_市场
  - 强信号 → P = 60-65%
  - 弱信号 → P = 55-58%
  - 无信号 → 不入场
- kelly_sizer.py: 仓位计算
  - Quarter-Kelly, 单笔上限 bankroll 5%
  - 日亏损上限 10%

### Step 8: 风控模块
- risk_manager.py:
  - 交易频率限制（单市场最多 1 笔）
  - 单日最大交易次数（100 笔）
  - 连续亏损暂停（5 次连亏 → 暂停 1h）
  - RSI 超买超卖过滤
  - 关键价位接近过滤
  - Price to beat 缺失 → 不交易
  - Gas 费利润检查

### Step 9: 交易执行
- trader.py:
  - 模拟模式: 记录到 DB，不下真单
  - 实盘模式: py_clob_client 下单
  - 仓位跟踪 + PnL 计算

### Step 10: 最后时刻狙击模块
- last_minute_sniper.py:
  - 结算前 60 秒检查
  - BTC 当前价 vs price to beat 方向明确
  - Polymarket 价格还没完全调整
  - 快速下单（< 2 秒延迟）

## Phase 3: 自动化 + 优化 (Step 11-14)

### Step 11: 主循环 + 15 分钟轮转
- scheduler.py:
  - asyncio 事件循环
  - 每 15 分钟整点触发: 新市场发现 → 获取 price to beat → 策略分析 → 下单
  - 结算前 60 秒触发: 最后狙击检查
  - 市场结算后: 记录结果 + PnL 更新
- main.py: 3 种模式
  - --once: 单次扫描
  - --run: 24/7 运行
  - --backtest: 历史回测

### Step 12: 模拟回测
- backtest.py:
  - 用历史 Binance K线数据模拟
  - 假设 Polymarket 定价（简化）
  - 跑 7 天数据验证策略
  - 输出: 胜率、PnL、最大回撤

### Step 13: 实盘部署
- 钱包配置
- systemd 服务
- 监控告警

### Step 14: 策略优化
- 自适应参数（RSI 阈值、趋势阈值）
- 策略权重动态调整
- 多币种扩展（ETH/SOL）
