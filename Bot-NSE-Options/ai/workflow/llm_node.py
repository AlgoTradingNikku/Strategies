"""
ai/workflow/llm_node.py
========================
LangChain LLM interpretation node.
Dispatches to the correct provider based on config ai.provider:
  openai_compatible  — any OpenAI-API-compatible gateway (IBM ICA, Azure, etc.)
  openai             — OpenAI directly
  anthropic          — Anthropic Claude
  deepseek           — DeepSeek (OpenAI-compatible)
  ollama             — local Ollama

API key is read from:
  1. config ai.api_key (direct value) — highest priority
  2. os.environ[ai.api_key_env]       — env-var override
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

_bot_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------

def llm_interpretation_node(state: dict) -> dict:
    """
    LLM interpretation node.
    Builds structured prompt → invokes LLM → validates response → returns LLMRecommendation.
    Provider is selected from config ai.provider. Falls back to OpenAI-compatible
    fallback if the primary call fails.
    The LLM receives ONLY pre-computed data — never raw chain data.
    """
    t0 = time.time()
    try:
        import yaml
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        ai_cfg = cfg.get("ai", {})

        from ai.prompts.analysis_prompt import SYSTEM_PROMPT, build_analysis_prompt, PROMPT_VERSION
        human_prompt = build_analysis_prompt(state)

        llm_rec = None
        provider_used = ""
        model_used = ""
        primary_exc = None

        provider = ai_cfg.get("provider", "openai_compatible").lower()

        try:
            llm_rec, provider_used, model_used = _invoke_primary(provider, ai_cfg, SYSTEM_PROMPT, human_prompt)
        except Exception as exc:
            primary_exc = exc
            log.warning("[LLM] Primary (%s) failed: %s — trying fallback", provider, exc)
            try:
                llm_rec, provider_used, model_used = _invoke_fallback(ai_cfg, SYSTEM_PROMPT, human_prompt)
            except Exception as fallback_exc:
                log.error("[LLM] Both providers failed. Primary: %s, Fallback: %s", exc, fallback_exc)
                return {
                    "llm_recommendation": None,
                    "llm_validation_passed": False,
                    "errors": [
                        *state.get("errors", []),
                        f"LLM invocation failed: {exc} / {fallback_exc}",
                    ],
                    "node_timings": {**state.get("node_timings", {}), "llm_node": time.time() - t0},
                }

        if llm_rec is None:
            return {
                "llm_recommendation": None,
                "llm_validation_passed": False,
                "errors": [*state.get("errors", []), "LLM returned empty response"],
                "node_timings": {**state.get("node_timings", {}), "llm_node": time.time() - t0},
            }

        # Attach metadata
        llm_rec.prompt_version = PROMPT_VERSION
        llm_rec.llm_provider = provider_used
        llm_rec.llm_model = model_used

        log.info(
            "[LLM] view=%s confidence=%d strategy=%s via %s/%s",
            llm_rec.market_view, llm_rec.confidence,
            llm_rec.selected_strategy_type, provider_used, model_used,
        )

        return {
            "llm_recommendation": llm_rec,
            "prompt_version": PROMPT_VERSION,
            "node_timings": {**state.get("node_timings", {}), "llm_node": time.time() - t0},
        }

    except Exception as exc:
        log.error("[LLM] Unexpected error: %s", exc)
        return {
            "llm_recommendation": None,
            "llm_validation_passed": False,
            "errors": [*state.get("errors", []), f"LLM node error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "llm_node": time.time() - t0},
        }


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _resolve_api_key(ai_cfg: dict, key_field: str = "api_key", env_field: str = "api_key_env") -> str:
    """
    Resolve API key with priority:
      1. config ai.api_key (direct value)
      2. os.environ[ai.api_key_env]
    """
    direct = ai_cfg.get(key_field, "")
    if direct:
        return direct
    env_var = ai_cfg.get(env_field, "")
    return os.environ.get(env_var, "") if env_var else ""


def _invoke_primary(provider: str, ai_cfg: dict, system_prompt: str, human_prompt: str):
    """Dispatch to the configured primary provider. Returns (llm_rec, provider_name, model)."""
    if provider == "anthropic":
        rec = _invoke_anthropic(ai_cfg, system_prompt, human_prompt)
        return rec, "anthropic", ai_cfg.get("model", "claude-sonnet-4-5")

    if provider in ("openai", "openai_compatible", "deepseek"):
        rec = _invoke_openai_compatible(ai_cfg, system_prompt, human_prompt, primary=True)
        return rec, provider, ai_cfg.get("model", "gpt-4o")

    if provider == "ollama":
        rec = _invoke_ollama(ai_cfg, system_prompt, human_prompt)
        return rec, "ollama", ai_cfg.get("model", "llama3")

    raise ValueError(f"Unknown LLM provider: {provider!r}")


def _invoke_fallback(ai_cfg: dict, system_prompt: str, human_prompt: str):
    """Invoke the fallback provider (always OpenAI-compatible). Returns (llm_rec, provider_name, model)."""
    rec = _invoke_openai_compatible(ai_cfg, system_prompt, human_prompt, primary=False)
    return rec, ai_cfg.get("fallback_provider", "openai"), ai_cfg.get("fallback_model", "gpt-4o")


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _invoke_openai_compatible(ai_cfg: dict, system_prompt: str, human_prompt: str, primary: bool = True):
    """
    Invoke any OpenAI-API-compatible endpoint.
    When primary=True: uses ai.model, ai.api_key / ai.api_key_env, ai.base_url.
    When primary=False (fallback): uses ai.fallback_model, ai.fallback_api_key_env, no base_url override.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    if primary:
        api_key = _resolve_api_key(ai_cfg, key_field="api_key", env_field="api_key_env")
        model   = ai_cfg.get("model", "gpt-4o")
        base_url = ai_cfg.get("base_url", "") or None
    else:
        api_key = _resolve_api_key(ai_cfg, key_field="fallback_api_key", env_field="fallback_api_key_env")
        model   = ai_cfg.get("fallback_model", "gpt-4o")
        base_url = None   # fallback always goes to standard OpenAI

    if not api_key:
        label = "primary api_key" if primary else "fallback OPENAI_API_KEY"
        raise ValueError(f"No API key found for OpenAI-compatible provider ({label} not set)")

    init_kwargs = dict(model=model, api_key=api_key, max_tokens=2048, temperature=0.1)
    if base_url:
        init_kwargs["base_url"] = base_url
        log.debug("[LLM] Using custom base_url: %s", base_url)

    llm = ChatOpenAI(**init_kwargs)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
    response = llm.invoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_llm_response(content)


