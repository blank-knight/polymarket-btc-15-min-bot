# Tech Stack — Polymarket BTC 15分钟交易 Bot

## 语言 & 运行时
- **Python 3.12** + asyncio（异步架构，处理双 WS）

## 数据源
| 数据 | 来源 | 接入 |
|------|------|------|
| BTC 实时价格 | Binance WebSocket | `websockets` |
| BTC K线(趋势/RSI) | Binance REST API | `aiohttp` |
| Price to Beat | Polymarket 页面 | `playwright` |
| 市场发现 | Gamma API (`/markets`) | `aiohttp` |
| UP/DOWN 价格 | Polymarket CLOB WS | `websockets` |
| 下单 | Polymarket CLOB REST | `py_clob_client` |

## 核心依赖
```
websockets       # Binance WS + Polymarket CLOB WS
aiohttp          # REST API 调用
playwright       # Price to Beat 页面解析
py_clob_client   # Polymarket 交易 SDK
pandas           # K线数据处理 / RSI 计算
numpy            # 数学计算
apscheduler      # 调度（备用，主要用 asyncio 定时器）
loguru           # 日志
```

## 存储
- **SQLite**: trades / signals / prices / pnl / markets

## 部署
- systemd / screen / supervisor
- 单进程，常驻运行
