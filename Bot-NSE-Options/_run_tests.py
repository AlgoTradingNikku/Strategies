"""
Quick offline unit tests for the AI Strategy Platform.
Run: python _run_tests.py  (from Bot-NSE-Options directory)
"""
import sys
import json
sys.path.insert(0, '.')

PASS = 0
FAIL = 0

def ok(label):
    global PASS
    PASS += 1
    print(f"  PASS: {label}")

def fail(label, exc):
    global FAIL
    FAIL += 1
    print(f"  FAIL: {label}: {exc}")

print("=== 1. Settings Service ===")
try:
    from ai.workflow.settings_service import get_settings_service
    svc = get_settings_service()
    settings = svc.get_settings()
    assert settings.risk_auto_execution == False, "auto_execution must be locked off"
    ok("auto_execution is locked off")
    d = svc.get_all_as_dict()
    assert len(d) == 28, f"Expected 28 toggles, got {len(d)}"
    ok(f"28 toggles loaded (got {len(d)})")
    # Test toggle update round-trip
    original = d.get("risk_paper_trade", True)
    updated = svc.update_toggle("risk_paper_trade", not original)
    assert updated.risk_paper_trade == (not original)
    ok("toggle update round-trip works")
    # Restore
    svc.update_toggle("risk_paper_trade", original)
    ok("toggle restore works")
except Exception as e:
    fail("settings service", e)

print()
print("=== 2. Persistence DB ===")
try:
    from persistence.recommendation_db import init_db, get_recent_recommendations
    init_db()
    rows = get_recent_recommendations(5)
    ok(f"get_recent_recommendations returned {len(rows)} rows")
except Exception as e:
    fail("recommendation db", e)

try:
    from persistence.settings_db import set_value, get_all
    set_value("_test_key", "hello")
    all_vals = get_all()
    assert all_vals.get("_test_key") == "hello", f"Expected 'hello', got {all_vals.get('_test_key')!r}"
    ok("settings_db set_value/get_all round-trip")
except Exception as e:
    print(f"  FAIL: settings_db: {e}")
    FAIL += 1

print()
print("=== 3. Prompt Builder ===")
try:
    from ai.prompts.analysis_prompt import build_analysis_prompt, PROMPT_VERSION
    state = {"underlying": "NIFTY", "analysis_id": "test-abc", "scored_candidates": []}
    prompt = build_analysis_prompt(state)
    assert "NIFTY" in prompt
    assert "JSON" in prompt
    assert len(prompt) > 200
    ok(f"prompt built ({len(prompt)} chars, version={PROMPT_VERSION})")
except Exception as e:
    fail("prompt builder", e)

print()
print("=== 4. LLM Response Parser ===")
try:
    from ai.workflow.llm_node import _parse_llm_response
    sample = json.dumps({
        "market_view": "bearish",
        "confidence": 72,
        "selected_strategy_type": "bear_put_spread",
        "reasoning": ["Strong bearish trend", "IV low so buying options is favoured"],
        "risks": ["Gap-up gap risk on overnight positions"],
        "conflicts": [],
        "no_trade_recommended": False,
        "no_trade_reason": "",
        "dashboard_summary": "Bearish — bear_put_spread selected (score 65)"
    })
    rec = _parse_llm_response(sample)
    assert rec.market_view == "bearish"
    assert rec.confidence == 72
    assert rec.selected_strategy_type == "bear_put_spread"
    assert len(rec.reasoning) >= 1
    ok(f"valid JSON parsed: view={rec.market_view}, confidence={rec.confidence}")
except Exception as e:
    fail("llm response parser", e)

try:
    # Test markdown fence stripping
    from ai.workflow.llm_node import _parse_llm_response
    fenced = '```json\n{"market_view":"neutral","confidence":50,"selected_strategy_type":"iron_condor","reasoning":["r1"],"risks":["r1"],"conflicts":[],"no_trade_recommended":false,"no_trade_reason":"","dashboard_summary":"ok"}\n```'
    rec2 = _parse_llm_response(fenced)
    assert rec2.market_view == "neutral"
    ok("markdown fence stripping works")
except Exception as e:
    fail("markdown fence stripping", e)

try:
    # Test truncated response detection
    from ai.workflow.llm_node import _parse_llm_response
    truncated = '{"market_view": "bullish", "confidence": 80, "reasoning": ["lots of text'
    try:
        _parse_llm_response(truncated)
        fail("truncated — should have raised", "no exception raised")
    except ValueError as ve:
        ok(f"truncated response raises ValueError: {str(ve)[:60]}")
except Exception as e:
    fail("truncated response detection", e)

print()
print("=== 5. Schema Validation ===")
try:
    from ai.schemas.regime import MarketRegime, TimeframeAlignment
    r = MarketRegime(
        regime="STRONG_BEARISH",
        trend_direction="BEARISH",
        trend_strength=80,
        timeframe_alignment=TimeframeAlignment(),
        vol_regime="ELEVATED",
        iv_regime="HIGH",
        no_trade=False,
        no_trade_reason=None,
        regime_confidence=75,
    )
    d = r.model_dump()
    assert d["regime"] == "STRONG_BEARISH"
    ok("MarketRegime schema validates and serialises")
except Exception as e:
    fail("MarketRegime schema", e)

try:
    from ai.schemas.settings import PlatformSettings
    ps = PlatformSettings()
    assert hasattr(ps, "risk_paper_trade")
    assert hasattr(ps, "risk_kill_switch")
    d = ps.model_dump()
    assert len(d) == 28
    ok(f"PlatformSettings has 28 fields")
except Exception as e:
    fail("PlatformSettings schema", e)

print()
print("=== 6. Graph Build (LangGraph) ===")
try:
    from ai.workflow.graph import build_graph, create_initial_state
    graph = build_graph()
    ok("LangGraph compiled successfully")
    state = create_initial_state("NIFTY", {"symbol": "NIFTY", "exchange": "NSE_INDEX", "lot_size": 75}, "test")
    assert state["underlying"] == "NIFTY"
    assert "analysis_id" in state
    assert len(state["analysis_id"]) == 36  # UUID
    ok(f"initial state created, analysis_id={state['analysis_id'][:8]}...")
except Exception as e:
    fail("LangGraph build", e)

print()
print("=" * 50)
total = PASS + FAIL
print(f"Results: {PASS}/{total} passed, {FAIL} failed")
if FAIL > 0:
    print("SOME TESTS FAILED")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
