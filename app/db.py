"""SQLite 存储层：设置 / 状态 / 日志 / 决策 / 交易 / 净值快照 / 密钥 / 防重锁。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DB_PATH, DEFAULTS

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS exchange_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT UNIQUE NOT NULL,        -- okx / binance
    api_key     TEXT NOT NULL,               -- Fernet 加密
    api_secret  TEXT NOT NULL,
    passphrase  TEXT DEFAULT '',
    testnet     INTEGER DEFAULT 0,
    enabled     INTEGER DEFAULT 1,
    updated_at  INTEGER
);

CREATE TABLE IF NOT EXISTS llm_models (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    model      TEXT NOT NULL,                -- litellm 模型串，如 openai/gpt-4o-mini
    api_key    TEXT DEFAULT '',
    base_url   TEXT DEFAULT '',
    enabled    INTEGER DEFAULT 1,
    priority   INTEGER DEFAULT 100,
    latency_ms INTEGER DEFAULT 0,
    ok_rate    REAL DEFAULT 1.0,
    last_ok    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS engine_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    running      INTEGER DEFAULT 0,
    paused_until INTEGER DEFAULT 0,          -- 时间戳；0=未暂停；-1=无限期暂停
    status       TEXT DEFAULT 'stopped',
    last_cycle   INTEGER DEFAULT 0,
    last_error   TEXT DEFAULT '',
    cycles       INTEGER DEFAULT 0,
    active_model TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    level   TEXT DEFAULT 'info',
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    cycle       INTEGER DEFAULT 0,
    model       TEXT DEFAULT '',
    latency_ms  INTEGER DEFAULT 0,
    symbol      TEXT DEFAULT '',
    action      TEXT DEFAULT '',             -- open_long / open_short / close / hold
    confidence  REAL DEFAULT 0,
    reason      TEXT DEFAULT '',
    approved    INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    raw         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions(ts);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    action     TEXT NOT NULL,                -- open_long/open_short/close
    side       TEXT NOT NULL,                -- buy/sell
    qty        REAL DEFAULT 0,
    price      REAL DEFAULT 0,
    leverage   REAL DEFAULT 1,
    notional   REAL DEFAULT 0,
    sl         REAL DEFAULT 0,
    tp         REAL DEFAULT 0,
    order_id   TEXT DEFAULT '',
    pnl        REAL DEFAULT 0,
    fee        REAL DEFAULT 0,
    status     TEXT DEFAULT 'submitted',     -- submitted / filled / failed / dry_run
    detail     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tr_ts ON trades(ts);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts       INTEGER NOT NULL,
    equity   REAL NOT NULL,
    unrealized REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_eq_ts ON equity_snapshots(ts);

CREATE TABLE IF NOT EXISTS dedupe_locks (
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    PRIMARY KEY (symbol, action)
);
"""


def init_db() -> None:
    with _lock:
        conn = _get_conn()
        conn.executescript(SCHEMA)
        for k, v in DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        conn.execute("INSERT OR IGNORE INTO engine_state(id) VALUES(1)")
        conn.commit()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


@contextmanager
def tx():
    with _lock:
        conn = _get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ---------------- settings ----------------
def get_setting(key: str, default: str = "") -> str:
    with _lock:
        row = _get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def get_settings() -> dict[str, str]:
    with _lock:
        rows = _get_conn().execute("SELECT key,value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_settings(kv: dict[str, Any]) -> None:
    with tx() as conn:
        for k, v in kv.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )


def get_int(key: str, default: int) -> int:
    try:
        return int(float(get_setting(key, str(default))))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    try:
        return float(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


# ---------------- engine state ----------------
def get_state() -> dict[str, Any]:
    with _lock:
        row = _get_conn().execute("SELECT * FROM engine_state WHERE id=1").fetchone()
        return dict(row) if row else {}


def set_state(**kw: Any) -> None:
    cols = ",".join(f"{k}=?" for k in kw)
    with tx() as conn:
        conn.execute(f"UPDATE engine_state SET {cols} WHERE id=1", tuple(kw.values()))


# ---------------- logs ----------------
def add_log(message: str, level: str = "info") -> None:
    with tx() as conn:
        conn.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)", (int(time.time()), level, message[:2000]))
        conn.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 2000)")


def get_logs(limit: int = 200) -> list[dict]:
    with _lock:
        rows = _get_conn().execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---------------- decisions ----------------
def add_decision(d: dict) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO decisions(ts,cycle,model,latency_ms,symbol,action,confidence,reason,approved,reject_reason,raw) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.get("ts", int(time.time())), d.get("cycle", 0), d.get("model", ""),
                d.get("latency_ms", 0), d.get("symbol", ""), d.get("action", ""),
                d.get("confidence", 0), d.get("reason", ""), int(bool(d.get("approved"))),
                d.get("reject_reason", ""), d.get("raw", "")[:8000],
            ),
        )


def get_decisions(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _get_conn().execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---------------- trades ----------------
def add_trade(t: dict) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO trades(ts,symbol,action,side,qty,price,leverage,notional,sl,tp,order_id,pnl,fee,status,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t.get("ts", int(time.time())), t["symbol"], t["action"], t["side"],
                t.get("qty", 0), t.get("price", 0), t.get("leverage", 1), t.get("notional", 0),
                t.get("sl", 0), t.get("tp", 0), t.get("order_id", ""), t.get("pnl", 0),
                t.get("fee", 0), t.get("status", "submitted"), t.get("detail", "")[:2000],
            ),
        )
        return int(cur.lastrowid)


