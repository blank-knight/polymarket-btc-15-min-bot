# Architecture — Polymarket BTC 15-Minute Trading Bot

> 每个 Module / 文件的职责说明，随开发进展更新

## 系统架构

```
polymarket-btc-15min-bot/
├── memory-bank/                    # 记忆库（单一真相源）
│   ├── architecture.md             # 本文件 - 架构说明
│   ├── architecture.canvas         # Obsidian Canvas 架构图
│   ├── strategy-research.md        # 策略研究
│   ├── vibe-coding-core.md         # 元方法论核心定义
│   ├── g-updated-rules.md          # 生成器G迭代规则
│   ├── p-history.md                # 提示词P迭代历史
│   ├── implementation-plan.md      # 实施计划
│   └── progress.md                 # 进度记录
├── src/
│   ├── price/                      # 模块1: 价格数据引擎
│   │   ├── __init__.py
│   │   ├── binance_ws.py           # Binance WebSocket 实时价格+K线
│   │   ├── binance_rest.py         # Binance REST K线（15m/1h/4h/1d）
│   │   ├── price_manager.py        # 技术指标计算（趋势/RSI/布林/MA）
│   │   └── price_recorder.py       # BTC 价格定时记录（60s快照）
│   ├── market/                     # 模块2: 市场发现引擎
│   │   ├── __init__.py
│   │   ├── gamma_client.py         # Gamma API 市场发现 + slug 计算
│   │   └── price_beat_fetcher.py   # Playwright 抓取 Price to Beat
│   ├── signal/                     # 模块3: 信号引擎
│   │   ├── __init__.py
│   │   ├── signal_engine.py        # 三层信号融合（趋势+动量+定价偏差）
│   │   ├── last_minute_sniper.py   # 最后一分钟狙击模块
│   │   └── strategy_optimizer.py   # 自适应参数优化器
│   ├── decision/                   # 模块4: 决策引擎
│   │   ├── __init__.py
│   │   └── kelly_sizer.py          # Kelly Criterion 仓位计算
│   ├── execution/                  # 模块5: 交易执行引擎
│   │   ├── __init__.py
│   │   └── trader.py               # 交易执行（模拟+实盘）
│   ├── risk/                       # 模块6: 风控模块
│   │   ├── __init__.py
│   │   └── risk_manager.py         # 风控规则检查
│   ├── config/                     # 配置
│   │   ├── __init__.py
│   │   ├── settings.py             # 全局配置
│   │   └── deployment.py           # 部署配置 + systemd 模板
│   ├── utils/                      # 工具
│   │   ├── __init__.py
│   │   ├── logger.py               # 日志
│   │   └── db.py                   # SQLite 存储（5张表）
│   └── scheduler.py                # 主循环调度器（15分钟轮转）
├── tests/                          # 测试
├── main.py                         # 主入口
├── requirements.txt
└── .env.example
```

## 文件职责说明

### 模块1: 价格数据引擎 (`src/price/`)
| 文件 | 职责 |
|------|------|
| `binance_ws.py` | Binance WebSocket 实时价格流（trade + kline_15m） |
| `binance_rest.py` | Binance REST API 获取多时间框架 K 线（15m/1h/4h/1d） |
| `price_manager.py` | 技术指标计算：趋势方向（4h/12h/24h）、RSI(14)、布林带(20,2)、MA20/MA50、关键价位 |
| `price_recorder.py` | 每60秒记录 BTC 价格快照到 SQLite |

### 模块2: 市场发现引擎 (`src/market/`)
| 文件 | 职责 |
|------|------|
| `gamma_client.py` | 计算 `btc-updown-15m-{unix_timestamp}` slug，查询 Gamma API 获取 UP/DOWN token |
| `price_beat_fetcher.py` | Playwright 持久化无头浏览器，从 Polymarket 页面抓取 Price to Beat |

### 模块3: 信号引擎 (`src/signal/`)
| 文件 | 职责 |
|------|------|
| `signal_engine.py` | 三层信号融合：L1 趋势方向 + L2 动量确认 + L3 定价偏差，含安全阀（RSI/速度衰减/关键位） |
| `last_minute_sniper.py` | 结算前60s评估：BTC 方向 vs PTB + Polymarket 价格滞后，保守 Kelly sizing |
| `strategy_optimizer.py` | 基于7天胜率自适应调参：胜率>65%→激进，<45%→保守，交易过少→放宽 RSI |

### 模块4: 决策引擎 (`src/decision/`)
| 文件 | 职责 |
|------|------|
| `kelly_sizer.py` | Quarter-Kelly 仓位计算，5% 最大仓位限制 |

### 模块5: 交易执行引擎 (`src/execution/`)
| 文件 | 职责 |
|------|------|
| `trader.py` | 模拟模式（实时记录）+ 实盘模式（py_clob_client 下单） |

### 模块6: 风控 (`src/risk/`)
| 文件 | 职责 |
|------|------|
| `risk_manager.py` | 日交易限制(100)、日亏损限制(10%)、连续亏损冷却(5次→60min) |

### 调度器 (`src/scheduler.py`)
| 文件 | 职责 |
|------|------|
| `scheduler.py` | TradingLoop 类：新市场→获取PTB→策略分析(5-10min)→最后狙击(60s前)→结算 |

### 数据库（5张表）
| 表名 | 用途 |
|------|------|
| `markets` | 市场元数据（slug、token_id、PTB、开收时间） |
| `prices` | BTC 价格快照（每60秒） |
| `signals` | 信号记录（方向、强度、Edge、Kelly） |
| `trades` | 交易记录（买入价、数量、方向） |
| `pnl_log` | 盈亏日志 |

---

*本文档将随开发进展持续更新。*
