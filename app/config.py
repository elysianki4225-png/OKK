"""全局配置：环境变量 + data 目录 + 密钥文件管理。"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "okk.db"
SECRET_FILE = DATA_DIR / "secret.key"
MASTER_KEY_FILE = DATA_DIR / "master.key"

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()  # live / paper
TZ_NAME = os.getenv("TZ_NAME", "Asia/Shanghai")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))
SCAN_TOP_N = int(os.getenv("SCAN_TOP_N", "25"))
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "120"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# ---------- 硬性风控上限（代码级强制，Web 端配置不可突破） ----------
HARD_MAX_LEVERAGE = 3.0        # 最大杠杆 3 倍
HARD_MAX_POSITION_PCT = 20.0   # 单笔最大仓位 20% 总资产
HARD_MAX_CONCURRENT = 5        # 最大同时持仓数
HARD_MIN_SL_PCT = 0.3          # 止损最小百分比
HARD_MAX_SL_PCT = 15.0         # 止损最大百分比


def _load_or_create(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    key = secrets.token_hex(32).encode()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def secret_key() -> bytes:
    return _load_or_create(SECRET_FILE)


def master_key() -> bytes:
    return _load_or_create(MASTER_KEY_FILE)


# 运行期默认配置（可在 Web 设置里覆盖，存 SQLite）
DEFAULTS: dict[str, str] = {
    "trading_mode": TRADING_MODE,            # live / paper
    "scan_interval": str(SCAN_INTERVAL),
    "scan_top_n": str(SCAN_TOP_N),
    "kline_limit": str(KLINE_LIMIT),
    "timeframe": "15m",                      # 主周期
    "htf": "1h",                             # 高级周期（趋势过滤）
    "max_leverage": "3",                     # <= 3（硬上限）
    "max_position_pct": "20",                # <= 20（硬上限）
    "max_concurrent": "3",                   # <= 5（硬上限）
    "stop_loss_pct": "2.0",                  # 默认止损 %
    "take_profit_pct": "4.0",                # 默认止盈 %
    "min_confidence": "0.60",                # AI 置信度阈值
    "dedupe_minutes": "60",                  # 防重锁时长（分钟）
    "daily_loss_limit_pct": "10",            # 当日最大回撤熔断 %
    "consecutive_loss_cooldown": "3",        # 连亏 N 单后冷却
    "cooldown_minutes": "120",
    "auto_start": "0",
    "notify_webhook": "",                    # 可选：企业微信/TG Bot/飞书 webhook
    "llm_timeout": "60",
    "risk_auto_sl_tp": "1",                  # 强制附带止盈止损
}