def _invoke_anthropic(ai_cfg: dict, system_prompt: str, human_prompt: str):
    """Invoke Claude via LangChain's ChatAnthropic."""
    from langchain_anthropic import ChatAnthropic

    api_key = _resolve_api_key(ai_cfg, key_field="api_key", env_field="api_key_env")
    if not api_key:
        raise ValueError("No API key found for Anthropic (ai.api_key or ANTHROPIC_API_KEY not set)")

    model = ai_cfg.get("model", "claude-sonnet-4-5")
    llm = ChatAnthropic(model=model, api_key=api_key, max_tokens=1024, temperature=0.1)
    messages = [{"role": "user", "content": f"{system_prompt}\n\n{human_prompt}"}]
    response = llm.invoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_llm_response(content)


def _invoke_ollama(ai_cfg: dict, system_prompt: str, human_prompt: str):
    """Invoke a local Ollama model via LangChain's ChatOllama."""
    from langchain_community.chat_models import ChatOllama
    from langchain_core.messages import SystemMessage, HumanMessage

    base_url = ai_cfg.get("ollama_url", "http://localhost:11434")
    model    = ai_cfg.get("model", "llama3")
    llm = ChatOllama(model=model, base_url=base_url, temperature=0.1)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
    response = llm.invoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_llm_response(content)


def _parse_llm_response(content: str):
    """Parse and validate the LLM JSON response into LLMRecommendation."""
    from ai.schemas.recommendation import LLMRecommendation

    log.debug("[LLM] Raw response (%d chars): %s", len(content), content[:500])

    # Strip markdown fences if present
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    # Find outermost JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1

    if start == -1:
        raise ValueError(f"No JSON object found in LLM response: {text[:300]}")

    if end <= start:
        # Response was truncated — log and raise a clear error
        log.warning("[LLM] Response appears truncated (no closing brace). Content: %s", text[:500])
        raise ValueError(f"LLM response truncated (no closing brace). Got: {text[:300]}")

    json_str = text[start:end]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as je:
        log.warning("[LLM] JSON decode error: %s — content: %s", je, json_str[:400])
        raise ValueError(f"LLM returned invalid JSON: {je}") from je

    # Validate and construct
    return LLMRecommendation(
        market_view=data.get("market_view", "neutral"),
        confidence=int(data.get("confidence", 50)),
        selected_strategy_type=data.get("selected_strategy_type", ""),
        reasoning=data.get("reasoning", []),
        risks=data.get("risks", []),
        conflicts=data.get("conflicts", []),
        no_trade_recommended=bool(data.get("no_trade_recommended", False)),
        no_trade_reason=data.get("no_trade_reason", ""),
        dashboard_summary=data.get("dashboard_summary", ""),
    )
