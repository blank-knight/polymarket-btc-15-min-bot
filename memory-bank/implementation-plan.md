# Implementation Plan — Polymarket BTC 15-Minute Trading Bot

> 分步实施计划。每步小而具体，包含验证测试。严禁包含代码。

---

## Phase 1: 基础设施（数据获取 + 市场连接）

### Step 1: 项目骨架 + 配置系统

**目标**: 搭建项目结构，配置系统可运行

**指令**:
- 创建 `src/` 下的所有模块目录和 `__init__.py`：price/、market/、signal/、decision/、execution/、risk/、config/、utils/
- 创建 `src/config/settings.py`，从 `.env` 读取配置（TRADING_MODE、INITIAL_BANKROLL、MIN_EDGE、KELLY_FRACTION、MAX_POSITION_RATIO、RSI阈值、SNIPER_TRIGGER_SECONDS）
- 创建 `src/utils/logger.py`，配置日志输出到 `logs/` 目录，按天轮转
- 创建 `src/utils/db.py`，初始化 SQLite 数据库，建 5 张表：`markets`、`prices`、`signals`、`trades`、`pnl_log`
- 创建 `.env.example` 模板
- 创建 `requirements.txt`
- 创建 `main.py` 骨架（先只初始化配置和日志）

**验证**:
- [ ] `pip install -r requirements.txt` 成功
- [ ] `python main.py` 输出初始化日志
- [ ] SQLite 数据库文件自动创建，5 张表存在
- [ ] 所有 `__init__.py` 可正常 import

---

### Step 2: Binance 价格数据引擎

**目标**: 能获取实时 BTC 价格和多时间框架 K 线数据

**指令**:
- 创建 `src/price/binance_ws.py`
  - 连接 Binance WebSocket（wss://stream.binance.com:9443）
  - 订阅 BTCUSDT 的 trade 流和 kline_15m 流
  - 实现自动重连机制
  - 回调函数处理实时价格更新
- 创建 `src/price/binance_rest.py`
  - 实现 `get_klines(symbol, interval, limit)` 方法
  - 支持 15m、1h、4h、1d 四个时间框架
  - 返回标准化的 K 线数据（开盘/收盘/最高/最低/成交量）
- 创建 `src/price/price_manager.py`
  - 实现 `TechnicalIndicators` 类
  - 计算 4h/12h/24h 趋势方向（简单收益率，带阈值）
  - 计算 RSI(14)
  - 计算布林带(20, 2)
  - 计算 MA20 / MA50
  - 检测关键价位（$1000 整数关口、布林带边沿、前高前低）

**验证**:
- [ ] WebSocket 能收到实时 BTC 价格
- [ ] REST API 能获取 4 个时间框架的 K 线
- [ ] RSI 计算结果与 TradingView 一致
- [ ] 布林带计算正确
- [ ] 趋势方向判断合理

---

### Step 3: Polymarket 市场发现

**目标**: 能自动发现当前活跃的 BTC 15 分钟市场

**指令**:
- 创建 `src/market/gamma_client.py`
  - 实现 `calc_current_market_slug()` — 计算 `btc-updown-15m-{unix_timestamp}`
  - 实现 `get_market_by_slug()` — 查询 Gamma API 获取市场详情
  - 返回 `BTC15mMarket` 数据类：slug、UP token ID、DOWN token ID、UP 价格、DOWN 价格
  - 实现市场列表获取（当前+未来若干个市场）
- 处理市场不存在的情况（非活跃时段）

**验证**:
- [ ] 能正确计算当前市场的 slug
- [ ] 能查询到 UP/DOWN token ID
- [ ] 能获取 Polymarket 上的 UP/DOWN 价格
- [ ] 非活跃市场能优雅处理

---

### Step 4: Price to Beat 抓取

**目标**: 从 Polymarket 页面抓取 Price to Beat（基准价格）

