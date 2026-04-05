"""
三层信号引擎

Layer 1: 多时间框架趋势方向 (4h/12h/24h)
Layer 2: 动量确认 (当前 15 分钟内走势)
Layer 3: 定价偏差 (Polymarket 价格 vs 估计概率)
安全阀: RSI / 跌速衰减 / 关键价位

输出: Signal (direction, confidence, strength)
"""

from dataclasses import dataclass
from enum import Enum

from src.price.price_manager import TechnicalIndicators
from src.config.settings import (
    TREND_THRESHOLD,
    MOMENTUM_THRESHOLD,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    SPEED_DECAY_RATIO,
)
from src.utils.logger import setup_logger

logger = setup_logger("signal_engine")


class SignalStrength(Enum):
    NONE = "none"
    WEAK = "weak"        # 两层同意
    STRONG = "strong"    # 三层同意


@dataclass
class Signal:
    """交易信号"""
    direction: str         # "up" / "down" / None
    strength: SignalStrength
    confidence: float      # 0.0 - 1.0
    edge: float            # 估计 Edge

    # 各层详情
    layer1_trend: str = ""       # "up" / "down" / "neutral"
    layer1_detail: str = ""
    layer2_momentum: str = ""    # "up" / "down" / "neutral"
    layer2_detail: str = ""
    layer3_deviation: float = 0.0
    layer3_detail: str = ""

    # 安全阀
    safety_rsi_ok: bool = True
    safety_speed_ok: bool = True
    safety_keylevel_ok: bool = True
    safety_skip_reason: str = ""

    # 最终决策
    should_trade: bool = False


def generate_signal(
    indicators: TechnicalIndicators,
    up_price: float,
    down_price: float,
    btc_current: float,
    price_to_beat: float | None = None,
) -> Signal:
    """
    生成交易信号

    Args:
        indicators: 技术指标
        up_price: Polymarket UP 价格 (0-1)
        down_price: Polymarket DOWN 价格 (0-1)
        btc_current: BTC 当前价格
        price_to_beat: Price to Beat (None = 未知，不能用 Layer 2)

    Returns:
        Signal
    """
    signal = Signal(
        direction=None,
        strength=SignalStrength.NONE,
        confidence=0.0,
        edge=0.0,
    )

    # ========== Layer 1: 趋势方向 ==========
    signal.layer1_trend, signal.layer1_detail = _evaluate_trend(indicators)

    # ========== Layer 2: 动量确认 ==========
    if price_to_beat and price_to_beat > 0:
        signal.layer2_momentum, signal.layer2_detail = _evaluate_momentum(
            btc_current, price_to_beat, indicators
        )
    else:
        # 没有 price to beat，用 15 分钟 K 线内动量
        if indicators.momentum_15m > MOMENTUM_THRESHOLD:
            signal.layer2_momentum = "up"
            signal.layer2_detail = f"15m 内涨 {indicators.momentum_15m:+.2%}"
        elif indicators.momentum_15m < -MOMENTUM_THRESHOLD:
            signal.layer2_momentum = "down"
            signal.layer2_detail = f"15m 内跌 {indicators.momentum_15m:+.2%}"
        else:
            signal.layer2_momentum = "neutral"
            signal.layer2_detail = f"15m 内波动小 {indicators.momentum_15m:+.2%}"

    # ========== Layer 3: 定价偏差 ==========
    signal.layer3_deviation = _evaluate_pricing(up_price, down_price)
    if up_price < 0.48:
        signal.layer3_detail = f"UP 被低估 ({up_price:.2f})"
    elif down_price < 0.48:
        signal.layer3_detail = f"DOWN 被低估 ({down_price:.2f})"
    else:
        signal.layer3_detail = f"定价均衡 UP={up_price:.2f} DOWN={down_price:.2f}"

    # ========== 安全阀检查 ==========
    signal.safety_rsi_ok = _check_rsi(indicators)
    signal.safety_speed_ok = _check_speed(indicators)
    signal.safety_keylevel_ok = _check_key_levels(indicators)

    # ========== 信号合成 ==========
    layers = [signal.layer1_trend, signal.layer2_momentum]
    up_count = layers.count("up")
    down_count = layers.count("down")

    # 确定方向
    if up_count >= 2:
        direction = "up"
    elif down_count >= 2:
        direction = "down"
    elif up_count == 1 and down_count == 0:
        direction = "up"
        signal.strength = SignalStrength.WEAK
    elif down_count == 1 and up_count == 0:
        direction = "down"
        signal.strength = SignalStrength.WEAK
    else:
        # 冲突或都中性
        signal.direction = None
        signal.should_trade = False
        return signal

    signal.direction = direction

    # 计算一致度
    agreement = max(up_count, down_count)
    has_layer3 = (direction == "up" and up_price < 0.50) or (direction == "down" and down_price < 0.50)

    if agreement >= 2 and has_layer3:
        signal.strength = SignalStrength.STRONG
    elif agreement >= 2 or (agreement >= 1 and has_layer3):
        signal.strength = SignalStrength.WEAK
    else:
        signal.strength = SignalStrength.NONE

    # 置信度
    if signal.strength == SignalStrength.STRONG:
        signal.confidence = 0.60 + abs(signal.layer3_deviation) * 0.5
    elif signal.strength == SignalStrength.WEAK:
        signal.confidence = 0.53 + abs(signal.layer3_deviation) * 0.3
    else:
        signal.confidence = 0.50

    signal.confidence = min(0.75, signal.confidence)  # 上限 75%

    # Edge
    if direction == "up":
        signal.edge = signal.confidence - up_price
    else:
        signal.edge = signal.confidence - down_price

    # ========== 安全阀过滤 ==========
    if not signal.safety_rsi_ok:
        signal.safety_skip_reason = f"RSI={indicators.rsi:.0f} (超买/超卖)"
        signal.should_trade = False
        return signal

    if not signal.safety_speed_ok:
        signal.safety_skip_reason = "趋势速度衰减中"
        signal.should_trade = False
        return signal

    if not signal.safety_keylevel_ok:
        signal.safety_skip_reason = "接近关键价位"
        signal.should_trade = False
        return signal

    # Edge 过滤
    if abs(signal.edge) < 0.03:  # MIN_EDGE
        signal.safety_skip_reason = f"Edge 不足 ({signal.edge:+.1%})"
        signal.should_trade = False
        return signal

    signal.should_trade = True
    return signal


