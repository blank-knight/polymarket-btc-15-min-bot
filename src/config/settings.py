# Polymarket BTC 15分钟交易 Bot 配置

import os
from pathlib import Path

# 自动加载 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# === 路径 ===
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# === 交易模式 ===
TRADING_MODE = os.getenv("TRADING_MODE", "SIMULATION")  # SIMULATION | LIVE

# === Binance API ===
BINANCE_WS_URL = "wss://stream.binance.us/ws"
BINANCE_REST_URL = "https://api.binance.us/api/v3"
BINANCE_SYMBOL = "btcusdt"

# === Polymarket API ===
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"
POLYMARKET_URL = "https://polymarket.com"

# === 钱包配置 (实盘用) ===
POLYGON_PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")
POLYMARKET_FUNDER = os.getenv("POLYMARKET_FUNDER", "")
CLOB_SIGNATURE_TYPE = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
API_KEY = os.getenv("CLOB_API_KEY", "")
API_SECRET = os.getenv("CLOB_API_SECRET", "")
API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE", "")

# === 资金管理 ===
INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "52"))
KELLY_FRACTION = 0.25           # Quarter-Kelly
MAX_POSITION_RATIO = 0.05       # 单笔最大 5% bankroll
MAX_DAILY_LOSS_RATIO = 0.10     # 单日最大亏损 10%
MAX_DAILY_TRADES = 200          # 单日最大交易次数（提高频率）
MIN_EDGE_BASE = 0.025             # v0.7: 动态 edge 基准值 2.5%
MIN_EDGE_MIN = 0.015             # 波动大时最低 1.5%
MIN_EDGE_MAX = 0.045             # 波动小时最高 4.5%
VOLATILITY_LOOKBACK = 20         # 用最近 20 根 5m K线算波动率
MIN_TRADE_USD = 1.0             # 最小交易金额
MAX_BUY_PRICE = 0.60            # v0.3: 最大买入价 0.60（放宽，允许中等赔率下单）
SETTLE_FEE_RATE = 0.04          # v0.3: Polymarket 结算手续费估算 4%
CONSECUTIVE_LOSS_PAUSE = 2       # v0.3: 连亏 2 次触发暂停
COOLDOWN_AFTER_LOSS = 30        # v0.3: 暂停 30 分钟（从 60 降）

# === v0.4: Taker/Maker 混合模式 ===
STRONG_EDGE_THRESHOLD = 0.05    # 强信号门槛：>= 此值用 Taker 模式（立刻吃单）
MAKER_PRICE_OFFSET = 0.01       # Maker 挂单比中间价低/高多少
MAKER_ORDER_TIMEOUT = 60        # Maker 单未成交超时秒数（之后撤单）

# === v0.4: 被动做市（Liquidity Rewards + Spread） ===
PASSIVE_MM_ENABLED = False      # 已关闭：$55本金做市风险大于收益
PASSIVE_MM_SPREAD = 0.08        # 挂单 spread：bid = mid - 0.04, ask = mid + 0.04
PASSIVE_MM_SIZE_USD = 1.5       # 每边挂单金额（USD）

# === v0.4: 止盈参数 ===
TAKE_PROFIT_PRICE = 0.72        # 持仓涨到 0.72 触发止盈
TAKE_PROFIT_PCT = 0.30          # 或者盈利 30% 触发止盈

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
CONSECUTIVE_LOSS_LIMIT = 3          # 连亏 3 次全局暂停（安全底线）
COOLDOWN_MINUTES = 60               # 暂停 60 分钟

# === 数据库 ===
DB_PATH = str(DATA_DIR / "btc_15m_bot.db")

# === v0.8: 聪明钱包跟单 ===
SMART_WALLET_ENABLED = True  # 总开关
SMART_WALLET_POLL_INTERVAL = 30     # 轮询间隔（秒）
SMART_WALLET_MIN_CONFIDENCE = 0.6    # 最低跟单信心分数
SMART_WALLET_BOOST_KELLY = 1.5       # 跟单信号叠加时 Kelly 放大系数
SMART_WALLET_MAX_COPY_USD = 3.0      # 单笔跟单最大金额
SMART_WALLET_FOLLOW_ONLY_BTC = True  # 只跟 BTC Up/Down 市场
SMART_WALLET_FOLLOW_ONLY_BUY = True  # 只跟 BUY（开仓方向）

# 聪明钱包列表（在这里配置要跟踪的钱包）
SMART_WALLET_LIST = [
    {"address": "0x08ea825d0f6189ce27c3d1168511e30072fd9984", "name": "NebulaDrive", "weight": 1.0},
]

# === 日志 ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = str(DATA_DIR / "bot.log")
