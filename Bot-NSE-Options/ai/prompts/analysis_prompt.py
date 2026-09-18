"""
ai/prompts/analysis_prompt.py
==============================
Versioned prompt templates for the LLM interpretation node.
PROMPT_VERSION must be incremented when prompt content changes.
The version is stored with every recommendation for reproducibility.
"""
from __future__ import annotations

PROMPT_VERSION = "1.0"

SYSTEM_PROMPT = """You are an expert Indian options market analyst assistant.
You will receive pre-computed quantitative analysis of NIFTY/BANKNIFTY option chain data.

CRITICAL RULES — you MUST follow these without exception:
1. You are an INTERPRETER, not a calculator. All numerical values (LTP, Greeks, payoffs, breakevens) are pre-computed by the Python engine. Accept them as-is.
2. You MUST NOT invent, modify, or recalculate any numbers. Use only the values provided.
3. You MUST NOT recommend a strike price that was not present in the supplied candidate strategies.
4. You MUST NOT label any strategy as "guaranteed profit" or "risk-free".
5. When signals are contradictory or confidence is low, recommend NO_TRADE.
6. Your response MUST be valid JSON matching the exact schema provided.
7. Reasoning must be grounded in the supplied data — no speculation beyond what the data supports.
8. Risk warnings must be specific to the recommended strategy's actual risks.

Your role:
- Interpret the market regime and what it means for options strategy selection
- Compare the pre-scored candidates and explain which best fits the current conditions
- Identify any contradictory signals between OI, technicals, and volatility data
- Highlight the most important risks for the selected strategy
- Produce concise, actionable dashboard language

You should NOT:
- Recalculate payoffs, Greeks, or IV
- Reference strikes not in the candidate list
- Make predictions about future price direction with false certainty
- Ignore NO_TRADE signals from the regime engine"""


