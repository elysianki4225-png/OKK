"""REST API 路由：认证 / 状态 / 引擎控制 / 设置 / 密钥 / 模型 / 行情 / 持仓 / 日志。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from . import ai_engine, db, exchanges, risk, security
from .engine import engine
from .exchanges import exchange

router = APIRouter(prefix="/api")

# ---------------- 认证 ----------------
def _check_token(authorization: str | None) -> bool:
    if not authorization:
        return False
    token = authorization.replace("Bearer ", "").strip()
    return security.verify_token(token)


def require_auth(authorization: str | None = Header(default=None)):
    if not _check_token(authorization):
        raise HTTPException(status_code=401, detail="未登录或令牌失效")
    return True


@router.post("/login")
async def login(payload: dict = Body(...)):
    password = str(payload.get("password") or "")
    stored = db.get_setting("admin_password_hash", "")
    if not stored:
        # 首次登录：用传入密码初始化
        if len(password) < 6:
            raise HTTPException(400, "首次登录请设置至少 6 位的管理密码")
        db.set_settings({"admin_password_hash": security.hash_password(password)})
        db.add_log("管理密码已初始化", "warn")
    else:
        if not security.verify_password(password, stored):
            db.add_log("登录失败：密码错误", "warn")
            raise HTTPException(401, "密码错误")
    token = security.issue_token()
    db.add_log("管理员登录成功", "info")
    return {"ok": True, "token": token, "first_run": not stored}


@router.post("/logout")
async def logout():
    return {"ok": True}


@router.get("/me")
async def me(authorization: str | None = Header(default=None)):
    return {"auth": _check_token(authorization)}


# ---------------- 总览状态 ----------------
def _day_start_ts() -> int:
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


@router.get("/status")
async def status(_: bool = Depends(require_auth)):
    st = db.get_state()
    paused, until = risk.is_paused()
    equity, unreal, pos_count, positions = 0.0, 0.0, 0, []
    err = ""
    if exchange.ready:
        try:
            bal = await exchange.balance()
            positions = await exchange.positions()
            unreal = sum(p["unrealizedPnl"] for p in positions)
            equity = bal["total"] + unreal
        except Exception as e:
            err = str(e)[:200]
    day_start = _day_start_ts()
    start_eq = db.day_start_equity(day_start)
    today_pnl = (equity - start_eq) if (start_eq and equity) else db.today_realized_pnl(day_start)
    today_pct = (today_pnl / start_eq * 100) if (start_eq and start_eq > 0) else 0.0

    models = db.get_llm_models(only_enabled=True)
    models.sort(key=lambda m: (m.get("priority", 100), m.get("latency_ms") or 0))
    return {
        "running": bool(st.get("running")),
        "paused": paused,
        "paused_until": until,
        "status": st.get("status", "stopped"),
        "cycles": st.get("cycles", 0),
        "last_cycle": st.get("last_cycle", 0),
        "last_error": st.get("last_error", ""),
        "trading_mode": db.get_setting("trading_mode", "paper"),
        "exchange_connected": exchange.ready,
        "exchange": exchange.exchange_id if exchange.ready else "",
        "equity": round(equity, 2),
        "unrealized": round(unreal, 2),
        "today_pnl": round(today_pnl, 2),
        "today_pct": round(today_pct, 2),
        "positions_count": len(positions),
        "active_model": st.get("active_model", "") or (models[0]["label"] if models else ""),
        "models": [
            {"label": m["label"], "model": m["model"], "latency_ms": m["latency_ms"],
             "ok_rate": round(float(m.get("ok_rate") or 0), 2)}
            for m in models[:6]
        ],
        "error": err,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/equity-curve")
async def equity_curve(hours: int = 48, _: bool = Depends(require_auth)):
    return {"points": db.equity_series(min(max(hours, 1), 24 * 90))}


# ---------------- 引擎控制 ----------------
@router.post("/engine/start")
async def engine_start(_: bool = Depends(require_auth)):
    if not exchange.ready:
        await exchange.connect_from_db()
    r = engine.start()
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "启动失败"))
    return r


@router.post("/engine/stop")
async def engine_stop(_: bool = Depends(require_auth)):
    return engine.stop()


@router.post("/engine/pause")
async def engine_pause(payload: dict = Body(...), _: bool = Depends(require_auth)):
    seconds = int(payload.get("seconds", 3600))  # -1 = 无限期
    return engine.pause(seconds)


@router.post("/engine/resume")
async def engine_resume(_: bool = Depends(require_auth)):
    return engine.resume()


@router.post("/engine/emergency-close")
async def engine_emergency(_: bool = Depends(require_auth)):
    dry = db.get_setting("trading_mode", "paper") != "live"
    return await trader_close_all(dry)


async def trader_close_all(dry: bool):
    from . import trader
    return await trader.emergency_close_all(dry)


# ---------------- 设置 ----------------
PUBLIC_SETTINGS = [
    "trading_mode", "scan_interval", "scan_top_n", "kline_limit", "timeframe", "htf",
    "max_leverage", "max_position_pct", "max_concurrent", "stop_loss_pct", "take_profit_pct",
    "min_confidence", "dedupe_minutes", "daily_loss_limit_pct", "consecutive_loss_cooldown",
    "cooldown_minutes", "auto_start", "notify_webhook", "llm_timeout", "risk_auto_sl_tp",
]


@router.get("/settings")
async def get_settings(_: bool = Depends(require_auth)):
    s = db.get_settings()
    return {k: s.get(k, "") for k in PUBLIC_SETTINGS}


@router.put("/settings")
async def put_settings(payload: dict = Body(...), _: bool = Depends(require_auth)):
    clean = {}
    for k in PUBLIC_SETTINGS:
        if k in payload:
            v = payload[k]
            if k in ("max_leverage",):
                v = str(max(1.0, min(float(v), risk_module_max_leverage())))
            elif k in ("max_position_pct",):
                v = str(max(1.0, min(float(v), 20.0)))
            elif k in ("max_concurrent",):
                v = str(max(1, min(int(float(v)), 5)))
            clean[k] = v
    if "admin_password" in payload and payload["admin_password"]:
        pw = str(payload["admin_password"])
        if len(pw) < 6:
            raise HTTPException(400, "密码至少 6 位")
        clean["admin_password_hash"] = security.hash_password(pw)
    db.set_settings(clean)
    db.add_log(f"设置已更新：{', '.join(k for k in clean if k != 'admin_password_hash')}", "info")
    return {"ok": True}


def risk_module_max_leverage():
    from .config import HARD_MAX_LEVERAGE
    return HARD_MAX_LEVERAGE


# ---------------- 交易所密钥 ----------------
@router.get("/exchange")
async def get_exchange_config(_: bool = Depends(require_auth)):
    row = db.get_enabled_exchange()
    if not row:
        return {"configured": False}
    return {
        "configured": True,
        "exchange": row["exchange"],
        "testnet": bool(row.get("testnet")),
        "api_key_masked": security.mask_secret(security.decrypt(row["api_key"])),
        "updated_at": row.get("updated_at", 0),
    }


@router.post("/exchange")
async def set_exchange_config(payload: dict = Body(...), _: bool = Depends(require_auth)):
    name = str(payload.get("exchange", "")).lower()
    if name not in ("okx", "binance", "binance-usdm"):
        raise HTTPException(400, "仅支持 okx / binance")
    api_key = str(payload.get("api_key", "")).strip()
    api_secret = str(payload.get("api_secret", "")).strip()
    passphrase = str(payload.get("passphrase", "")).strip()
    if not api_key or not api_secret:
        raise HTTPException(400, "API Key / Secret 不能为空")
    if "okx" in name and not passphrase:
        raise HTTPException(400, "OKX 需要 Passphrase（创建 API 时设置的口令）")

    # 先测试连通
    test = await exchanges.test_connection(name, api_key, api_secret, passphrase, bool(payload.get("testnet")))
    if not test.get("ok"):
        raise HTTPException(400, f"连接失败：{test.get('error', '未知错误')}")

    db.save_exchange_keys(name, {
        "api_key": security.encrypt(api_key),
        "api_secret": security.encrypt(api_secret),
        "passphrase": security.encrypt(passphrase) if passphrase else "",
        "testnet": int(bool(payload.get("testnet"))),
    })
    await exchange.connect_from_db()
    db.add_log(f"交易所 {name} 已绑定（余额 {test['balance']['total']} USDT，持仓 {test['positions']} 个）", "warn")
    return {"ok": True, "balance": test["balance"], "positions": test["positions"]}


@router.delete("/exchange")
async def del_exchange_config(_: bool = Depends(require_auth)):
    await exchange.close()
    for ex in ("okx", "binance", "binance-usdm"):
        db.clear_exchange_keys(ex)
    db.add_log("交易所密钥已清除", "warn")
    return {"ok": True}


# ---------------- LLM 模型 ----------------
@router.get("/llm/models")
async def llm_models(_: bool = Depends(require_auth)):
    rows = db.get_llm_models()
    for r in rows:
        r["api_key_masked"] = security.mask_secret(security.decrypt(r.get("api_key") or ""))
        r.pop("api_key", None)
    return {"models": rows}


@router.post("/llm/models")
async def llm_model_upsert(payload: dict = Body(...), _: bool = Depends(require_auth)):
    model = str(payload.get("model", "")).strip()
    if not model:
        raise HTTPException(400, "模型串必填，例如 openai/gpt-4o-mini 或 deepseek/deepseek-chat")
    row = {
        "id": payload.get("id"),
        "label": str(payload.get("label") or model),
        "model": model,
        "api_key": security.encrypt(str(payload.get("api_key", ""))) if payload.get("api_key") else "",
        "base_url": str(payload.get("base_url", "") or ""),
        "enabled": int(bool(payload.get("enabled", 1))),
        "priority": int(payload.get("priority", 100)),
    }
    # 保留旧密钥（编辑时留空表示不改）
    if row["id"] and not payload.get("api_key"):
        old = next((m for m in db.get_llm_models() if m["id"] == row["id"]), None)
        if old:
            row["api_key"] = old.get("api_key") or ""
    mid = db.upsert_llm_model(row)
    db.add_log(f"AI 模型已保存：{row['label']} ({model})", "info")
    return {"ok": True, "id": mid}


@router.delete("/llm/models/{model_id}")
async def llm_model_delete(model_id: int, _: bool = Depends(require_auth)):
    db.delete_llm_model(model_id)
    return {"ok": True}


@router.post("/llm/speed-test")
async def llm_speed_test(_: bool = Depends(require_auth)):
    res = await ai_engine.speed_test()
    return {"results": res}


@router.post("/llm/test")
async def llm_test(payload: dict = Body(...), _: bool = Depends(require_auth)):
    """手动测试某个模型的对话能力。"""
    row = {
        "model": payload.get("model", ""),
        "api_key": security.encrypt(str(payload.get("api_key", ""))) if payload.get("api_key") else "",
        "base_url": payload.get("base_url", ""),
    }
    if not payload.get("api_key"):
        old = next((m for m in db.get_llm_models() if m["model"] == row["model"]), None)
        if old:
            row["api_key"] = old.get("api_key") or ""
            row["base_url"] = row["base_url"] or old.get("base_url") or ""
    t0 = time.time()
    try:
        content = await ai_engine._call_llm(row, [{"role": "user", "content": "回复：OK"}], max_tokens=8, timeout=20)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "reply": content[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ---------------- 行情 / 持仓 / 流水 ----------------
@router.get("/markets")
async def markets(_: bool = Depends(require_auth)):
    if not exchange.ready:
        raise HTTPException(400, "交易所未连接")
    try:
        top_n = max(5, min(db.get_int("scan_top_n", 25), 60))
        syms = await exchange.top_symbols(top_n)
        tickers = await exchange.client.fetch_tickers()
    except Exception as e:
        raise HTTPException(500, f"获取行情失败: {str(e)[:200]}")
    out = []
    for s in syms:
        t = tickers.get(s) or {}
        out.append({
            "symbol": s,
            "price": t.get("last") or t.get("close") or 0,
            "change_pct": t.get("percentage") or 0,
            "quote_volume": t.get("quoteVolume") or 0,
        })
    return {"markets": out, "scan_top_n": top_n}


@router.get("/positions")
async def positions(_: bool = Depends(require_auth)):
    if not exchange.ready:
        return {"positions": []}
    try:
        pos = await exchange.positions()
        return {"positions": pos}
    except Exception as e:
        raise HTTPException(500, f"获取持仓失败: {str(e)[:200]}")


@router.post("/positions/close")
async def close_position(payload: dict = Body(...), _: bool = Depends(require_auth)):
    from . import trader
    symbol = payload.get("symbol", "")
    dry = db.get_setting("trading_mode", "paper") != "live"
    pos = await exchange.positions()
    for p in pos:
        if p["symbol"] == symbol:
            return await trader.execute_close(p, dry, reason="手动平仓")
    raise HTTPException(404, "未找到该持仓")


@router.get("/trades")
async def trades(limit: int = 100, _: bool = Depends(require_auth)):
    return {"trades": db.get_trades(limit)}


@router.get("/decisions")
async def decisions(limit: int = 100, _: bool = Depends(require_auth)):
    return {"decisions": db.get_decisions(limit)}


@router.get("/logs")
async def logs(limit: int = 200, _: bool = Depends(require_auth)):
    return {"logs": db.get_logs(limit)}


@router.get("/health")
async def health():
    """免认证健康检查（监控/探活用）。"""
    return {"ok": True, "ts": int(time.time()), "engine": db.get_state().get("status", "stopped")}
