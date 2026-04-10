"""
聪明钱包跟踪器 — 监控 Polymarket 上高胜率交易者的活动

功能:
1. 定期拉取指定钱包的交易记录
2. 分析策略：胜率、方向偏好、仓位大小
3. 发现新交易时发出跟单信号
4. 与 Bot 自有 edge 信号叠加，增强信心
"""

import time
import requests
from typing import Optional
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("smart_wallet")


@dataclass
class SmartWallet:
    """聪明钱包配置"""
    address: str              # 钱包地址 (0x...)
    name: str                 # 别名
    min_win_rate: float = 0.6 # 最低胜率门槛
    min_trades: int = 20      # 最少交易笔数（样本不足不跟）
    weight: float = 1.0       # 跟单权重（多钱包时按权重分配）


@dataclass
class WalletTrade:
    """单笔交易"""
    timestamp: int
    market_slug: str
    side: str          # BUY / SELL
    direction: str     # UP / DOWN
    price: float
    size: float        # 股数
    size_usd: float    # 美元金额
    asset_id: str


@dataclass
class WalletStats:
    """钱包统计"""
    address: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_size_usd: float = 0.0
    btc_market_ratio: float = 0.0  # BTC 市场占比
    last_direction: Optional[str] = None  # 最近一次方向
    last_trade_ts: int = 0
    recent_trades: list = field(default_factory=list)  # 最近 N 笔