**指令**:
- 创建 `src/market/price_beat_fetcher.py`
  - 使用 Playwright 持久化无头浏览器
  - 导航到 BTC 15 分钟市场页面
  - 通过 CSS 选择器 + 文本搜索提取 "Price to Beat: $XX,XXX.XX"
  - 10 秒超时，失败则跳过该市场
  - 浏览器实例复用，避免每次重新启动
- **重要**: Price to Beat 是 Polymarket 的数据源，不是 Binance 开盘价

**验证**:
- [ ] 能从 Polymarket 页面抓取到 PTB
- [ ] PTB 格式正确（浮点数）
- [ ] 抓取失败时能优雅降级
- [ ] 浏览器复用正常

---

### Step 5: 价格快照记录

**目标**: 每 60 秒记录 BTC 价格快照到数据库

**指令**:
- 创建 `src/price/price_recorder.py`
  - 每 60 秒从 price_manager 获取当前 BTC 价格
  - 保存到 SQLite `prices` 表
  - 包含时间戳、价格、来源（binance_ws）
  - 自动清理超过 7 天的历史数据

**验证**:
- [ ] 价格快照正常保存到数据库
- [ ] 60 秒间隔准确
- [ ] 历史数据自动清理

---

## Phase 2: 决策 + 执行

### Step 6: 三层信号引擎

**目标**: 融合趋势、动量、定价偏差生成交易信号

**指令**:
- 创建 `src/signal/signal_engine.py`
- **Layer 1 — 趋势方向**: 4h/12h/24h 趋势共振检测
  - 3 个一致 → 强趋势
  - 2 个一致 → 弱趋势
  - 冲突 → 无信号
- **Layer 2 — 动量确认**: 当前 BTC 价格 vs Price to Beat
  - 方向一致 → 信号增强
  - 方向冲突 → 信号减弱
- **Layer 3 — 定价偏差**: Polymarket UP/DOWN 价格 vs 估计真实概率
- **安全阀**:
  - RSI > 75 → 不追涨
  - RSI < 25 → 不追跌
  - 价格速度衰减 → 趋势可能衰竭
  - 接近关键位 → 信号降权 50%
- 输出：Signal（方向、强度、Edge、置信度）

**验证**:
- [ ] 三层融合逻辑正确
- [ ] 安全阀有效阻止危险信号
- [ ] Strong/Weak/None 分类正确
- [ ] 混合趋势时正确输出"无信号"

---

### Step 7: Kelly 仓位计算

**目标**: 根据 Edge 和胜率计算最优仓位

**指令**:
- 创建 `src/decision/kelly_sizer.py`
- 实现标准 Kelly Criterion：f* = (p*b - q) / b
- 使用 Quarter-Kelly（KELLY_FRACTION = 0.25）
- 单笔最大仓位 5%（MAX_POSITION_RATIO = 0.05）
- bankroll 从数据库读取

**验证**:
- [ ] Kelly 公式计算正确
- [ ] 不超过 5% 仓位上限
- [ ] Edge 越大仓位越大（正相关）

---

### Step 8: 风控模块

**目标**: 交易前强制风控检查

**指令**:
- 创建 `src/risk/risk_manager.py`
- 实现风控规则：
  - 日交易次数 ≤ 100
  - 日亏损 ≤ 10% bankroll
  - 连续亏损 5 次 → 暂停 60 分钟
  - 单笔仓位 ≤ 5% bankroll
- 所有交易必须通过全部检查

**验证**:
- [ ] 超过日交易限制时拒绝
- [ ] 日亏损超限时暂停
- [ ] 连续亏损冷却正常

---

### Step 9: 交易执行引擎

**目标**: 执行交易（模拟模式 + 实盘占位）

**指令**:
- 创建 `src/execution/trader.py`
- 模拟模式：记录交易但不实际下单
  - 记录买入价、数量、方向、时间
  - 跟踪模拟持仓
- 实盘模式：通过 py_clob_client 下单（占位）
- 所有交易记录保存到 `trades` 和 `pnl_log` 表

**验证**:
- [ ] 模拟模式正确记录交易
- [ ] 盈亏计算正确
- [ ] 交易记录保存到数据库

