"""交易所封装（ccxt.async_support）：欧易 OKX / 币安 Binance 统一接口。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import ccxt.async_support as ccxt

from . import db, security

log = logging.getLogger("okk.exchange")

SUPPORTED = {"okx": "okx", "binance": "binanceusdm", "binance-usdm": "binanceusdm"}


class ExchangeError(Exception):
    pass


class ExchangeClient:
    """统一封装：余额 / 持仓 / 行情 / K 线 / 下单 / 止盈止损 / 平仓。"""

    def __init__(self):
        self.client = None
        self.exchange_id = ""
        self.testnet = False
        self._markets_loaded = False

    # ---------- 初始化 ----------
    async def connect_from_db(self) -> bool:
        row = db.get_enabled_exchange()
        if not row:
            return False
        api_key = security.decrypt(row["api_key"])
        api_secret = security.decrypt(row["api_secret"])
        passphrase = security.decrypt(row.get("passphrase") or "")
        return await self.connect(row["exchange"], api_key, api_secret, passphrase, bool(row.get("testnet")))

    async def connect(self, exchange: str, api_key: str, api_secret: str, passphrase: str = "", testnet: bool = False) -> bool:
        await self.close()
        ex_id = SUPPORTED.get(exchange.lower().replace("_", ""), None) or exchange.lower()
        klass = getattr(ccxt, ex_id, None)
        if klass is None:
            raise ExchangeError(f"不支持的交易所: {exchange}")
        opts: dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap", "warnOnFetchOpenOrdersWithoutSymbol": False},
            "timeout": 30000,
        }
        if passphrase:
            opts["password"] = passphrase
        self.client = klass(opts)
        self.exchange_id = ex_id
        self.testnet = testnet
        if testnet:
            try:
                self.client.set_sandbox_mode(True)
            except Exception:
                pass
        await self.client.load_markets()
        self._markets_loaded = True
        return True

    async def close(self):
        if self.client:
            try:
                await self.client.close()
            except Exception:
                pass
            self.client = None
            self._markets_loaded = False

    @property
    def ready(self) -> bool:
        return self.client is not None

    def _need(self):
        if not self.client:
            raise ExchangeError("交易所未连接，请先在设置里绑定 API Key")

    # ---------- 市场 ----------
    def perp_markets(self) -> list[dict]:
        self._need()
        out = []
        for sym, m in self.client.markets.items():
            if m.get("swap") and m.get("active", True) and m.get("linear") and m.get("quote") == "USDT":
                out.append(m)
        return out

    async def top_symbols(self, n: int) -> list[str]:
        """按 24h 成交额取前 n 个 USDT 永续合约。"""
        self._need()
        tickers = await self.client.fetch_tickers()
        perp = {m["symbol"] for m in self.perp_markets()}
        scored = []
        for sym, t in tickers.items():
            if sym not in perp:
                continue
            qv = t.get("quoteVolume") or 0
            if qv:
                scored.append((qv, sym))
        scored.sort(reverse=True)
        return [s for _, s in scored[:n]]

    async def ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 120) -> list:
        self._need()
        return await self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    async def ticker(self, symbol: str) -> dict:
        self._need()
        return await self.client.fetch_ticker(symbol)

    # ---------- 账户 ----------
    async def balance(self) -> dict:
        """返回 {'total': float, 'free': float, 'used': float}（USDT 计价）。"""
        self._need()
        bal = await self.client.fetch_balance()
        usdt = bal.get("USDT") or {}
        total = float(usdt.get("total") or 0)
        return {
            "total": total,
            "free": float(usdt.get("free") or 0),
            "used": float(usdt.get("used") or 0),
        }

    async def positions(self) -> list[dict]:
        self._need()
        raw = await self.client.fetch_positions()
        out = []
        for p in raw:
            contracts = float(p.get("contracts") or 0)
            if contracts == 0:
                continue
            out.append({
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "contracts": contracts,
                "notional": float(p.get("notional") or 0),
                "entryPrice": float(p.get("entryPrice") or 0),
                "markPrice": float(p.get("markPrice") or 0),
                "unrealizedPnl": float(p.get("unrealizedPnl") or 0),
                "leverage": float(p.get("leverage") or 1),
                "liquidationPrice": float(p.get("liquidationPrice") or 0),
                "margin": float(p.get("initialMargin") or p.get("margin") or 0),
            })
        return out

    async def set_leverage(self, symbol: str, leverage: float):
        self._need()
        try:
            params = {"mgnMode": "cross"} if self.exchange_id == "okx" else {}
            await self.client.set_leverage(leverage, symbol, params=params)
        except Exception as e:  # 有些交易所/账户模式不支持，忽略
            log.warning("set_leverage %s %s: %s", symbol, leverage, e)

    # ---------- 下单 ----------
    async def open_position(self, symbol: str, side: str, notional_usdt: float,
                            leverage: float, sl_price: float = 0, tp_price: float = 0,
                            dry_run: bool = False) -> dict:
        """市价开仓（buy=long / sell=short），自动附带止盈止损。
        notional_usdt: 名义仓位（USDT），内部换算成合约数量并按交易所精度取整。
        """
        self._need()
        t = await self.client.fetch_ticker(symbol)
        price = float(t.get("last") or t.get("close") or 0)
        if price <= 0:
            raise ExchangeError(f"{symbol} 无有效价格")
        market = self.client.market(symbol)
        contract_size = float(market.get("contractSize") or 1)
        amount = notional_usdt / (price * contract_size)
        amount = float(self.client.amount_to_precision(symbol, amount))
        if amount <= 0:
            raise ExchangeError(f"{symbol} 下单数量过小（名义 {notional_usdt:.2f} USDT）")

        if dry_run:
            return {"id": f"dry-{int(time.time()*1000)}", "price": price, "amount": amount,
                    "status": "dry_run", "sl": sl_price, "tp": tp_price}

        await self.set_leverage(symbol, leverage)
        is_okx = self.exchange_id == "okx"
        params: dict[str, Any] = {"tdMode": "cross"} if is_okx else {}
        # 优先使用交易所原生附带止盈止损（OKX / Binance 均支持该 ccxt 参数）
        if sl_price > 0:
            params["stopLossPrice"] = float(self.client.price_to_precision(symbol, sl_price))
        if tp_price > 0:
            params["takeProfitPrice"] = float(self.client.price_to_precision(symbol, tp_price))
        try:
            order = await self.client.create_order(symbol, "market", side, amount, None, params)
        except Exception as e:
            # 回退：先成交市价单，再补触发单
            log.warning("附带止盈止损下单失败，改用分步: %s", e)
            order = await self.client.create_order(symbol, "market", side, amount, None,
                                                   {"tdMode": "cross"} if is_okx else {})
            await self._attach_stop_orders(symbol, side, amount, sl_price, tp_price)
        return {"id": order.get("id", ""), "price": price, "amount": amount,
                "status": order.get("status", "submitted"), "sl": sl_price, "tp": tp_price}

    async def _attach_stop_orders(self, symbol: str, side: str, amount: float, sl: float, tp: float):
        is_okx = self.exchange_id == "okx"
        base = {"reduceOnly": True, **({"tdMode": "cross"} if is_okx else {})}
        close_side = "sell" if side == "buy" else "buy"
        for price, typ in ((sl, "stop_market"), (tp, "take_profit_market")):
            if price <= 0:
                continue
            for attempt in (
                lambda: self.client.create_order(symbol, typ, close_side, amount, None, {**base, "stopPrice": price}),
                lambda: self.client.create_order(symbol, "market", close_side, amount, None,
                                                 {**base, "triggerPrice": price}),
            ):
                try:
                    await attempt()
                    break
                except Exception as e:
                    log.warning("%s %s 触发单尝试失败(%s): %s", symbol, typ, price, e)

    async def close_position(self, position: dict, dry_run: bool = False) -> dict:
        """市价全平某个持仓（reduceOnly）。"""
        self._need()
        symbol = position["symbol"]
        side = "sell" if position.get("side") == "long" else "buy"
        amount = float(position.get("contracts") or 0)
        if amount <= 0:
            return {"status": "noop"}
        if dry_run:
            return {"id": f"dry-close-{int(time.time()*1000)}", "status": "dry_run"}
        is_okx = self.exchange_id == "okx"
        base = {"reduceOnly": True, **({"tdMode": "cross"} if is_okx else {})}
        try:
            order = await self.client.create_order(symbol, "market", side, amount, None, base)
            return {"id": order.get("id", ""), "status": order.get("status", "filled")}
        except Exception as e:
            # 部分交易所 reduceOnly 报错时改用反向市价单
            log.warning("reduceOnly 平仓失败，尝试反向市价: %s", e)
            order = await self.client.create_order(symbol, "market", side, amount, None,
                                                   {"tdMode": "cross"} if is_okx else {})
            return {"id": order.get("id", ""), "status": order.get("status", "filled")}

    async def cancel_all(self, symbol: str | None = None):
        self._need()
        try:
            if symbol:
                await self.client.cancel_all_orders(symbol)
            else:
                for m in self.perp_markets():
                    try:
                        await self.client.cancel_all_orders(m["symbol"])
                    except Exception:
                        pass
        except Exception as e:
            log.warning("cancel_all: %s", e)

    async def fetch_funding(self, symbol: str) -> float | None:
        try:
            self._need()
            f = await self.client.fetch_funding_rate(symbol)
            return float(f.get("fundingRate") or 0)
        except Exception:
            return None


# 全局单例
exchange = ExchangeClient()


async def test_connection(exchange_name: str, api_key: str, api_secret: str, passphrase: str = "", testnet: bool = False) -> dict:
    """绑定前的连通性测试：返回余额与持仓数量。"""
    c = ExchangeClient()
    try:
        await c.connect(exchange_name, api_key, api_secret, passphrase, testnet)
        bal = await c.balance()
        pos = await c.positions()
        return {"ok": True, "balance": bal, "positions": len(pos), "exchange": c.exchange_id}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}
    finally:
        await c.close()


async def retry(coro_factory, retries: int = 2, delay: float = 1.5):
    """轻量重试（网络抖动）。"""
    last = None
    for i in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last = e
            if i < retries:
                await asyncio.sleep(delay * (i + 1))
    raise last
