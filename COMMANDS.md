# BTC 15M Bot — 常用命令手册

## 📊 查看盈亏

```bash
# 进入项目目录
cd ~/clawd/polymarket-btc-15min-bot

# 查看盈亏汇总（最新一条）
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM pnl_log ORDER BY id DESC LIMIT 1"

# 查看所有盈亏记录
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM pnl_log ORDER BY id DESC"

# 查看已结算的交易
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM trades WHERE status='settled' ORDER BY id DESC LIMIT 10"

# 查看未结算的交易
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM trades WHERE status='open'"

# 查看所有交易
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM trades ORDER BY id DESC LIMIT 20"

# 统计：总交易数、胜率、总盈亏
sqlite3 -header -column src/data/btc_15m_bot.db "
SELECT 
  COUNT(*) as 总交易数,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as 盈利次数,
  SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as 亏损次数,
  ROUND(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as 胜率,
  ROUND(COALESCE(SUM(pnl), 0), 2) as 总盈亏
FROM trades WHERE status='settled'
"
```

---

## 📝 查看信号

```bash
# 最近10条信号
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM signals ORDER BY id DESC LIMIT 10"

# 只看有效信号（未被过滤的）
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM signals WHERE filtered=0 ORDER BY id DESC"

# 统计信号数量
sqlite3 -header -column src/data/btc_15m_bot.db "
SELECT 
  COUNT(*) as 总信号,
  SUM(CASE WHEN filtered=0 THEN 1 ELSE 0 END) as 有效信号,
  SUM(CASE WHEN filtered=1 THEN 1 ELSE 0 END) as 被过滤
FROM signals
"
```

---

## 📋 查看市场记录

```bash
# 查看所有市场
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM markets ORDER BY id DESC LIMIT 10"

# 查看已结算市场
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM markets WHERE result IS NOT NULL ORDER BY id DESC"
```

---

## 💰 查看价格记录

```bash
# 最近10条BTC价格
sqlite3 -header -column src/data/btc_15m_bot.db "SELECT * FROM prices ORDER BY id DESC LIMIT 10"

# 当前BTC价格（实时从Binance获取）
curl -s "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT" | python3 -m json.tool
```

---

## 📄 查看日志

```bash
# 实时查看日志（Ctrl+C退出）
tail -f src/data/bot.log

# 查看最近50行日志
tail -50 src/data/bot.log

# 查看今天的交易日志
grep "交易\|结算\|信号\|狙击" src/data/bot.log | tail -30

# 查看错误日志
grep "ERROR" src/data/bot.log | tail -20
```

---

## 🔄 启动/停止 Bot

```bash
# 启动（后台运行）
cd ~/clawd/polymarket-btc-15min-bot
nohup ./venv/bin/python main.py --run >> src/data/bot.log 2>&1 &

# 单次扫描（不下单，只看信号）
./venv/bin/python main.py

# 查看是否在运行
ps aux | grep "main.py" | grep -v grep

# 停止
pkill -f "main.py --run"
```

---

## 🗄️ 数据库操作

```bash
# 交互式进入数据库
sqlite3 src/data/btc_15m_bot.db

# 清空所有数据（谨慎！）
sqlite3 src/data/btc_15m_bot.db "DELETE FROM trades; DELETE FROM signals; DELETE FROM markets; DELETE FROM pnl_log; DELETE FROM prices;"

# 数据库文件大小
ls -lh src/data/btc_15m_bot.db
```

---

## 字段说明

### trades 表
| 字段 | 含义 |
|---|---|
| id | 交易编号 |
| market_slug | 哪个15分钟市场 |
| side | 方向：up/down |
| shares | 股数 |
| price | 买入价（0-1之间） |
| cost_usd | 花了多少美元 |
| status | open=未结算 / settled=已结算 |
| pnl | 盈亏金额（正=赚，负=亏） |
| created_at | 下单时间 |

### signals 表
| 字段 | 含义 |
|---|---|
| direction | up/down/none |
| confidence | 置信度 0-1 |
| layer1_trend | L1趋势：bullish/bearish/neutral |
| layer2_momentum | L2动量：bullish/bearish/neutral |
| rsi | RSI指标（>75过热，<25过冷） |
| edge | 优势大小（越高越好） |
| filtered | 1=被过滤没下单，0=有效信号 |

### pnl_log 表
| 字段 | 含义 |
|---|---|
| total_pnl | 累计盈亏 |
| bankroll | 当前资金 |
| trade_count | 累计交易数 |
| win_count | 盈利次数 |
| loss_count | 亏损次数 |