---

### Step 10: 最后时刻狙击模块

**目标**: 结算前 60 秒评估最后机会

**指令**:
- 创建 `src/signal/last_minute_sniper.py`
- 结算前 60 秒触发评估
- 检查 BTC 当前方向 vs Price to Beat
- 检查 Polymarket UP/DOWN 价格是否有明显偏差
- 使用更保守的 Kelly（Quarter-Kelly 的 50%）
- 条件不满足则不触发

**验证**:
- [ ] 60 秒窗口触发正确
- [ ] 方向判断准确
- [ ] 保守 sizing 生效

---

## Phase 3: 自动化运行

### Step 11: 15 分钟轮转主循环

**目标**: Bot 按 15 分钟周期自动运行

**指令**:
- 创建 `src/scheduler.py` — `TradingLoop` 类
- 每 15 分钟一个周期：
  1. 新市场开始 → 获取市场信息
  2. 前 5 分钟 → 获取 Price to Beat
  3. 5-10 分钟 → 策略分析（信号引擎 + Kelly + 风控）
  4. 如有信号 → 执行交易
  5. 最后 60 秒 → 狙击评估
  6. 市场结算 → 记录结果
- 支持 `--run` 参数启动 24/7 模式
- 异常处理和自动恢复

**验证**:
- [ ] 15 分钟周期正确执行
- [ ] 各阶段时间点准确
- [ ] 异常不导致崩溃

---

### Step 12: 回测

**目标**: 用历史数据验证策略

**指令**:
- 创建 `tests/test_step12_backtest.py`
- 使用 Binance 历史 K 线数据（过去 7 天）
- 模拟简化 Polymarket 定价（基于历史波动率）
- 统计信号数量、胜率、模拟盈亏
- 使用 80% 的窗口数据估计方向（模拟真实决策不确定性）

**验证**:
- [ ] 回测完整运行
- [ ] 胜率在合理范围（50-65%）
- [ ] 模拟盈亏计算正确

---

### Step 13: 部署配置

**目标**: 准备实盘部署

**指令**:
- 创建 `src/config/deployment.py`
- 实现 `check_live_config()` — 检查实盘所需配置是否完整
- 实现 `get_clob_client()` — 初始化 Polymarket CLOB 客户端
- 生成 systemd service 模板
- 配置日志轮转

**验证**:
- [ ] 配置检查正确
- [ ] systemd 模板可用

---

### Step 14: 策略优化器

**目标**: 根据历史表现自适应调整参数

**指令**:
- 创建 `src/signal/strategy_optimizer.py`
- 实现 `AdaptiveParams` 类
- 基于过去 7 天表现调整：
  - 胜率 > 65% → 降低阈值（更激进）
  - 胜率 < 45% → 提高阈值（更保守）
  - 交易频率过低 → 放宽 RSI 阈值
- 记录参数调整历史

**验证**:
- [ ] 自适应逻辑正确
- [ ] 参数调整有记录
- [ ] 不同市场环境下参数自动适应

---

## 里程碑检查点

| 阶段 | 步骤 | 交付物 | 检查点 |
|------|------|--------|--------|
| Phase 1 | Step 1-5 | 数据基础设施 | 能获取 BTC 价格 + Polymarket 市场 + PTB |
| Phase 2 | Step 6-10 | 决策 + 执行 | 三层信号 + Kelly + 风控 + 狙击 |
| Phase 3 | Step 11-14 | 自动化运行 | 15分钟轮转 + 回测 + 部署 + 自适应 |

---

## 关键设计约束

1. **Price to Beat 必须从 Polymarket 抓取**，不能用 Binance 开盘价
2. **多时间框架趋势**：4h/12h/24h，不只是 15 分钟微观波动
3. **安全阀必须存在**：RSI、速度衰减、关键位
4. **Quarter-Kelly 保守下注**：高频市场需要控制回撤
5. **模拟先行**：Paper trading 验证策略后才考虑实盘
