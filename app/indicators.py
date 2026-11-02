"""纯 Python 技术指标：RSI / MACD / BOLL / ATR / EMA / 成交量。
程序端先算好指标，打包成简短 JSON 发给 AI —— 大幅省 Token。
"""
from __future__ import annotations

from typing import Any

# ohlcv: [[ts, open, high, low, close, volume], ...] 按时间升序


def _closes(ohlcv: list) -> list[float]:
    return [float(k[4]) for k in ohlcv]


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = gains / losses
    return round(100 - 100 / (1 + rs), 2)


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(values) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    ef, es = ema(values, fast), ema(values, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig = ema(line, signal)
    return {
        "macd": round(line[-1], 6),
        "signal": round(sig[-1], 6),
        "hist": round(line[-1] - sig[-1], 6),
    }


def bollinger(values: list[float], period: int = 20, mult: float = 2.0) -> dict:
    if len(values) < period:
        v = values[-1] if values else 0.0
        return {"mid": v, "upper": v, "lower": v, "width": 0.0, "pos": 0.5}
    window = values[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    std = var ** 0.5
    upper, lower = mid + mult * std, mid - mult * std
    width = (upper - lower) / mid if mid else 0.0
    last = values[-1]
    pos = (last - lower) / (upper - lower) if upper != lower else 0.5
    return {
        "mid": round(mid, 6), "upper": round(upper, 6), "lower": round(lower, 6),
        "width": round(width, 4), "pos": round(max(0.0, min(1.0, pos)), 3),
    }


def atr(ohlcv: list, period: int = 14) -> float:
    if len(ohlcv) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        h, l, pc = float(ohlcv[i][2]), float(ohlcv[i][3]), float(ohlcv[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs) / period, 6)


def analyze(ohlcv: list) -> dict[str, Any]:
    """把一根币种的 K 线压缩成 AI 需要的全部指标（省 Token 核心）。"""
    if not ohlcv or len(ohlcv) < 30:
        return {}
    closes = _closes(ohlcv)
    vols = [float(k[5]) for k in ohlcv]
    last = ohlcv[-1]
    price = closes[-1]
    e7, e25, e99 = ema(closes, 7), ema(closes, 25), ema(closes, 99)
    m = macd(closes)
    b = bollinger(closes)
    a = atr(ohlcv)
    vol_ma = sum(vols[-20:]) / 20 if len(vols) >= 20 else (sum(vols) / len(vols) or 1)
    chg1 = (price / closes[-2] - 1) * 100 if len(closes) >= 2 else 0.0
    chg4 = (price / closes[-5] - 1) * 100 if len(closes) >= 5 else 0.0
    chg24 = (price / closes[-25] - 1) * 100 if len(closes) >= 25 else 0.0

    # 简单信号打分（辅助 AI，也用于本地预筛）
    score = 0.0
    if e7[-1] > e25[-1]:
        score += 1
    else:
        score -= 1
    if m["hist"] > 0:
        score += 1
    else:
        score -= 1
    if rsi(closes) < 30:
        score += 1
    elif rsi(closes) > 70:
        score -= 1
    if b["pos"] < 0.2:
        score += 0.5
    elif b["pos"] > 0.8:
        score -= 0.5

    return {
        "p": round(price, 8),
        "chg1": round(chg1, 2), "chg4": round(chg4, 2), "chg24": round(chg24, 2),
        "rsi": rsi(closes),
        "macd": m,
        "boll": b,
        "atr": round(a, 8),
        "atrPct": round(a / price * 100, 2) if price else 0,
        "ema7": round(e7[-1], 8), "ema25": round(e25[-1], 8), "ema99": round(e99[-1], 8),
        "volx": round(vols[-1] / vol_ma, 2) if vol_ma else 1.0,
        "score": round(score, 1),
    }
