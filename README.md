# Polymarket BTC 15-Minute Trading Bot

Automated trading bot for Polymarket BTC 15-minute up/down prediction markets.

## Strategy

Uses multi-timeframe confluence (trend + momentum + pricing deviation) to find edges in Polymarket's BTC 15-minute markets.

**Three-Layer Signal Engine:**
- **Layer 1 — Trend Direction**: 4h/12h/24h BTC price trend
- **Layer 2 — Momentum Confirmation**: Real-time BTC movement vs Price to Beat
- **Layer 3 — Pricing Deviation**: Polymarket UP/DOWN price vs estimated probability

**Safety Valves:**
- RSI overbought/oversold filter
- Price speed decay detection
- Key level proximity (round numbers, Bollinger bands, previous highs/lows)

**Additional Strategies:**
- Last-minute snipe (60s before settlement)
- Adaptive parameter optimization based on historical performance

## Markets

- 96 markets per day (every 15 minutes)
- BTC/USD: end price ≥ start price → "Up" wins
- Polymarket slug format: `btc-updown-15m-{unix_timestamp}`

## Tech Stack

- Python 3.12 + asyncio
- Binance WebSocket (real-time BTC price)
- Binance REST API (kline data for indicators)
- Polymarket Gamma API (market discovery)
- Polymarket CLOB API (trading)
- Playwright (Price to Beat extraction)
- SQLite (trades/signals/prices storage)

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install playwright && playwright install chromium

# Single scan
python main.py

# 24/7 run
python main.py --run

# Backtest
python tests/test_step12_backtest.py
```

## Configuration

Copy `.env.example` to `.env` and fill in:

```
TRADING_MODE=SIMULATION    # SIMULATION or LIVE
INITIAL_BANKROLL=100
POLYGON_PRIVATE_KEY=       # Required for LIVE mode
CLOB_API_KEY=
CLOB_API_SECRET=
CLOB_API_PASSPHRASE=
```

## License

MIT
