# Architecture

```text
                    +--------------------- One UI PWA (web/) ----------------------+
                    |  Diagnose (chat) | Device health | Insights | On-device toggle |
                    +-------------------------------+-------------------------------+
                                                    | REST (app.py, stdlib HTTP)
      +---------------------------+-----------------+----------------+------------------------+
      |                           |                                  |                        |
  Dialogue (O1/O2)           TroubleshootingEngine              Predictor (O3)          Feedback (O4)
  engine/dialogue.py         engine/pipeline.py                 engine/predictive.py    pipeline.record_feedback
  - one fixed question       - understand (Llama | rules)       - telemetry -> z-score, - fixed / not_fixed
  - session state            - retrieve (BM25 + bge | n-gram)     trend, forecast        - verified-fix score
  - still stuck ladder       - compile + VALIDATE               - warning -> evidence   - ladder reorder within
  - fixed -> feedback        - compile_evidence + VALIDATE        -> compile_evidence      risk class only
      |                           |                                  |                        |
      +---------------------------+------------- SQLite (engine/store.py) -------------------+
                                     sessions | session_events | feedback | telemetry
```

## Pipeline (single complaint)

```text
Complaint
  -> mode: cloud (Galaxy AI hybrid) or on-device (toggle)
  -> cloud: Llama 3.3 70B -> closed-vocabulary labels -> code-built canonical complaint
     (fallback to rules if AI off, unsure, off-vocabulary, down, or on-device mode)
  -> L1 exact cache (key includes mode) -> L2 semantic cache (bge cosine >= 0.90 + intent equality, same mode)
  -> hybrid SIIS retrieval -> evidence-bound compile -> deeplink metadata retrieval
  -> validators: schema, catalog URI, source support, safe ordering, no URLs
  -> plan with proof (source span, catalog id, screen, risk, scores, validator result, verified-fix count) | abstain
```

## O1 Guided multi-turn diagnosis

- Trigger: AI flags the complaint as a vague phone problem, AI confidence is low, rules find a phone-related complaint with no domain, or the Display domain is detected without a clear symptom.
- Question bank (`QUESTIONS` in `engine/dialogue.py`): `q_area` (5 options, one per supported symptom) and `q_display` (flicker vs swipe). Text and options are fixed.
- In cloud mode Llama picks the question id (`WorkersAI.choose_question`); any id not in the set is discarded and the rules choice is used. On-device mode never calls the cloud.
- Option answers are validated against the asked question. The chosen symptom becomes a code-built canonical complaint and runs through the normal pipeline. Free-text answers get one more understanding pass; if still vague, the session fails closed.

## O2 Session memory

- Session state is JSON in SQLite; every turn is logged to `session_events`.
- Ladder for a symptom = primary evidence-matched fix, then follow-ups sorted by `(risk class, -verified-fix score, authored order)`.
- "Still stuck" records `not_fixed`, picks the first ladder entry not tried, compiles it with `compile_evidence` (same validators, catalog binding checked in code). No entries left -> `escalate` with a service-centre report.
- "Fixed" records `fixed` and closes the session.

## O3 Predictive maintenance

- Metrics: `battery_drain_pct_per_hr`, `storage_used_pct`, `app_crashes_per_day`. 30 days per device.
- Detection: baseline = first 21 days (mean, std with a floor), recent = last 7 days, z = (recent mean - baseline mean) / std; 14-day least-squares slope; storage days-to-95% forecast; per-day flags above baseline + 3 std.
- Rules: battery z >= 3 and >= 20% above baseline; storage >= 90% or forecast full within 30 days; crashes z >= 3 and >= 1.5/day.
- Each warning maps to an evidence record (battery usage, free up storage, safe-mode check) and is compiled by `compile_evidence`. A plan that fails validation is withheld; the warning still shows.
- Demo telemetry is synthetic and labelled. SmartThings is the documented real source (not built).

## O4 Feedback-driven ranking

- Score = (fixed + 1) / (fixed + not_fixed + 2) - 0.5 (Laplace-smoothed fix rate, 0 when no feedback).
- Used only to reorder follow-up fixes inside one risk class. The primary evidence-matched fix stays first; plan content, retrieval and validators are unaffected.
- `/v1/metrics.feedback` feeds the Insights dashboard.

## O5 Verification guarantees

- One validator (`engine/validators.py`) for every path.
- Follow-up entries bind to catalog ids; unknown id -> `catalog_binding_missing`, no plan.
- Steps must be supported by the source text; unsupported step -> no plan.
- Feedback cannot lift a critical fix above auto/manual fixes.
- The LLM never writes steps, screens, URIs or question wording.

## Samsung ecosystem mapping

| Layer | Status | Where |
|---|---|---|
| One UI design language | Built | `web/styles.css`, `web/index.html` |
| Galaxy AI hybrid (cloud + on-device) | Built | `offline` flag through `understand/_qemb/troubleshoot`, UI toggle |
| SmartThings telemetry | Roadmap | would feed `POST /v1/telemetry` / `Predictor` |
| Knox fleet diagnostics | Roadmap | multi-device `Predictor` + admin view |
| Bixby capsule | Roadmap | voice front end calling `/v1/session` |
| Tizen clients | Roadmap | TV / watch front ends on the same API |

## Failure behaviour

Unsupported domain, weak evidence, weak deeplink, unknown catalog binding or any validator error -> `{"contexts": []}` with a machine-readable reason. Unanswerable clarifications close the session with `no_match`. Exhausted ladders escalate to a human service channel.

## Known limits

See the README "Limitations" section. In short: synthetic data, ephemeral SQLite on the free host, small question bank, and a small synthetic benchmark.
