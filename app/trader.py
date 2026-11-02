"""交易执行：把风控放行的指令落到交易所（或模拟盘），并写入流水。"""
from __future__ import annotations

import asyncio
import logging
import time

from . import db, exchanges, risk
from .exchanges import exchange

log = logging.getLogger("okk.trader")


async def execute_open(verdict_order: dict, price: float, dry_run: bool) -> dict:
    """执行开仓：换算数量、设杠杆、附带止盈止损。"""
    action = verdict_order["action"]          # open_long / open_short
    symbol = verdict_order["symbol"]
    side = "buy" if action == "open_long" else "sell"
    leverage = verdict_order["leverage"]
    notional = verdict_order["notional"]
    # 价格兜底：指标缺失时取最新成交价，确保止盈止损必带
    if not price or price <= 0:
        try:
            t = await exchange.ticker(symbol)
            price = float(t.get("last") or t.get("close") or 0)
        except Exception:
            price = 0
    sl, tp = risk.compute_sl_tp(action, price, verdict_order["sl_pct"], verdict_order["tp_pct"])
    if sl <= 0 or tp <= 0:
        db.add_log(f"{symbol} 无法计算止损止盈价（price={price}），拒绝开仓", "error")
        db.release_dedupe(symbol, action)
        return {"ok": False, "error": "价格无效，无法附带止盈止损"}

    try:
        res = await exchange.open_position(
            symbol, side, notional, leverage, sl_price=sl, tp_price=tp, dry_run=dry_run
        )
        db.add_trade({
            "symbol": symbol, "action": action, "side": side,
            "qty": res.get("amount", 0), "price": res.get("price", price),
            "leverage": leverage, "notional": notional, "sl": sl, "tp": tp,
            "order_id": res.get("id", ""), "status": "dry_run" if dry_run else res.get("status", "submitted"),
            "detail": f"AI 开仓 conf 通过；SL={sl} TP={tp}",
        })
        db.add_log(
            f"{'[模拟] ' if dry_run else ''}开仓 {action} {symbol} 数量={res.get('amount')} 价格≈{res.get('price')} "
            f"杠杆={leverage}x 止损={sl} 止盈={tp}", "trade"
        )
        return {"ok": True, "result": res}
    except Exception as e:
        db.release_dedupe(symbol, action)  # 下单失败释放防重锁，允许下轮重试
        db.add_trade({
            "symbol": symbol, "action": action, "side": side, "qty": 0, "price": price,
            "leverage": leverage, "notional": notional, "sl": sl, "tp": tp,
            "status": "failed", "detail": str(e)[:500],
        })
        db.add_log(f"开仓失败 {symbol} {action}: {str(e)[:200]}", "error")
        return {"ok": False, "error": str(e)[:500]}


async def execute_close(position: dict, dry_run: bool, reason: str = "") -> dict:
    symbol = position["symbol"]
    try:
        res = await exchange.close_position(position, dry_run=dry_run)
        pnl = float(position.get("unrealizedPnl") or 0)
        db.add_trade({
            "symbol": symbol, "action": "close",
            "side": "sell" if position.get("side") == "long" else "buy",
            "qty": position.get("contracts", 0), "price": position.get("markPrice", 0),
            "leverage": position.get("leverage", 1), "notional": position.get("notional", 0),
            "order_id": res.get("id", ""), "pnl": pnl,
            "status": "dry_run" if dry_run else res.get("status", "filled"),
            "detail": reason[:200],
        })
        db.add_log(f"{'[模拟] ' if dry_run else ''}平仓 {symbol} 仓位方向={position.get('side')} 估算pnl={pnl:.2f} {reason}", "trade")
        return {"ok": True, "result": res}
    except Exception as e:
        db.add_log(f"平仓失败 {symbol}: {str(e)[:200]}", "error")
        return {"ok": False, "error": str(e)[:500]}


async def emergency_close_all(dry_run: bool) -> dict:
    """一键全平 + 撤单（急停按钮）。"""
    closed, errors = 0, []
    try:
        positions = await exchange.positions()
    except Exception as e:
        return {"ok": False, "error": f"获取持仓失败: {e}"}
    for p in positions:
        r = await execute_close(p, dry_run, reason="手动急停")
        if r.get("ok"):
            closed += 1
        else:
            errors.append(r.get("error", ""))
    try:
        await exchange.cancel_all()
    except Exception:
        pass
    db.add_log(f"紧急全平完成：平仓 {closed} 个", "warn")
    return {"ok": True, "closed": closed, "errors": errors}
