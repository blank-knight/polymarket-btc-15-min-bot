"""数据库模块"""

import sqlite3
from datetime import datetime
from src.config.settings import DB_PATH
from src.utils.logger import setup_logger

logger = setup_logger("db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # 市场表
    c.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            market_id TEXT,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            price_to_beat REAL,
            up_token TEXT,
            down_token TEXT,
            up_price REAL,
            down_price REAL,
            result TEXT,           -- 'up' / 'down' / NULL(未结算)
            settled_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # BTC 价格快照
    c.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            btc_price REAL NOT NULL,
            source TEXT DEFAULT 'binance',
            volume_24h REAL
        )
    """)

    # 交易信号
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,    -- 'up' / 'down'
            confidence REAL,            -- 0.0 - 1.0
            layer1_trend TEXT,
            layer2_momentum TEXT,
            layer3_deviation REAL,
            rsi REAL,
            edge REAL,
            filtered INTEGER DEFAULT 0,
            FOREIGN KEY (market_slug) REFERENCES markets(slug)
        )
    """)

    # 交易记录
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            side TEXT NOT NULL,          -- 'up' / 'down'
            token_id TEXT,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            cost_usd REAL NOT NULL,
            status TEXT DEFAULT 'open',  -- open / settled / cancelled
            pnl REAL,
            settled_price REAL,
            settled_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (market_slug) REFERENCES markets(slug)
        )
    """)

    # PnL 日志
    c.execute("""
        CREATE TABLE IF NOT EXISTS pnl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            total_pnl REAL NOT NULL,
            bankroll REAL NOT NULL,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0
        )
    """)

    # 索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_markets_slug ON markets(slug)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_markets_start ON markets(start_time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_ts ON prices(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")

    conn.commit()
    conn.close()
    logger.info(f"数据库初始化完成: {DB_PATH}")


def insert_market(slug: str, start_time: str, end_time: str, **kwargs) -> int:
    conn = get_connection()
    c = conn.cursor()
    cols = ["slug", "start_time", "end_time"] + list(kwargs.keys())
    vals = [slug, start_time, end_time] + list(kwargs.values())
    placeholders = ", ".join(["?"] * len(cols))
    c.execute(f"INSERT OR REPLACE INTO markets ({', '.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id


def get_market(slug: str) -> dict | None:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM markets WHERE slug = ?", (slug,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def insert_trade(market_slug: str, side: str, shares: float, price: float, cost_usd: float, **kwargs) -> int:
    conn = get_connection()
    c = conn.cursor()
    cols = ["market_slug", "side", "shares", "price", "cost_usd"] + list(kwargs.keys())
    vals = [market_slug, side, shares, price, cost_usd] + list(kwargs.values())
    placeholders = ", ".join(["?"] * len(cols))
    c.execute(f"INSERT INTO trades ({', '.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id


def update_trade_pnl(trade_id: int, pnl: float, settled_price: float):
    conn = get_connection()
    conn.execute(
        "UPDATE trades SET status='settled', pnl=?, settled_price=?, settled_at=datetime('now') WHERE id=?",
        (pnl, settled_price, trade_id),
    )
    conn.commit()
    conn.close()


def get_open_trades() -> list[dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE status='open'")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def insert_signal(market_slug: str, strategy: str, direction: str, **kwargs) -> int:
    conn = get_connection()
    c = conn.cursor()
    cols = ["timestamp", "market_slug", "strategy", "direction"] + list(kwargs.keys())
    vals = [datetime.utcnow().isoformat(), market_slug, strategy, direction] + list(kwargs.values())
    placeholders = ", ".join(["?"] * len(cols))
    c.execute(f"INSERT INTO signals ({', '.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id


def get_recent_signals(limit: int = 100) -> list[dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_daily_stats(date: str = None) -> dict:
    """获取当日交易统计"""
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    conn = get_connection()
    c = conn.cursor()

    c.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(pnl), 0) as pnl FROM trades WHERE status='settled' AND settled_at LIKE ?",
        (f"{date}%",),
    )
    row = c.fetchone()

    c.execute("SELECT COUNT(*) as cnt FROM trades WHERE status='settled' AND pnl > 0 AND settled_at LIKE ?", (f"{date}%",))
    wins = c.fetchone()[0]

    conn.close()
    return {
        "date": date,
        "total_trades": row["cnt"],
        "total_pnl": row["pnl"],
        "wins": wins,
        "losses": row["cnt"] - wins,
        "win_rate": wins / row["cnt"] if row["cnt"] > 0 else 0,
    }
