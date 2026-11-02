"""AI 引擎：litellm 多模型接入、测速、故障切换、省 Token 提示词。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from . import db, security

log = logging.getLogger("okk.ai")

SYSTEM_PROMPT = (
    "你是量化合约交易 Agent。只输出 JSON，不要任何解释、markdown 或多余文字。\n"
    "输入是压缩后的行情指标 JSON：p=现价, chg1/4/24=涨幅%, rsi, macd{macd,signal,hist}, "
    "boll{mid,upper,lower,width,pos}, atrPct=波动%, ema7/25/99, volx=量比, score=本地信号分。\n"
    "账户字段：equity=总资金, upnl=未实现盈亏, positions=当前持仓。\n"
    "输出格式严格为：\n"
    "{\"actions\":[{\"sym\":\"BTC/USDT\",\"act\":\"open_long|open_short|close|hold\",\"conf\":0.0-1.0,\"reason\":\"20字内\"}]}\n"
    "规则：1)只挑最有把握的机会,宁缺毋滥 2)持仓有反向信号优先close 3)conf<0.6不要开仓 4)趋势与动量共振才开仓 5)震荡市多hold。"
)

VALID_ACTS = {"open_long", "open_short", "close", "hold"}


class AIError(Exception):
    pass


# ---------------- 测速 ----------------
async def speed_test(model_rows: list[dict] | None = None, quick: bool = True) -> list[dict]:
    """对每个启用模型发一条极小请求测延迟，更新数据库并返回排序结果。"""
    rows = model_rows or db.get_llm_models(only_enabled=True)
    results = []

    async def probe(row: dict) -> dict:
        t0 = time.time()
        ok, err = False, ""
        try:
            await _call_llm(row, messages=[
                {"role": "user", "content": "ping，回复 pong"}
            ], max_tokens=4, timeout=12 if quick else 30)
            ok = True
        except Exception as e:
            err = str(e)[:200]
        ms = int((time.time() - t0) * 1000)
        db.update_llm_stats(row["id"], ms, ok)
        return {"id": row["id"], "label": row["label"], "model": row["model"],
                "latency_ms": ms if ok else -1, "ok": ok, "error": err}

    tasks = [probe(r) for r in rows if r.get("enabled")]
    if tasks:
        results = await asyncio.gather(*tasks)
    results.sort(key=lambda r: (not r["ok"], r["latency_ms"] if r["ok"] else 1e9))
    return list(results)


# ---------------- 调用 ----------------
def _build_client_kwargs(row: dict) -> dict:
    kw: dict[str, Any] = {}
    if row.get("api_key"):
        kw["api_key"] = security.decrypt(row["api_key"]) or row["api_key"]
    if row.get("base_url"):
        kw["api_base"] = row["base_url"]
    return kw


async def _call_llm(row: dict, messages: list[dict], max_tokens: int = 400,
                    timeout: int = 60, json_mode: bool = True) -> str:
    """litellm 调用（惰性导入，失败自动降级到 OpenAI 兼容 HTTP）。"""
    model = row["model"]
    client_kw = _build_client_kwargs(row)
    temperature = 0.2

    try:
        import litellm
        resp = await litellm.acompletion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            response_format={"type": "json_object"} if json_mode else None,
            **client_kw,
        )
        return resp["choices"][0]["message"]["content"] or ""
    except ImportError:
        pass
    except Exception as e:
        # JSON 模式不被支持时重试一次
        if "response_format" in str(e) or "json" in str(e).lower():
            try:
                import litellm
                resp = await litellm.acompletion(
                    model=model, messages=messages, max_tokens=max_tokens,
                    temperature=temperature, timeout=timeout, **client_kw,
                )
                return resp["choices"][0]["message"]["content"] or ""
            except Exception as e2:
                raise AIError(f"{model}: {e2}") from e2
        raise AIError(f"{model}: {e}") from e

    # litellm 未安装 → OpenAI 兼容 REST 兜底（DeepSeek/Kimi/Groq/硅基流动等都兼容）
    return await _openai_compat(row, messages, max_tokens, timeout)


async def _openai_compat(row: dict, messages: list[dict], max_tokens: int, timeout: int) -> str:
    import httpx

    base = (row.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    key = security.decrypt(row.get("api_key") or "") or row.get("api_key") or ""
    model = row["model"].split("/")[-1]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    async with httpx.AsyncClient(timeout=timeout) as cli:
        r = await cli.post(f"{base}/chat/completions", json=payload,
                           headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""


def _parse_actions(content: str, known_symbols: set[str]) -> list[dict]:
    """鲁棒解析模型输出：容忍 ```json 包裹、前后缀废话。"""
    text = content.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError(f"AI 输出无 JSON: {text[:200]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(f"AI JSON 解析失败: {e}") from e

    actions = data.get("actions") if isinstance(data, dict) else data
    if not isinstance(actions, list):
        raise AIError("AI 输出缺少 actions 列表")
    out = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        sym = str(a.get("sym") or a.get("symbol") or "").upper().replace("-", "/")
        act = str(a.get("act") or a.get("action") or "hold").lower()
        if act not in VALID_ACTS:
            act = "hold"
        if sym and known_symbols and sym not in known_symbols:
            continue
        out.append({
            "symbol": sym,
            "action": act,
            "confidence": max(0.0, min(1.0, float(a.get("conf") or a.get("confidence") or 0))),
            "reason": str(a.get("reason") or "")[:120],
        })
    return out


async def ask_ai(payload: dict, timeout: int = 60) -> dict:
    """按 测速优先级 依次尝试模型（故障切换），返回决策列表。"""
    models = db.get_llm_models(only_enabled=True)
    if not models:
        raise AIError("未配置任何 AI 模型，请到 设置 → AI 模型 里添加")
    models.sort(key=lambda m: (m.get("priority", 100), m.get("latency_ms") or 0, -(m.get("ok_rate") or 0)))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": security.json_dumps(payload)},
    ]
    known = set(payload.get("symbols") or [])
    errors: list[str] = []

    for row in models:
        t0 = time.time()
        try:
            content = await _call_llm(row, messages, max_tokens=500, timeout=timeout, json_mode=True)
            ms = int((time.time() - t0) * 1000)
            actions = _parse_actions(content, known)
            db.update_llm_stats(row["id"], ms, True)
            return {"actions": actions, "model": row["label"] or row["model"],
                    "model_id": row["id"], "latency_ms": ms, "raw": content[:4000]}
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            db.update_llm_stats(row["id"], ms, False)
            errors.append(f"{row['label'] or row['model']}: {str(e)[:160]}")
            log.warning("AI 模型失败，切换下一个: %s", errors[-1])
            continue

    raise AIError("所有模型均失败 → " + " | ".join(errors)[:500])
