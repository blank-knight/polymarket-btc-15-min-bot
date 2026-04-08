"""
Polymarket CLOB Orderbook 查询

获取真实买卖价，用于模拟盘真实价格估算
"""

import requests
from src.utils.logger import setup_logger

logger = setup_logger("orderbook")

CLOB_API_URL = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT = 8


def get_orderbook(token_id: str) -> dict | None:
    """获取某个 token 的 orderbook"""
    try:
        resp = requests.get(
            f"{CLOB_API_URL}/book",
            params={"token_id": token_id},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        asks = data.get("asks", [])
        bids = data.get("bids", [])

        if not asks and not bids:
            return None

        best_ask = float(asks[0]["price"]) if asks else 1.0
        best_bid = float(bids[0]["price"]) if bids else 0.0
        mid = (best_ask + best_bid) / 2

        ask_depth = sum(float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks[:5])
        bid_depth = sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids[:5])

        return {
            "best_ask": best_ask,
            "best_bid": best_bid,
            "ask_depth_usd": ask_depth,
            "bid_depth_usd": bid_depth,
            "mid_price": mid,
            "spread": best_ask - best_bid,
        }

    except Exception as e:
        logger.debug(f"Orderbook 查询失败 ({token_id[:12]}...): {e}")
        return None


def get_real_buy_price(token_id: str, budget_usd: float = 5.0) -> float | None:
    """
    获取真实买入价格（考虑滑点）

    模拟用 $budget_usd 买入，计算平均成交价
    """
    try:
        resp = requests.get(
            f"{CLOB_API_URL}/book",
            params={"token_id": token_id},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        asks = data.get("asks", [])
        if not asks:
            return None

        asks_sorted = sorted(asks, key=lambda x: float(x["price"]))

        remaining_budget = budget_usd
        total_shares = 0.0

        for ask in asks_sorted:
            price = float(ask["price"])
            size = float(ask.get("size", 0))
            cost_at_level = size * price

            if cost_at_level >= remaining_budget:
                shares = remaining_budget / price
                total_shares += shares
                remaining_budget = 0
                break
            else:
                total_shares += size
                remaining_budget -= cost_at_level

        if total_shares <= 0:
            return None

        avg_price = budget_usd / total_shares
        return avg_price

    except Exception as e:
        logger.debug(f"真实价格查询失败: {e}")
        return None
