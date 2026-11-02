"""风控引擎：AI 只是建议，代码强制执行规则（防重锁/杠杆/仓位/止盈止损/熔断）。"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import db
from .config import (HARD_MAX_CONCURRENT, HARD_MAX_LEVERAGE, HARD_MAX_POSITION_PCT,
                     HARD_MAX_SL_PCT, HARD_MIN_SL_PCT)

log = logging.getLogger("okk.risk")


class RiskVerdict(dict):
    approved: bool
    reason: str
    order: dict


def _v(approved: bool, reason: str, order: dict | None = None) -> RiskVerdict:
    return RiskVerdict(approved=approved, reason=reason, order=order or {})


def is_paused() -> tuple[bool, int]:
    """返回 (是否暂停, paused_until)。"""
    st = db.get_state()
    pu = int(st.get("paused_until") or 0)
    now = int(time.time())
    if pu == -1:
        return True, -1
    if pu > now:
        return True, pu
    return False, pu


def circuit_breaker(equity: float) -> tuple[bool, str]:
    """当日亏损熔断 + 连亏冷却。"""
    day_start = _day_start_ts()
    start_eq = db.day_start_equity(day_start)
    limit_pct = db.get_float("daily_loss_limit_pct", 10)
    if start_eq and start_eq > 0 and equity > 0:
        dd = (start_eq - equity) / start_eq * 100
        if dd >= limit_pct:
            return True, f"当日回撤 {dd:.1f}% 触发熔断（上限 {limit_pct}%），请人工复核"

    # 连亏冷却
    trades = db.get_trades(limit=30)
    closed = [t for t in trades if t["action"] == "close" and t["status"] != "failed"]
    n = db.get_int("consecutive_loss_cooldown", 3)
    cd = db.get_int("cooldown_minutes", 120)
    if n > 0 and len(closed) >= n:
        streak = 0
        last_ts = 0
        for t in closed:
            if float(t.get("pnl") or 0) < 0:
                streak += 1
                last_ts = max(last_ts, int(t["ts"]))
            else:
                break
        if streak >= n and time.time() - last_ts < cd * 60:
            return True, f"连续亏损 {streak} 单，冷却至 {(last_ts + cd*60 - int(time.time()))//60} 分钟后"
    return False, ""


def _day_start_ts() -> int:
    lt = time.localtime()
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return int(midnight)


def check_open(symbol: str, action: str, equity: float, positions: list[dict],
               confidence: float, dry_run: bool) -> RiskVerdict:
    """开仓前的全部强制检查。返回放行后的下单参数（含修正）。"""
    # 1) 置信度
    min_conf = db.get_float("min_confidence", 0.6)
    if confidence < min_conf:
        return _v(False, f"置信度 {confidence:.2f} < 阈值 {min_conf:.2f}")

    # 2) 防重锁（同币同方向 60 分钟内不重复开仓）
    dedupe_min = db.get_int("dedupe_minutes", 60)
    if not db.acquire_dedupe(symbol, action, dedupe_min):
        return _v(False, f"防重锁：{symbol} {action} 在 {dedupe_min} 分钟内已执行过")

    # 3) 最大同时持仓数
    max_conc = min(db.get_int("max_concurrent", 3), HARD_MAX_CONCURRENT)
    if len(positions) >= max_conc:
        db.release_dedupe(symbol, action)
        return _v(False, f"已达最大持仓数 {max_conc}")

    # 4) 同币已有仓位则不加仓（单币单仓，简化风控）
    for p in positions:
        if p["symbol"] == symbol:
            db.release_dedupe(symbol, action)
            return _v(False, f"{symbol} 已有持仓，禁止加仓")

    # 5) 熔断
    halted, why = circuit_breaker(equity)
    if halted:
        db.release_dedupe(symbol, action)
        return _v(False, why)

    # 6) 杠杆：强制 ≤ 3
    lev = db.get_float("max_leverage", 3)
    lev = max(1.0, min(lev, HARD_MAX_LEVERAGE))

    # 7) 单笔仓位：强制 ≤ 20% 总资产
    pct = db.get_float("max_position_pct", 20)
    pct = max(1.0, min(pct, HARD_MAX_POSITION_PCT))
    notional = equity * pct / 100.0

    # 8) 止损止盈：强制附带，幅度钳制在合理区间
    sl_pct = db.get_float("stop_loss_pct", 2.0)
    tp_pct = db.get_float("take_profit_pct", 4.0)
    sl_pct = max(HARD_MIN_SL_PCT, min(sl_pct, HARD_MAX_SL_PCT))
    tp_pct = max(sl_pct * 0.5, min(tp_pct, HARD_MAX_SL_PCT * 2))

    return _v(True, "通过", {
        "symbol": symbol,
        "action": action,
        "leverage": round(lev, 1),
        "notional_pct": pct,
        "notional": round(notional, 2),
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
    })


def compute_sl_tp(action: str, price: float, sl_pct: float, tp_pct: float) -> tuple[float, float]:
    """根据方向计算绝对止损/止盈价。"""
    if price <= 0:
        return 0.0, 0.0
    if action == "open_long":
        return round(price * (1 - sl_pct / 100), 8), round(price * (1 + tp_pct / 100), 8)
    return round(price * (1 + sl_pct / 100), 8), round(price * (1 - tp_pct / 100), 8)


def check_close(symbol: str, positions: list[dict]) -> tuple[bool, str, dict]:
    """平仓检查（比开仓宽松，但同样要有持仓才允许）。"""
    for p in positions:
        if p["symbol"] == symbol:
            return True, "通过", p
    return False, f"{symbol} 无持仓可平", {}
