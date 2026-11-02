"""OKK — 24H AI 自动炒币 Agent。FastAPI 入口。"""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, security
from .config import BASE_DIR, HOST, LOG_LEVEL, PORT, TRADING_MODE
from .engine import engine
from .exchanges import exchange
from .routes import router

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("okk")

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # 首次运行：生成管理密码
    if not db.get_setting("admin_password_hash"):
        from .config import ADMIN_PASSWORD
        pw = ADMIN_PASSWORD or secrets.token_urlsafe(10)
        db.set_settings({"admin_password_hash": security.hash_password(pw)})
        log.warning("=" * 60)
        log.warning("首次启动，Web 管理密码: %s", pw)
        log.warning("请登录后立即在 设置 页面修改此密码！")
        log.warning("=" * 60)
        db.add_log(f"初始化完成，管理密码已生成（模式：{TRADING_MODE}）", "warn")

    # 自动连接交易所
    try:
        ok = await exchange.connect_from_db()
        if ok:
            log.info("交易所已自动连接: %s", exchange.exchange_id)
            db.add_log(f"交易所 {exchange.exchange_id} 已自动连接", "info")
        else:
            db.add_log("尚未绑定交易所，请到 Web 设置里绑定 OKX/币安 API", "warn")
    except Exception as e:
        log.warning("交易所自动连接失败: %s", e)
        db.add_log(f"交易所自动连接失败: {str(e)[:150]}", "warn")

    task = asyncio.create_task(engine.run(), name="okk-engine")

    # 自动启动开关
    if db.get_setting("auto_start", "0") == "1" and exchange.ready:
        engine.start()

    log.info("OKK 已启动 → http://%s:%s  （模式: %s）", HOST, PORT, TRADING_MODE)
    yield
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    await exchange.close()
    log.info("OKK 已安全退出")


app = FastAPI(title="OKK · AI 炒币 Agent", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, workers=1)
