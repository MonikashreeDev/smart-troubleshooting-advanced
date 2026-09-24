#!/usr/bin/env python3
"""Run the full offline suite and write TEST_RESULTS.md: every test, what it verifies, pass/fail."""
import io, os, sys, unittest
from datetime import datetime, timezone
from pathlib import Path
os.environ.setdefault('SGT_DB', ':memory:')
ROOT = Path(__file__).parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))

D = {
 # original engine
 'test_health_and_missing_assets': 'Health endpoint is ok and the engine reports synthetic demo mode honestly',
 'test_supported_contract': 'A supported complaint returns exactly the Appendix A contract with a plan',
 'test_exact_cache': 'Repeating a complaint is served from the exact cache',
 'test_semantic_cache_guard': 'Semantic cache reuses only when intent matches; a camera complaint never reuses a performance plan',
 'test_abstention': 'Out-of-scope complaint returns no plan (safe abstention)',
 'test_deeplink_exact_catalog': 'Every deeplink returned is an exact catalog entry',
 'test_determinism_core': 'Same complaint gives the same plan every time',
 'test_no_web_urls': 'No web URLs ever appear in a plan',
 # AI layer
 'test_tanglish_understood_then_validated': 'Tanglish complaint understood by the AI layer, then validated by code',
 'test_rules_alone_cannot_parse_it': 'Without AI the same Tanglish complaint safely abstains',
 'test_off_vocabulary_label_rejected': 'AI label outside the fixed vocabulary is rejected',
 'test_ai_down_falls_back_to_rules': 'AI outage falls back to the rules path and still gives the right plan',
 'test_low_confidence_ignored': 'Low-confidence AI output is ignored',
 'test_out_of_scope_abstains': 'AI says out of scope -> no plan',
 'test_ai_never_writes_steps': 'Text injected via AI output never reaches the plan',
 # O1
 'test_vague_complaint_gets_one_question': 'O1: vague complaint gets exactly one question from the fixed set',
 'test_answer_sharpens_to_validated_plan': 'O1: the answer produces the right validated plan',
 'test_display_ambiguity_asks_display_question': 'O1: "screen acting up" asks the screen question; answer gives the motion-smoothness plan',
 'test_llm_picks_question_from_fixed_set': 'O1: AI-chosen question is used when it is in the fixed set',
 'test_llm_invented_question_rejected': 'O1: AI inventing a question id is rejected; rules pick instead',
 'test_option_outside_question_rejected': 'O1: an answer option that was not offered is refused',
 'test_only_one_question_then_fail_closed': 'O1: a still-vague answer ends with no plan, never a second question',
 'test_clear_complaint_skips_question': 'O1: a clear complaint goes straight to a plan',
 'test_confident_out_of_scope_no_question': 'O1: AI says out of scope -> no question asked',
 'test_non_phone_vague_text_not_asked': 'O1: non-phone vague text ("My toaster has a problem") is not asked about',
 'test_on_device_mode_makes_no_ai_calls': 'O1 + hybrid: on-device mode makes zero AI calls',
 # O2
 'test_still_stuck_never_repeats_a_fix': 'O2: walking the whole ladder never shows a fix twice',
 'test_escalation_is_safe_before_destructive': 'O2: risk order never goes backwards; reset comes last',
 'test_every_escalation_plan_passes_validators': 'O2: every follow-up plan passes validators and uses a catalog screen',
 'test_ladder_exhausted_escalates_to_service_report': 'O2: after the last fix, a service-centre report lists every fix tried',
 'test_fixed_closes_loop_and_records_verified_fix': 'O2: "fixed" closes the session and stores the confirmed fix',
 'test_free_text_still_stuck_and_fixed': 'O2: typed "still not working" / "fixed now" work like the buttons',
 'test_session_persists_in_sqlite': 'O2: session state and history persist in SQLite',
 'test_still_stuck_without_plan_rejected': 'O2: cannot escalate before a plan exists',
 'test_unknown_session_rejected': 'O2: unknown session id is refused',
 'test_on_device_mode_persists_across_turns': 'O2 + hybrid: on-device mode stays on for the whole session',
 'test_bad_catalog_binding_fails_closed': 'O5: a fix pointing at a screen not in the catalog gives no plan',
 'test_unsupported_step_fails_closed': 'O5: a step not backed by the source text gives no plan',
 # O3
 'test_healthy_device_no_warnings': 'O3: healthy phone raises no warning',
 'test_battery_degradation_warns_with_validated_plan': 'O3: battery-degrading phone warns with a validated battery plan',
 'test_storage_forecast_and_crash_spike': 'O3: storage-full forecast (< 30 days) and crash spike both caught with the right plans',
 'test_demo_data_is_labelled_synthetic': 'O3: demo telemetry is labelled synthetic everywhere',
 'test_slope_math': 'O3: least-squares trend is correct',
 'test_z_score_detects_step_change': 'O3: a sudden jump is detected and flagged on the right days',
 'test_noise_alone_does_not_warn': 'O3: normal noise raises no false alarm',
 'test_insufficient_history_no_warning': 'O3: too little history gives no warning',
 'test_ingest_validation': 'O3: unknown metric / negative value refused; valid reading stored',
 'test_unknown_device_rejected': 'O3: unknown device refused',
 'test_plan_withheld_when_evidence_fails_validation': 'O3 + O5: a preventive plan that fails validation is withheld',
 # O4
 'test_no_feedback_keeps_authored_order': 'O4: no feedback keeps the original safe order',
 'test_verified_fixes_boost_within_risk_class': 'O4: confirmed fixes move up within their risk class',
 'test_not_fixed_demotes_within_risk_class': 'O4: failing fixes move down within their risk class',
 'test_feedback_never_lifts_destructive_fix': 'O4 + O5: even 50 votes cannot lift "Reset all settings" above safe fixes',
 'test_primary_plan_stays_first': 'O4: the evidence-matched fix always comes first',
 'test_unknown_source_and_bad_outcome_rejected': 'O4: fake fix ids and bad answers refused',
 'test_plan_carries_verified_fix_count': 'O4: plans show their verified-fix count',
 'test_session_escalation_follows_boosted_order': 'O4: "still stuck" uses the feedback-boosted order',
 'test_metrics_dashboard_shows_feedback': 'O4: the metrics dashboard records the confirmed fix',
 'test_feedback_does_not_change_plan_content': 'O4 + O5: feedback never changes the steps of a plan',
}
GROUP = {'test_engine': 'Core engine (original)', 'test_ai_layer': 'AI layer (original)', 'test_dialogue': 'O1 multi-turn diagnosis + O2 session memory',
         'test_predictive': 'O3 predictive maintenance', 'test_feedback': 'O4 feedback-driven ranking'}

