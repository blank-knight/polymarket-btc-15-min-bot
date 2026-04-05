# Polymarket BTC 15分钟交易 Bot 配置

import os
from pathlib import Path

# === 路径 ===
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# === 交易模式 ===
TRADING_MODE = os.getenv("TRADING_MODE", "SIMULATION")  # SIMULATION | LIVE

# === Binance API ===
BINANCE_WS_URL = "wss://stream.binance.com/ws"
BINANCE_REST_URL = "https://api.binance.com/api/v3"
BINANCE_SYMBOL = "btcusdt"

# === Polymarket API ===
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"
POLYMARKET_URL = "https://polymarket.com"

# === 钱包配置 (实盘用) ===
POLYGON_PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
API_KEY = os.getenv("CLOB_API_KEY", "")
API_SECRET = os.getenv("CLOB_API_SECRET", "")
API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE", "")

# === 资金管理 ===
INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))
KELLY_FRACTION = 0.25           # Quarter-Kelly
MAX_POSITION_RATIO = 0.05       # 单笔最大 5% bankroll
MAX_DAILY_LOSS_RATIO = 0.10     # 单日最大亏损 10%
MAX_DAILY_TRADES = 100          # 单日最大交易次数
MIN_EDGE = 0.03                 # 最小 Edge 3%
MIN_TRADE_USD = 1.0             # 最小交易金额

# === 策略参数 ===
# 趋势分析
TREND_WINDOWS = [240, 720, 1440]   # 分钟: 4h, 12h, 24h
TREND_THRESHOLD = [0.015, 0.025, 0.035]  # 对应涨幅阈值: 1.5%, 2.5%, 3.5%

# 动量确认
MOMENTUM_WINDOW = 15               # 当前 15 分钟内
MOMENTUM_THRESHOLD = 0.003         # 最小动量 0.3%

# 安全阀
RSI_OVERBOUGHT = 75                 # RSI 超买
RSI_OVERSOLD = 25                   # RSI 超卖
BOLLINGER_TOUCH_RATIO = 0.95        # 触碰布林带 95% 即视为接近
SPEED_DECAY_RATIO = 0.3             # 跌速/涨速衰减到 30% 以下视为减弱
ROUND_NUMBER_PCT = 0.003            # 距离千位整数 <0.3% 视为接近

# 最后狙击
SNIPER_TRIGGER_SECONDS = 60         # 结算前 60 秒
SNIPER_MIN_MOVE_PCT = 0.001         # BTC 至少偏移 0.1%
SNIPER_PRICE_LAG_PCT = 0.03         # Polymarket 价格滞后 3%

# 连续亏损保护
CONSECUTIVE_LOSS_LIMIT = 5          # 连亏 5 次
COOLDOWN_MINUTES = 60               # 暂停 60 分钟

# === 数据库 ===
DB_PATH = str(DATA_DIR / "btc_15m_bot.db")

# === 日志 ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = str(DATA_DIR / "bot.log")
