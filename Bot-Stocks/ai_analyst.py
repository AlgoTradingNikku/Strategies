"""
ai_analyst.py
=============
AI / LLM-based Signal Analysis Module for Bot-Stocks.

Provides automated LLM analysis and recommendations for scanned trading signals.
Supports Cloud LLMs (OpenAI, DeepSeek, Anthropic) and Local LLMs (Ollama).

Fail-open design: Network timeouts, missing API keys, or provider errors return
safe fallback structures without breaking scanning or order execution.
"""

import os
import json
import logging
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("UTBotSRChannelsScanner")

# Grade rank comparison helper
GRADE_RANKS = {"A": 4, "B": 3, "C": 2, "D": 1}

DEFAULT_AI_RESULT = {
    "ai_recommendation": "N/A",
    "ai_score": None,
    "ai_badge": None,
    "ai_reasoning": "AI analysis skipped or unavailable.",
}


def _build_prompt(signal: dict) -> str:
    """Build a concise technical context prompt for the LLM."""
    symbol = signal.get("symbol", "UNKNOWN")
    sig_type = signal.get("signal", "UNKNOWN")
    price = signal.get("close_price") or signal.get("close") or "N/A"
    setup_score = signal.get("setup_score", 0.0)
    grade = signal.get("grade", "N/A")
    grade_score = signal.get("grade_score", "N/A")
    reasons = signal.get("score_reasons", [])
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except Exception:
            reasons = [reasons]
    
    adx = signal.get("adx")
    rs_ratio = signal.get("rs_ratio")
    mtf = signal.get("mtf_trend") or signal.get("mtf", {})
    regime = signal.get("regime", "unknown")
    # Use `or` (not dict.get's default) because the key is often present with
    # a literal None value when risk_reward_enabled is off or ATR data is too
    # short — dict.get(key, default) only substitutes when the key is absent,
    # so a plain .get(..., "N/A") would leak "None" straight into the prompt.
    sl = signal.get("stop_loss") or None
    tp = signal.get("target") or None
    rr = signal.get("risk_reward") or None
    risk_defined = sl is not None and tp is not None

    if risk_defined:
        risk_line = f"Stop Loss: ₹{sl} | Target: ₹{tp} | Risk/Reward Ratio: {rr if rr is not None else 'N/A'}"
    else:
        risk_line = (
            "Stop Loss / Target: NOT COMPUTED (risk/reward calculator disabled or "
            "insufficient price history for this symbol — this is a data-availability "
            "gap, not evidence of a bad setup)"
        )

    prompt = f"""You are an expert quantitative stock trader reviewing scanner signals.
Evaluate the following trade setup and provide a JSON recommendation.

--- STOCK SIGNAL DATA ---
Symbol: {symbol}
Direction: {sig_type}
Current Price: ₹{price}
Rule-Based Setup Score: {setup_score}/100
Market Quality Grade: Grade {grade} (Score: {grade_score})
Market Regime: {regime}
ADX Trend Strength: {adx if adx is not None else 'N/A'}
Relative Strength vs NIFTY: {rs_ratio if rs_ratio is not None else 'N/A'}
Multi-Timeframe Trend: {mtf}
{risk_line}
Triggered Setup Reasons: {', '.join(reasons) if reasons else 'Standard Rule Match'}

--- INSTRUCTIONS ---
Base "ai_recommendation" and "ai_score" primarily on signal/context quality:
setup score, grade, ADX, relative strength, multi-timeframe agreement, and
regime. Do NOT treat a missing/not-computed Stop Loss or Target as a reason
to AVOID a technically strong setup — that reflects a data or config gap, not
the market. If risk parameters are not computed, mention it only as a
separate caveat in "ai_reasoning" (e.g. "manual stop required"), not as the
primary driver of the recommendation.

Respond ONLY with a valid raw JSON object (no markdown formatting, no code blocks) with keys:
{{
  "ai_recommendation": "STRONG BUY" | "BUY" | "NEUTRAL" | "AVOID" | "STRONG SELL" | "SELL",
  "ai_score": <number 0 to 100>,
  "ai_badge": "<Short visual tag e.g. '⭐ AI Recommended', '🎯 High Conviction', '⚠️ High Risk'>",
  "ai_reasoning": "<1 to 2 concise sentences explaining rationale>"
}}
"""
    return prompt.strip()


def _call_openai_compatible_api(
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 8.0,
    extra_headers: dict = None,
) -> str:
    """HTTP client for OpenAI/DeepSeek/Custom OpenAI-compatible API endpoints (e.g. IBM ICA)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a quantitative stock analysis assistant. Return output strictly in valid JSON format."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            content = resp_data["choices"][0]["message"]["content"]
            return content
    except urllib.error.HTTPError as e:
        # Retry without response_format if proxy/gateway rejects json_object mode (HTTP 400)
        if e.code == 400 and "response_format" in payload:
            payload.pop("response_format", None)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                content = resp_data["choices"][0]["message"]["content"]
                return content
        raise e


def _call_ollama_api(
    base_url: str,
    model: str,
    prompt: str,
    timeout: float = 8.0,
) -> str:
    """HTTP client for local Ollama server API."""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as response:
        resp_data = json.loads(response.read().decode("utf-8"))
        return resp_data.get("response", "")


def _call_anthropic_api(
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 8.0,
) -> str:
    """HTTP client for Anthropic Claude API."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as response:
        resp_data = json.loads(response.read().decode("utf-8"))
        content_blocks = resp_data.get("content", [])
        if content_blocks and "text" in content_blocks[0]:
            return content_blocks[0]["text"]
        return ""