class SmartWalletTracker:
    """聪明钱包跟踪器"""

    DATA_API = "https://data-api.polymarket.com"

    def __init__(self, wallets: list[SmartWallet], poll_interval: int = 30):
        """
        Args:
            wallets: 聪明钱包列表
            poll_interval: 轮询间隔（秒）
        """
        self.wallets = wallets
        self.poll_interval = poll_interval
        self.stats: dict[str, WalletStats] = {}
        self.last_seen_ts: dict[str, int] = {}  # 上次看到的最新交易时间
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})
        self._initialized = False

    async def initialize(self):
        """启动时加载所有钱包的历史数据"""
        logger.info(f"🧠 聪明钱包跟踪器启动，监控 {len(self.wallets)} 个钱包")
        for w in self.wallets:
            try:
                stats = self._fetch_and_analyze(w.address)
                if stats:
                    self.stats[w.address] = stats
                    trades = stats.recent_trades
                    if trades:
                        self.last_seen_ts[w.address] = max(t.timestamp for t in trades)
                    else:
                        self.last_seen_ts[w.address] = int(time.time())
                    logger.info(
                        f"  📊 {w.name}: {stats.total_trades}笔 | "
                        f"胜率 {stats.win_rate:.1%} | "
                        f"BTC占比 {stats.btc_market_ratio:.1%}"
                    )
                else:
                    logger.warning(f"  ⚠️ {w.name}: 无数据")
            except Exception as e:
                logger.error(f"  ❌ {w.name} 初始化失败: {e}")

        self._initialized = True
        logger.info("🧠 聪明钱包跟踪器就绪")

    def _fetch_trades(self, address: str, limit: int = 200) -> list[dict]:
        """从 Polymarket data-api 拉取交易记录"""
        try:
            r = self._session.get(
                f"{self.DATA_API}/trades",
                params={"user": address, "limit": limit},
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            else:
                logger.warning(f"API 返回 {r.status_code}: {address}")
                return []
        except Exception as e:
            logger.error(f"拉取交易失败 {address}: {e}")
            return []

    def _fetch_positions(self, address: str) -> list[dict]:
        """拉取当前持仓"""
        try:
            r = self._session.get(
                f"{self.DATA_API}/positions",
                params={"user": address, "limit": 100},
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            return []
        except Exception:
            return []

    def _classify_market(self, market_slug: str) -> Optional[str]:
        """判断市场类型 — 只关注 BTC Up/Down"""
        slug_lower = market_slug.lower() if market_slug else ""
        if "btc" in slug_lower and ("up" in slug_lower or "down" in slug_lower):
            return "BTC"
        return None

    def _infer_direction(self, trade: dict) -> str:
        """从交易记录推断方向 (UP/DOWN)"""
        # Polymarket 的 asset 对应条件 token
        # 通常 UP token 的 asset ID 和 DOWN 不同
        side = trade.get("side", "").upper()
        # 从 market slug 推断
        slug = (trade.get("market", "") or trade.get("slug", "")).lower()
        if "up" in slug:
            return "UP"
        elif "down" in slug:
            return "DOWN"
        return "UNKNOWN"

    def _fetch_and_analyze(self, address: str) -> Optional[WalletStats]:
        """拉取并分析钱包数据"""
        raw_trades = self._fetch_trades(address)
        if not raw_trades:
            return None

        stats = WalletStats(address=address)
        btc_trades = []
        all_trades = []

        for t in raw_trades:
            slug = t.get("market", "") or t.get("slug", "")
            side = t.get("side", "").upper()
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
            size_usd = price * size
            timestamp = int(t.get("timestamp", 0) or t.get("createdAt", 0))
            asset_id = t.get("asset", "")

            market_type = self._classify_market(slug)

            wt = WalletTrade(
                timestamp=timestamp,
                market_slug=slug,
                side=side,
                direction=self._infer_direction(t),
                price=price,
                size=size,
                size_usd=size_usd,
                asset_id=asset_id,
            )
            all_trades.append(wt)

            if market_type == "BTC":
                btc_trades.append(wt)

        stats.total_trades = len(all_trades)
        stats.btc_market_ratio = len(btc_trades) / max(len(all_trades), 1)

        # 分析 BTC 市场的胜率
        # 通过看每笔 BUY 的后续结算来判定赢输
        # 简化版：BUY price < 0.5 视为赌方向性，如果方向对就赢
        wins = 0
        losses = 0
        pnl = 0.0
        total_size = 0.0

        for bt in btc_trades:
            total_size += bt.size_usd
            if bt.side == "BUY":
                if bt.price <= 0.50:
                    # 低价买入 → 赌小概率事件（方向极端）
                    # 无法直接判定，标记为未知
                    pass
                else:
                    # 高价买入 → 赌大概率事件
                    pass
                # 真正的赢输需要看结算，先用简化逻辑
            elif bt.side == "SELL":
                # 卖出通常是止盈或止损
                pass

        # 更准确的胜率：通过买入价分析
        # 如果大量买入价在 0.50-0.90，说明在做高确定性方向
        buy_prices = [bt.price for bt in btc_trades if bt.side == "BUY"]
        if buy_prices:
            stats.avg_size_usd = total_size / max(len(btc_trades), 1)

        # 最近 50 笔交易
        stats.recent_trades = sorted(all_trades, key=lambda x: x.timestamp, reverse=True)[:50]

        if stats.recent_trades:
            stats.last_direction = stats.recent_trades[0].direction
            stats.last_trade_ts = stats.recent_trades[0].timestamp

        # 简化胜率计算：通过 positions API
        self._enrich_stats_from_positions(stats, address)

        return stats

    def _enrich_stats_from_positions(self, stats: WalletStats, address: str):
        """从持仓数据补充统计"""
        positions = self._fetch_positions(address)
        realized_pnl = 0.0
        wins = 0
        losses = 0

        for p in positions:
            cur_value = float(p.get("curPrice", 0)) * float(p.get("size", 0))
            invested = float(p.get("avgPrice", 0)) * float(p.get("size", 0))
            pnl = cur_value - invested

            # 已结算的仓位
            if p.get("closed", False) or float(p.get("curPrice", 0)) in (0.0, 1.0):
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
                realized_pnl += pnl

        if wins + losses > 0:
            stats.wins = wins
            stats.losses = losses
            stats.win_rate = wins / (wins + losses)
        stats.total_pnl = realized_pnl

    async def check_for_new_trades(self) -> list[dict]:
        """
        检查聪明钱包是否有新交易

        Returns:
            新交易信号列表，每条包含:
            - wallet_name: 钱包别名
            - direction: UP/DOWN
            - confidence: 信心分数 0-1
            - size_usd: 交易金额
        """
        if not self._initialized:
            return []

        signals = []

        for w in self.wallets:
            try:
                raw = self._fetch_trades(w.address, limit=10)
                if not raw:
                    continue

                last_ts = self.last_seen_ts.get(w.address, 0)
                new_trades = []

                for t in raw:
                    ts = int(t.get("timestamp", 0) or t.get("createdAt", 0))
                    if ts > last_ts:
                        slug = t.get("market", "") or t.get("slug", "")
                        market_type = self._classify_market(slug)
                        if market_type == "BTC":
                            new_trades.append(t)

                if not new_trades:
                    continue

                # 更新最新时间戳
                max_ts = max(int(t.get("timestamp", 0) or t.get("createdAt", 0)) for t in new_trades)
                self.last_seen_ts[w.address] = max_ts

                stats = self.stats.get(w.address)
                if not stats:
                    continue

                # 检查是否达到跟单门槛
                if stats.win_rate < w.min_win_rate:
                    logger.info(
                        f"  ⏭️ {w.name} 胜率不足 ({stats.win_rate:.1%} < {w.min_win_rate:.1%})，跳过"
                    )
                    continue
                if stats.total_trades < w.min_trades:
                    logger.info(
                        f"  ⏭️ {w.name} 交易笔数不足 ({stats.total_trades} < {w.min_trades})，跳过"
                    )
                    continue

                for t in new_trades:
                    direction = self._infer_direction(t)
                    if direction == "UNKNOWN":
                        continue

                    size_usd = float(t.get("price", 0)) * float(t.get("size", 0))
                    side = t.get("side", "").upper()

                    # 只跟 BUY（他们开仓的方向）
                    if side != "BUY":
                        continue

                    # 计算信心分数
                    confidence = self._calc_confidence(w, stats)

                    signal = {
                        "wallet_name": w.name,
                        "wallet_address": w.address,
                        "direction": direction,
                        "confidence": confidence,
                        "size_usd": size_usd,
                        "weight": w.weight,
                        "win_rate": stats.win_rate,
                        "total_trades": stats.total_trades,
                        "timestamp": int(t.get("timestamp", 0) or t.get("createdAt", 0)),
                    }
                    signals.append(signal)
                    logger.info(
                        f"  🔔 {w.name} 新交易: {direction} ${size_usd:.2f} | "
                        f"信心={confidence:.2f} 胜率={stats.win_rate:.1%}"
                    )

            except Exception as e:
                logger.error(f"检查 {w.name} 失败: {e}")

        return signals

    def _calc_confidence(self, wallet: SmartWallet, stats: WalletStats) -> float:
        """
        计算跟单信心分数 (0-1)

        综合考虑:
        - 胜率 (权重 40%)
        - BTC 市场专注度 (权重 30%)
        - 交易笔数/经验 (权重 20%)
        - 最近趋势 (权重 10%)
        """
        # 胜率得分
        win_score = min(stats.win_rate / 0.9, 1.0)  # 90% 胜率满分

        # BTC 专注度得分
        btc_score = min(stats.btc_market_ratio / 0.5, 1.0)  # 50% 以上 BTC 满分

        # 经验得分（交易笔数）
        exp_score = min(stats.total_trades / 100, 1.0)  # 100 笔满分

        # 最近趋势（最近10笔胜率）
        recent_score = stats.win_rate  # 简化

        confidence = (
            win_score * 0.40
            + btc_score * 0.30
            + exp_score * 0.20
            + recent_score * 0.10
        )

        return min(confidence, 1.0)

    def get_combined_signal(
        self, our_direction: Optional[str], our_edge: float
    ) -> dict:
        """
        将聪明钱包信号与自有信号叠加

        Args:
            our_direction: 我们信号方向 (UP/DOWN/None)
            our_edge: 我们的 edge 值

        Returns:
            {
                "direction": 最终方向,
                "confidence": 综合信心,
                "source": "signal" | "copy" | "combined",
                "boost": edge 放大系数,
            }
        """
        # 获取最新信号（同步版本，由 scheduler 调用）
        # 这里返回缓存的结果
        return {
            "direction": our_direction,
            "confidence": 0.0,
            "source": "signal",
            "boost": 1.0,
            "wallet_signals": [],
        }

    def get_stats_summary(self) -> str:
        """返回所有钱包的统计摘要"""
        lines = []
        for w in self.wallets:
            s = self.stats.get(w.address)
            if s:
                lines.append(
                    f"{w.name}: {s.total_trades}笔 | "
                    f"胜率{s.win_rate:.1%} | "
                    f"BTC占比{s.btc_market_ratio:.1%} | "
                    f"PnL ${s.total_pnl:.2f}"
                )
            else:
                lines.append(f"{w.name}: 无数据")
        return "\n".join(lines)


# === 全局实例 ===
_tracker: Optional[SmartWalletTracker] = None


def get_tracker() -> Optional[SmartWalletTracker]:
    """获取全局跟踪器实例"""
    return _tracker


def create_tracker(wallets: list[dict]) -> SmartWalletTracker:
    """创建跟踪器实例

    Args:
        wallets: [{"address": "0x...", "name": "nebuladrive", "weight": 1.0}, ...]
    """
    global _tracker
    wallet_objs = []
    for w in wallets:
        wallet_objs.append(SmartWallet(
            address=w["address"],
            name=w.get("name", w["address"][:8]),
            min_win_rate=w.get("min_win_rate", 0.6),
            min_trades=w.get("min_trades", 20),
            weight=w.get("weight", 1.0),
        ))
    _tracker = SmartWalletTracker(wallet_objs)
    return _tracker
