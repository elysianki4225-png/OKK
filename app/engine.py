"""24H AI Agent 主循环：扫描 → 指标 → AI 决策 → 风控 → 执行 → 记录。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from . import ai_engine, db, exchanges, indicators, risk, trader
from .exchanges import exchange

log = logging.getLogger("okk.engine")


class TradingEngine:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._cycle = 0
        self._last_speed_test = 0

    # ---------------- 状态控制 ----------------
    @property
    def running(self) -> bool:
        return bool(db.get_state().get("running"))

    def start(self) -> dict:
        st = db.get_state()
        if st.get("running"):
            return {"ok": False, "error": "引擎已在运行"}
        if not exchange.ready:
            return {"ok": False, "error": "交易所未连接，请先在设置里绑定 API Key"}
        db.set_state(running=1, status="running", paused_until=0, last_error="")
        db.add_log("引擎已启动，进入 24H 自动交易循环", "warn")
        return {"ok": True}

    def stop(self) -> dict:
        db.set_state(running=0, status="stopped", paused_until=0)
        db.add_log("引擎已停止", "warn")
        return {"ok": True}

    def pause(self, seconds: int) -> dict:
        """Windows 更新式暂停：seconds<=0 表示无限期。"""
        pu = -1 if seconds <= 0 else int(time.time()) + seconds
        db.set_state(paused_until=pu, status="paused")
        human = "无限期" if pu == -1 else datetime.fromtimestamp(pu).strftime("%H:%M:%S")
        db.add_log(f"引擎暂停至 {human}", "warn")
        return {"ok": True, "paused_until": pu}

    def resume(self) -> dict:
        db.set_state(paused_until=0, status="running" if self.running else "stopped")
        db.add_log("引擎已恢复运行", "warn")
        return {"ok": True}

    # ---------------- 主循环 ----------------
    async def run(self):
        db.add_log("交易引擎进程就绪（默认待机，需手动启动）", "info")
        while True:
            try:
                await self._loop_once()
            except asyncio.CancelledError:
                db.add_log("引擎任务被取消", "warn")
                raise
            except Exception as e:
                log.exception("engine loop error")
                db.set_state(last_error=str(e)[:500])
                db.add_log(f"引擎异常（10 秒后重试）：{str(e)[:200]}", "error")
                await asyncio.sleep(10)

    async def _loop_once(self):
        interval = max(60, db.get_int("scan_interval", 300))
        st = db.get_state()
        running = bool(st.get("running"))
        paused, until = risk.is_paused()
        if until > 0 and until <= time.time() and not running:
            db.set_state(paused_until=0)

        if not running or paused:
            await asyncio.sleep(5)
            return

        # 周期性模型测速（每 30 分钟）
        if self._cycle == 0 or time.time() - self._last_speed_test > 1800:
            self._last_speed_test = time.time()
            try:
                res = await ai_engine.speed_test()
                best = res[0] if res else None
                if best and best["ok"]:
                    db.set_state(active_model=best["label"] or best["model"])
            except Exception as e:
                db.add_log(f"模型测速失败: {str(e)[:150]}", "warn")

        self._cycle += 1
        t0 = time.time()
        db.set_state(status="scanning")
        try:
            await self.cycle()
        except exchanges.ExchangeError as e:
            db.set_state(last_error=str(e)[:300])
            db.add_log(f"交易周期失败：{str(e)[:200]}", "error")
        except Exception as e:
            log.exception("cycle error")
            db.set_state(last_error=str(e)[:300])
            db.add_log(f"周期异常：{str(e)[:200]}", "error")
        finally:
            db.set_state(status="running", last_cycle=int(time.time()), cycles=self._cycle)

        elapsed = time.time() - t0
        await asyncio.sleep(max(15, interval - elapsed))

    # ---------------- 单个周期 ----------------
    async def cycle(self):
        dry_run = db.get_setting("trading_mode", "paper") != "live"
        equity_info = await exchange.balance()
        positions = await exchange.positions()
        equity = equity_info["total"] + sum(p["unrealizedPnl"] for p in positions)
        db.add_equity(equity, sum(p["unrealizedPnl"] for p in positions))

        # 熔断检查（平仓不受熔断限制，开仓会二次检查）
        halted, why = risk.circuit_breaker(equity)

        # ---- 选币：成交额 TopN + 已持仓 ----
        top_n = max(5, min(db.get_int("scan_top_n", 25), 60))
        symbols = await exchange.top_symbols(top_n)
        for p in positions:
            if p["symbol"] not in symbols:
                symbols.append(p["symbol"])

        # ---- 拉 K 线 + 算指标（并发限制，1核1G 友好） ----
        tf = db.get_setting("timeframe", "15m")
        htf = db.get_setting("htf", "1h")
        limit = max(60, min(db.get_int("kline_limit", 120), 300))
        sem = asyncio.Semaphore(4)
        indic: dict[str, dict] = {}

        async def fetch_one(sym: str):
            async with sem:
                try:
                    ltf = await exchange.ohlcv(sym, tf, limit)
                    higher = await exchange.ohlcv(sym, htf, max(40, limit // 3))
                    a = indicators.analyze(ltf)
                    b = indicators.analyze(higher)
                    if a:
                        a["htfScore"] = b.get("score", 0) if b else 0
                        a["htfRsi"] = b.get("rsi", 50) if b else 50
                        indic[sym] = a
                except Exception as e:
                    log.debug("kline %s: %s", sym, e)

        await asyncio.gather(*(fetch_one(s) for s in symbols))

        # ---- 打包省 Token JSON ----
        payload = {
            "equity": round(equity, 2),
            "upnl": round(sum(p["unrealizedPnl"] for p in positions), 2),
            "mode": "模拟盘" if dry_run else "实盘",
            "positions": [
                {"sym": p["symbol"], "side": p["side"], "pnl": round(p["unrealizedPnl"], 2), "lev": p["leverage"]}
                for p in positions
            ],
            "symbols": list(indic.keys()),
            "m": indic,
        }

        if halted:
            db.add_log(f"熔断状态，仅观察不下单：{why}", "warn")

        # ---- AI 决策（故障切换） ----
        try:
            resp = await ai_engine.ask_ai(payload, timeout=db.get_int("llm_timeout", 60))
        except ai_engine.AIError as e:
            db.add_log(f"AI 决策失败：{str(e)[:250]}", "error")
            return

        db.set_state(active_model=resp["model"])
        actions = resp["actions"] or []
        db.add_log(f"AI({resp['model']} {resp['latency_ms']}ms) 给出 {len(actions)} 条指令", "info")

        if not actions:
            return

        # ---- 逐条 风控 → 执行 ----
        for a in actions:
            sym, act, conf = a["symbol"], a["action"], a["confidence"]
            if not sym or act == "hold":
                continue
            decision = {
                "ts": int(time.time()), "cycle": self._cycle, "model": resp["model"],
                "latency_ms": resp["latency_ms"], "symbol": sym, "action": act,
                "confidence": conf, "reason": a["reason"], "raw": resp["raw"],
            }

            if act == "close":
                ok, why2, pos = risk.check_close(sym, positions)
                decision.update(approved=ok, reject_reason="" if ok else why2)
                db.add_decision(decision)
                if ok:
                    await trader.execute_close(pos, dry_run, reason=f"AI: {a['reason']}")
                continue

            if halted:
                decision.update(approved=0, reject_reason=why)
                db.add_decision(decision)
                continue

            price = indic.get(sym, {}).get("p", 0)
            verdict = risk.check_open(sym, act, equity, positions, conf, dry_run)
            decision.update(approved=int(verdict["approved"]), reject_reason="" if verdict["approved"] else verdict["reason"])
            db.add_decision(decision)

            if not verdict["approved"]:
                db.add_log(f"风控拒绝 {sym} {act}: {verdict['reason']}", "warn")
                continue

            r = await trader.execute_open(verdict["order"], price, dry_run)
            if r.get("ok"):
                # 执行成功后同步本地持仓视图，避免同轮重复开仓
                positions.append({
                    "symbol": sym, "side": "long" if act == "open_long" else "short",
                    "contracts": 0, "notional": verdict["order"]["notional"],
                    "unrealizedPnl": 0, "leverage": verdict["order"]["leverage"],
                    "entryPrice": price, "markPrice": price, "margin": 0, "liquidationPrice": 0,
                })

        # ---- 通知 webhook（可选） ----
        await self._notify(payload, actions)


    async def _notify(self, payload: dict, actions: list[dict]):
        url = db.get_setting("notify_webhook", "")
        if not url or not actions:
            return
        try:
            import httpx
            text = f"OKK AI 决策 [{datetime.now().strftime('%H:%M')}] 资金={payload['equity']}\n"
            for a in actions[:5]:
                text += f"- {a['symbol']} {a['action']} conf={a['confidence']} {a['reason']}\n"
            async with httpx.AsyncClient(timeout=8) as cli:
                await cli.post(url, json={"msg_type": "text", "content": {"text": text},
                                          "text": text, "msg": text})
        except Exception as e:
            log.debug("notify failed: %s", e)


engine = TradingEngine()