class R(unittest.TextTestResult):
    def __init__(s, *a, **k): super().__init__(*a, **k); s.rows = []
    def addSuccess(s, t): super().addSuccess(t); s.rows.append((t, 'PASS'))
    def addFailure(s, t, e): super().addFailure(t, e); s.rows.append((t, 'FAIL'))
    def addError(s, t, e): super().addError(t, e); s.rows.append((t, 'ERROR'))

suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
res = unittest.TextTestRunner(stream=io.StringIO(), resultclass=R).run(suite)
passed = sum(1 for _, st in res.rows if st == 'PASS')
out = [f'# Test results', '', f'Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} by `python3 scripts/test_report.py` (offline; the AI layer is tested with a fake client).', '',
       f'**{passed}/{len(res.rows)} passed**', '']
by = {}
for t, st in res.rows: by.setdefault(t.__class__.__module__, []).append((t._testMethodName, st))
for mod in ['test_engine', 'test_ai_layer', 'test_dialogue', 'test_predictive', 'test_feedback'] + [m for m in by if m not in GROUP]:
    if mod not in by: continue
    rows = sorted(by[mod])
    out += [f'## {GROUP.get(mod, mod)} ({sum(s == "PASS" for _, s in rows)}/{len(rows)})', '', '| Test | What it verifies | Result |', '|---|---|---|']
    out += [f'| `{n}` | {D.get(n, "-")} | {s} |' for n, s in rows] + ['']
(ROOT / 'TEST_RESULTS.md').write_text('\n'.join(out))
print(f'{passed}/{len(res.rows)} passed')
sys.exit(0 if passed == len(res.rows) else 1)