def build_analysis_prompt(state: dict) -> str:
    """
    Build the human turn prompt from the AnalysisState.
    Includes only pre-computed data — never raw chain data.
    """
    underlying = state.get("underlying", "NIFTY")
    regime = state.get("regime")
    market_data = state.get("market_data")
    scored = state.get("scored_candidates", [])[:5]  # Top 5 only to control token usage

    lines = [
        f"## Market Analysis Request — {underlying}",
        f"Analysis ID: {state.get('analysis_id', 'unknown')}",
        "",
    ]

    # Market regime
    if regime:
        lines += [
            "## Market Regime (Python-computed, do not recalculate)",
            f"Regime: {regime.regime}",
            f"Trend Direction: {regime.trend_direction}",
            f"Trend Strength: {regime.trend_strength}/100",
            f"Regime Confidence: {regime.regime_confidence}/100",
            f"5m Trend: {regime.timeframe_alignment.tf_5m}",
            f"15m Trend: {regime.timeframe_alignment.tf_15m}",
            f"1h Trend: {regime.timeframe_alignment.tf_1h}",
            f"Day Trend: {regime.timeframe_alignment.tf_day}",
            f"TF Aligned: {regime.timeframe_alignment.aligned} ({regime.timeframe_alignment.aligned_direction})",
            f"Favors Selling: {regime.favors_selling}",
            f"Favors Buying: {regime.favors_buying}",
            f"NO_TRADE Flag: {regime.no_trade}",
            f"NO_TRADE Reason: {regime.no_trade_reason or 'None'}",
            "",
        ]

    # Market data
    if market_data:
        tech = market_data.technicals
        vol = market_data.volatility
        oi = market_data.oi_signals
        lines += [
            "## Technical Data (Python-computed)",
            f"Spot LTP: {tech.spot_ltp}",
            f"VWAP: {tech.vwap}",
            f"EMA20: {tech.ema20}  EMA50: {tech.ema50}",
            f"RSI14: {tech.rsi14}  ATR14: {tech.atr14}",
            f"Price vs VWAP: {tech.price_vs_vwap}",
            f"Price vs EMA20: {tech.price_vs_ema20}",
            "",
            "## Volatility Data (Python-computed)",
            f"India VIX: {vol.india_vix}  VIX Regime: {vol.vix_regime}",
            f"ATM IV: {vol.atm_iv}  IV Regime: {vol.iv_regime}",
            f"IV Rank: {vol.iv_rank}  IV Percentile: {vol.iv_percentile}",
            f"Expected Move: \u00b1{vol.expected_move_abs} pts ({vol.expected_move_pct:.2f}%)",
            "",
            "## OI Analysis (Python-computed)",
            f"PCR: {oi.pcr}  Near-ATM PCR: {oi.near_atm_pcr}  Volume PCR: {oi.pcr_volume}",
            f"Max Call OI Strike: {oi.max_call_oi_strike}  Max Put OI Strike: {oi.max_put_oi_strike}",
            f"OI Trend: {oi.oi_trend}",
            f"Call Writing Zones: {oi.call_writing_zones}",
            f"Put Writing Zones: {oi.put_writing_zones}",
            f"OI Support: {oi.oi_support_levels}  OI Resistance: {oi.oi_resistance_levels}",
            "",
        ]

    # Strategy candidates
    if scored:
        lines.append("## Pre-Scored Strategy Candidates (Python-computed — do not recalculate)")
        for sc in scored:
            c = sc.candidate
            p = c.payoff
            legs_str = ", ".join(
                f"{l.action} {l.strike}{l.option_type}@{l.ltp}" for l in c.legs
            )
            lines += [
                f"### Candidate #{sc.rank}: {c.strategy_type.replace('_', ' ').title()} "
                f"(strategy_type key: \"{c.strategy_type}\") — Score: {sc.score}/100",
                f"  Legs: {legs_str}",
                f"  Credit/Debit: {p.entry_credit}  Max Profit: {p.max_profit}  Max Loss: {p.max_loss}",
                f"  Breakeven(s): {p.breakeven_lower} / {p.breakeven_upper}",
                f"  Risk/Reward: {p.risk_reward_ratio}  Prob Profit: {p.probability_profit:.1%}",
                f"  Net Delta: {p.net_delta}  Net Theta: {p.net_theta}  Net Vega: {p.net_vega}",
                f"  Liquidity Score: {p.liquidity_score}  Regime Aligned: {c.regime_aligned}",
                f"  Score Breakdown: Trend={sc.score_breakdown.trend_alignment} OI={sc.score_breakdown.oi_structure} RR={sc.score_breakdown.risk_reward} IV={sc.score_breakdown.iv_environment}",
                "",
            ]

    lines += [
        "## Your Task",
        "Analyse the above data and return a JSON response.",
        "IMPORTANT: Your ENTIRE response must be a single, complete, valid JSON object.",
        "Do NOT include any text before or after the JSON. Do NOT use markdown code fences.",
        "Do NOT truncate or abbreviate the JSON. Complete ALL fields before ending.",
        "",
        "Required JSON schema (fill every field):",
        '{"market_view": "bullish|bearish|neutral|volatile|no_trade",',
        ' "confidence": <integer 0-100>,',
        ' "selected_strategy_type": "<strategy_type from candidates above, or no_trade>",',
        ' "reasoning": ["<grounded bullet 1>", "<grounded bullet 2>", "<grounded bullet 3>"],',
        ' "risks": ["<specific risk 1>", "<specific risk 2>"],',
        ' "conflicts": ["<conflict if any, else empty list>"],',
        ' "no_trade_recommended": <true|false>,',
        ' "no_trade_reason": "<reason if no_trade_recommended is true, else empty string>",',
        ' "dashboard_summary": "<one concise line for the dashboard>"}',
        "",
        "Rules:",
        "- selected_strategy_type MUST be the exact strategy_type key shown in parentheses above "
        "(lowercase, underscore-separated, e.g. \"bull_call_spread\") — NOT the Title Case display name",
        "- reasoning must have at least 2 bullets grounded in the supplied data",
        "- risks must have at least 1 bullet",
        "- Output ONLY the JSON object, nothing else",
    ]

    return "\n".join(lines)