def _evaluate_trend(ind: TechnicalIndicators) -> tuple[str, str]:
    """Layer 1: 多时间框架趋势"""
    trends = []

    for i, (trend, threshold) in enumerate(zip(
        [ind.trend_4h, ind.trend_12h, ind.trend_24h],
        TREND_THRESHOLD,
    )):
        window = [240, 720, 1440][i]
        if abs(trend) >= threshold:
            direction = "up" if trend > 0 else "down"
            trends.append((direction, f"{window}m={trend:+.2%}"))
        else:
            trends.append(("neutral", f"{window}m={trend:+.2%}"))

    up_count = sum(1 for t, _ in trends if t == "up")
    down_count = sum(1 for t, _ in trends if t == "down")

    details = " | ".join(d for _, d in trends)

    if up_count >= 2:
        return "up", details
    elif down_count >= 2:
        return "down", details
    else:
        return "neutral", details


def _evaluate_momentum(btc_current: float, price_to_beat: float, ind: TechnicalIndicators) -> tuple[str, str]:
    """Layer 2: 当前 15 分钟内动量"""
    change = (btc_current - price_to_beat) / price_to_beat

    if change > MOMENTUM_THRESHOLD:
        return "up", f"vs PTB: {change:+.2%} (涨)"
    elif change < -MOMENTUM_THRESHOLD:
        return "down", f"vs PTB: {change:+.2%} (跌)"
    else:
        return "neutral", f"vs PTB: {change:+.2%} (横盘)"


def _evaluate_pricing(up_price: float, down_price: float) -> float:
    """Layer 3: 定价偏差"""
    # 偏离 0.50 的程度
    deviation = abs(up_price - 0.50)
    return deviation


def _check_rsi(ind: TechnicalIndicators) -> bool:
    """RSI 安全阀"""
    return RSI_OVERSOLD < ind.rsi < RSI_OVERBOUGHT


def _check_speed(ind: TechnicalIndicators) -> bool:
    """速度衰减安全阀"""
    # 如果有趋势但速度在衰减 → 不入场
    if ind.speed_recent == 0:
        return True

    # 计算衰减比例
    if abs(ind.speed_older) > 0:
        ratio = abs(ind.speed_recent) / abs(ind.speed_older)
        # 速度衰减到 30% 以下 → 趋势减弱
        if ratio < SPEED_DECAY_RATIO:
            return False

    return True


def _check_key_levels(ind: TechnicalIndicators) -> bool:
    """关键价位安全阀"""
    if ind.near_round_number:
        return False
    if ind.near_bollinger:
        return False
    return True
