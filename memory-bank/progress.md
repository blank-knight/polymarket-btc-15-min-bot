# Progress — BTC 15分钟交易 Bot

## 2026-04-05

### ✅ 全部 14 步完成

| Step | 模块 | 状态 |
|------|------|------|
| 1 | 项目骨架 + 配置 | ✅ |
| 2 | Binance WS/REST + 技术指标 | ✅ |
| 3 | Polymarket 市场发现 (btc-updown-15m) | ✅ |
| 4 | Price to Beat (Playwright 常驻浏览器) | ✅ |
| 5 | 价格快照自动记录 | ✅ |
| 6 | 三层信号引擎 (趋势+动量+偏差+安全阀) | ✅ |
| 7 | Kelly 仓位计算 | ✅ |
| 8 | 风控模块 | ✅ |
| 9 | 交易执行 (模拟/实盘) | ✅ |
| 10 | 最后时刻狙击 | ✅ |
| 11 | 15分钟轮转主循环 | ✅ |
| 12 | 模拟回测 | ✅ |
| 13 | 实盘部署配置 | ✅ |
| 14 | 策略优化器 (自适应参数) | ✅ |

### 代码规模
- 28 个 Python 文件
- 2734 行代码
- 4 个测试文件
- SQLite 5 张表 (markets/prices/signals/trades/pnl_log)

### 设计文档
- ✅ program-design-document.md — 程序设计文档
- ✅ tech-stack.md — 技术栈
- ✅ implementation-plan.md — 实施计划
- ✅ memory-bank/architecture.md — 架构说明
- ✅ memory-bank/architecture.canvas — Canvas 架构图
- ✅ memory-bank/strategy-research.md — 策略研究
- ✅ memory-bank/vibe-coding-core.md — 元方法论定义
- ✅ memory-bank/g-updated-rules.md — 生成器 G v0.1 规则
- ✅ memory-bank/p-history.md — 提示词迭代历史
- ✅ memory-bank/progress.md — 进度记录

### 端到端验证
- BTC $67,063 → 不交易 (趋势混合+定价均衡 → 正确)
- 真实 Binance 数据 ✅
- 真实 Polymarket 市场发现 ✅
- 信号引擎+安全阀工作正常 ✅

### GitHub
- ✅ 已上传到 https://github.com/blank-knight/polymarket-btc-15-min-bot

### 待主人操作
- [ ] 安装 Playwright: `pip install playwright && playwright install chromium`
- [ ] 配置钱包 (.env) 用于实盘
- [ ] 部署到 VPS: `python main.py --run`

### 关键技术决策记录
- **Price to Beat 来源**: 必须从 Polymarket 页面抓取（Playwright），不能用 Binance 开盘价（数据源不同）
- **多时间框架**: 4h/12h/24h 趋势共振，不只是 15 分钟微观波动
- **用户洞察**: "长期跌 + 短期已跌 → 最后一分钟再涨概率极低" → 反转概率低
- **σ 计算**: 多模型融合用 sqrt(sum of squares) 而非 max()
- **安全阀**: RSI 75/25 + 速度衰减 + 关键位（整数/布林/前高前低）
