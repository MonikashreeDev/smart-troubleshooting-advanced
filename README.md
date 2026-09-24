# Smart Guided Troubleshooting Engine - Advanced (Galaxy Care)

Samsung PRISM Theme 02 - team ECLIPSE (VIT Vellore). Advanced edition of
[smart-guided-troubleshooting](https://github.com/MonikashreeDev/smart-guided-troubleshooting):
the engine now runs the whole troubleshooting journey, not just single complaints.

**AI proposes, code disposes.** Every plan, on every path (single complaint, follow-up question,
"still stuck" escalation, predictive warning), is compiled from source evidence and passes the same
deterministic validators. If validation fails, the engine shows nothing rather than guessing.

## What's new in the advanced version

| # | Capability | What it does |
|---|---|---|
| O1 | Guided multi-turn diagnosis | Vague complaint -> ONE clarifying question with fixed answer options. Llama may only pick which question id from a fixed set; code rejects anything else. The answer sharpens the plan. A still-vague answer fails closed (no second question). |
| O2 | Session memory and continuity | Server-side sessions (SQLite) remember every fix shown. "Still stuck" escalates to the next fix, never repeats, always safe -> manual -> critical. Ladder exhausted -> service-centre report. "Fixed" closes the loop. |
| O3 | Predictive maintenance | Device-health telemetry (battery drain, storage growth, crash frequency) -> statistical anomaly detection (z-score vs baseline, least-squares trend, days-to-full forecast) -> proactive warning + preventive plan through the verified pipeline. |
| O4 | Feedback-driven ranking | "Did this fix it?" on every plan. Verified-fix counts are stored and reorder fixes within the same risk class. Visible on the Insights dashboard. |
| O5 | Verified execution | Same validators on every new path; bad catalog bindings, unsupported steps and failed preventive plans are withheld. Feedback can never lift a destructive fix above safe ones. |
| UI | One UI-styled PWA | Samsung One UI look (large viewing area, rounded cards, bottom tabs, light/dark), installable, offline app shell. |
| Hybrid | Galaxy AI hybrid pattern | "On-device mode" toggle: cloud AI off, rules + n-gram retrieval run locally, same validators. |

## Tech stack

**Samsung ecosystem layer**
- One UI design language - the web app is restyled to look One UI native. *Built.*
- Galaxy AI hybrid pattern - cloud AI when available, on-device rules path when not, with a visible "On-device mode" toggle. *Built.*
- SmartThings - named real source for device telemetry in predictive maintenance. *Roadmap, not built* (no devices/account).
- Knox - enterprise fleet diagnostics and secure telemetry. *Roadmap.*
- Bixby capsule - voice entry into the same pipeline. *Roadmap.* (Masked deeplinks already use the `bixby://` scheme from the problem statement.)
- Tizen - TV / wearable clients. *Roadmap.*

**Intelligence layer**
- Llama 3.3 70B Instruct fp8-fast (Cloudflare Workers AI, free tier) - complaint understanding (closed vocabulary) and clarifying-question choice (fixed id set).
- BAAI bge-small-en-v1.5 embeddings + BM25 - hybrid retrieval and semantic cache.
- Time-series anomaly detection - z-score vs 21-day baseline, 14-day least-squares trend, days-to-threshold forecast. Plain Python statistics, no ML framework.

**Verification layer**
- Deterministic validator - schema, exact catalog URI, source support for every step, safe ordering, no URLs.
- Semantic cache - bge cosine >= 0.90 plus domain/symptom/trigger/feature agreement.

**Data layer**
- SQLite (standard library) - sessions, session events, feedback events, telemetry history. PostgreSQL is the migration path.

**Experience layer**
- One UI-styled PWA - manifest, service worker app shell, installable on Galaxy phones.

## Honest data note

- The problem statement names Samsung's starter assets (`queries.json`, `siis_responses.json`, `deeplinks.json`, `samples/`, `schema.py`) but does not include them. Everything under `data/demo_*.json` is **synthetic demo data** written for this prototype, including the new follow-up fixes (`demo_siis_followups.json`) and their masked deeplinks.
- Device telemetry for predictive maintenance is **synthetic**, generated in code with a fixed seed (`engine/predictive.py`), and every API response and screen says so.
- Feedback starts empty; nothing is pre-seeded.
- The benchmark set (`data/eval_messy.json`, 33 cases) is small and synthetic.

## Run

```bash
python3 app.py            # http://127.0.0.1:8000 - no third-party packages
```

Cloud AI (optional, Cloudflare Workers AI free tier, no billing):

```bash
export CF_ACCOUNT_ID=<account id>
export CF_API_TOKEN=<token with Workers AI permission>
python3 app.py
```

`SGT_DB` sets the SQLite path (default `/tmp/sgt_advanced.sqlite3`). `SGT_AI=off` forces the rules path.

Demo deep links: `/?q=My phone has a problem&answer=drain`, `/?q=My phone became slow after an update&stuck=2`, `/?offline=1&q=...`, `/#health`, `/#insights`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Status, AI models, feature flags |
| POST | `/v1/troubleshoot` | `{query, siis_response?, offline?}` single-shot plan (Appendix B envelope, Appendix A core) |
| POST | `/v1/session` | `{message, offline?}` start a guided session -> `question` / `plan` / `no_match` |
| POST | `/v1/session/{id}/turn` | `{type: message\|answer\|still_stuck\|fixed, text?, option?, offline?}` |
| GET | `/v1/session/{id}` | Session state and event history |
| POST | `/v1/feedback` | `{source_id, outcome: fixed\|not_fixed}` |
| GET | `/v1/devices` | Telemetry devices (synthetic demo devices flagged) |
| GET | `/v1/predict?device=` | Metrics analysis, warnings and preventive plans |
| POST | `/v1/telemetry` | `{device_id, day, metric, value}` ingest a reading |
| GET | `/v1/metrics` | Cache, latency, AI, sessions, on-device requests, feedback board, benchmark |

## Tests

```bash
python3 -m unittest discover -s tests -v   # 59 offline tests
python3 scripts/test_report.py             # writes TEST_RESULTS.md (every test, what it verifies, pass/fail)
python3 scripts/evaluate_ai.py             # benchmark; AI configs need CF_ACCOUNT_ID + CF_API_TOKEN
```

See [TEST_RESULTS.md](TEST_RESULTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Benchmark (messy complaints, 33 synthetic cases)

| Configuration | Accuracy | Wrong plans |
|---|---|---|
| Rules + hashed n-grams (also the on-device mode path) | 75.8% (25/33) | 0 |
| + bge-small embeddings | 87.9% (29/33) | 0 |
| + Llama 3.3 70B understanding | 100% (33/33) | 0 |

## Limitations

- Synthetic demo data throughout (see above); swap in the official assets via `data/official/`.
- SQLite lives on the free Render instance's temporary disk: sessions, feedback and ingested telemetry reset on restart or redeploy (demo telemetry is re-seeded automatically).
- The free Render instance sleeps when idle; the first request after a pause can take ~30-60 s.
- Workers AI free tier has a daily limit; past it the engine falls back to the rules path.
- One clarifying question set is small (area, screen behaviour) because it maps to the demo catalog.
- The Llama question picker and understanding are tested offline with a fake client; live behaviour can vary slightly, which the fixed vocabulary and validators contain.

## AI usage

Runtime models are open-source and served on Cloudflare Workers AI's free tier. The LLM only returns labels or a question id from fixed lists; steps and deeplinks come from evidence and the catalog. Development was AI-assisted (code generation, documentation drafting), reviewed and tested by the team.