def analyze_signal(signal: dict, config: dict = None) -> dict:
    """
    Analyze a single stock signal using LLM and return structured recommendation dict.
    Fail-open: Returns DEFAULT_AI_RESULT on error or missing config.
    """
    cfg = (config or {}).get("ai_analysis", {})
    if not cfg.get("enabled", True):
        return dict(DEFAULT_AI_RESULT)

    provider = cfg.get("provider", "openai").lower()
    model = cfg.get("model", "gpt-4o-mini")
    api_key = cfg.get("api_key_env", "")  # Read API key directly from config
    timeout = float(cfg.get("timeout_seconds", 8.0))

    if provider != "ollama" and not api_key:
        log.debug("ai_analyst: API key missing in config for provider %s", provider)
        res = dict(DEFAULT_AI_RESULT)
        res["ai_reasoning"] = "API Key missing in config."
        return res

    prompt = _build_prompt(signal)

    try:
        raw_response = ""
        base_url = cfg.get("base_url") or cfg.get("custom_endpoint", "")
        if base_url or provider in ("openai_compatible", "custom", "ica"):
            if not base_url:
                base_url = "https://api.openai.com/v1"
            endpoint = base_url.rstrip("/")
            if not endpoint.endswith("/chat/completions"):
                endpoint += "/chat/completions"
            raw_response = _call_openai_compatible_api(
                endpoint, api_key, model, prompt, timeout
            )
        elif provider == "openai":
            raw_response = _call_openai_compatible_api(
                "https://api.openai.com/v1/chat/completions",
                api_key, model, prompt, timeout
            )
        elif provider == "deepseek":
            raw_response = _call_openai_compatible_api(
                "https://api.deepseek.com/v1/chat/completions",
                api_key, model, prompt, timeout
            )
        elif provider == "ollama":
            ollama_url = cfg.get("ollama_url", "http://localhost:11434")
            raw_response = _call_ollama_api(ollama_url, model, prompt, timeout)
        elif provider == "anthropic":
            raw_response = _call_anthropic_api(api_key, model, prompt, timeout)
        else:
            log.warning("ai_analyst: Unknown provider '%s'", provider)
            return dict(DEFAULT_AI_RESULT)

        clean_json = raw_response.strip()
        if clean_json.startswith("```"):
            lines = clean_json.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_json = "\n".join(lines).strip()

        parsed = json.loads(clean_json)

        rec = str(parsed.get("ai_recommendation", "NEUTRAL")).upper()
        score = parsed.get("ai_score")
        badge = parsed.get("ai_badge") or ("⭐ AI Recommended" if "BUY" in rec else "🤖 Evaluated")
        reasoning = str(parsed.get("ai_reasoning", "Analyzed by AI.")).strip()

        return {
            "ai_recommendation": rec,
            "ai_score": score,
            "ai_badge": badge,
            "ai_reasoning": reasoning,
        }

    except urllib.error.URLError as e:
        log.warning("ai_analyst network error for %s: %s", signal.get("symbol"), e)
    except Exception as e:
        log.warning("ai_analyst evaluation failed for %s: %s", signal.get("symbol"), e)

    res = dict(DEFAULT_AI_RESULT)
    res["ai_reasoning"] = "AI evaluation timed out or network error."
    return res


def analyze_signals_batch(signals: list[dict], config: dict = None) -> list[dict]:
    """
    Filter and analyze a batch of candidate signals.
    Modifies signals in place by adding AI fields and returns updated list.
    """
    if not signals:
        return signals

    cfg = (config or {}).get("ai_analysis", {})
    if not cfg.get("enabled", True):
        for sig in signals:
            sig.update(DEFAULT_AI_RESULT)
        return signals

    min_grade = str(cfg.get("eval_min_grade", "C")).upper()
    min_rank = GRADE_RANKS.get(min_grade, 2)
    max_candidates = int(cfg.get("max_candidates_per_scan", 5))

    candidates = []
    for sig in signals:
        grade = str(sig.get("grade", "D")).upper()
        rank = GRADE_RANKS.get(grade, 1)
        if rank >= min_rank:
            candidates.append(sig)

    candidates.sort(
        key=lambda x: (x.get("grade_score") or 0, x.get("setup_score") or 0),
        reverse=True,
    )
    selected = candidates[:max_candidates]

    if selected:
        with ThreadPoolExecutor(max_workers=min(5, len(selected))) as executor:
            future_to_sig = {
                executor.submit(analyze_signal, sig, config): sig for sig in selected
            }
            for future in as_completed(future_to_sig):
                sig = future_to_sig[future]
                try:
                    res = future.result()
                    sig.update(res)
                except Exception as e:
                    log.warning("ai_analyst batch worker error: %s", e)
                    sig.update(DEFAULT_AI_RESULT)

    for sig in signals:
        if "ai_recommendation" not in sig:
            sig.update(DEFAULT_AI_RESULT)

    return signals