def get_trades(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _get_conn().execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def today_realized_pnl(day_start_ts: int) -> float:
    with _lock:
        row = _get_conn().execute(
            "SELECT COALESCE(SUM(pnl),0) AS s FROM trades WHERE ts>=? AND status!='failed'", (day_start_ts,)
        ).fetchone()
        return float(row["s"])


# ---------------- equity ----------------
def add_equity(equity: float, unrealized: float) -> None:
    with tx() as conn:
        conn.execute("INSERT INTO equity_snapshots(ts,equity,unrealized) VALUES(?,?,?)", (int(time.time()), equity, unrealized))
        conn.execute("DELETE FROM equity_snapshots WHERE ts < ?", (int(time.time()) - 90 * 86400,))


def equity_series(hours: int = 48) -> list[dict]:
    since = int(time.time()) - hours * 3600
    with _lock:
        rows = _get_conn().execute(
            "SELECT ts,equity,unrealized FROM equity_snapshots WHERE ts>=? ORDER BY ts", (since,)
        ).fetchall()
        return [dict(r) for r in rows]


def day_start_equity(day_start_ts: int) -> float | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT equity FROM equity_snapshots WHERE ts>=? ORDER BY ts LIMIT 1", (day_start_ts,)
        ).fetchone()
        return float(row["equity"]) if row else None


# ---------------- exchange keys ----------------
def save_exchange_keys(exchange: str, enc: dict) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO exchange_keys(exchange,api_key,api_secret,passphrase,testnet,enabled,updated_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(exchange) DO UPDATE SET api_key=excluded.api_key,api_secret=excluded.api_secret,"
            "passphrase=excluded.passphrase,testnet=excluded.testnet,enabled=excluded.enabled,updated_at=excluded.updated_at",
            (exchange, enc["api_key"], enc["api_secret"], enc.get("passphrase", ""),
             int(enc.get("testnet", 0)), 1, int(time.time())),
        )


def get_exchange_keys(exchange: str) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM exchange_keys WHERE exchange=?", (exchange,)).fetchone()
        return dict(row) if row else None


def get_enabled_exchange() -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM exchange_keys WHERE enabled=1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def clear_exchange_keys(exchange: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM exchange_keys WHERE exchange=?", (exchange,))


# ---------------- llm models ----------------
def upsert_llm_model(m: dict) -> int:
    with tx() as conn:
        if m.get("id"):
            conn.execute(
                "UPDATE llm_models SET label=?,model=?,api_key=?,base_url=?,enabled=?,priority=? WHERE id=?",
                (m["label"], m["model"], m.get("api_key", ""), m.get("base_url", ""),
                 int(m.get("enabled", 1)), int(m.get("priority", 100)), m["id"]),
            )
            return int(m["id"])
        cur = conn.execute(
            "INSERT INTO llm_models(label,model,api_key,base_url,enabled,priority) VALUES(?,?,?,?,?,?)",
            (m["label"], m["model"], m.get("api_key", ""), m.get("base_url", ""),
             int(m.get("enabled", 1)), int(m.get("priority", 100))),
        )
        return int(cur.lastrowid)


def delete_llm_model(model_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM llm_models WHERE id=?", (model_id,))


def get_llm_models(only_enabled: bool = False) -> list[dict]:
    q = "SELECT * FROM llm_models" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY priority, latency_ms"
    with _lock:
        return [dict(r) for r in _get_conn().execute(q).fetchall()]


def update_llm_stats(model_id: int, latency_ms: int, ok: bool) -> None:
    with tx() as conn:
        row = conn.execute("SELECT ok_rate, latency_ms FROM llm_models WHERE id=?", (model_id,)).fetchone()
        if not row:
            return
        rate = float(row["ok_rate"]) * 0.8 + (0.2 if ok else 0.0)
        new_latency = latency_ms if ok else int(row["latency_ms"] or 0)  # 失败不覆盖历史延迟
        conn.execute(
            "UPDATE llm_models SET latency_ms=?, ok_rate=?, last_ok=? WHERE id=?",
            (new_latency, round(rate, 3), int(time.time()) if ok else 0, model_id),
        )


# ---------------- dedupe locks ----------------
def acquire_dedupe(symbol: str, action: str, minutes: int) -> bool:
    """防重锁：minutes 内同一 symbol+action 不允许重复下单。返回是否获取成功。"""
    now = int(time.time())
    with tx() as conn:
        row = conn.execute("SELECT ts FROM dedupe_locks WHERE symbol=? AND action=?", (symbol, action)).fetchone()
        if row and now - int(row["ts"]) < minutes * 60:
            return False
        conn.execute(
            "INSERT INTO dedupe_locks(symbol,action,ts) VALUES(?,?,?) "
            "ON CONFLICT(symbol,action) DO UPDATE SET ts=excluded.ts",
            (symbol, action, now),
        )
        conn.execute("DELETE FROM dedupe_locks WHERE ts < ?", (now - 7 * 86400,))
        return True


def release_dedupe(symbol: str, action: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM dedupe_locks WHERE symbol=? AND action=?", (symbol, action))
