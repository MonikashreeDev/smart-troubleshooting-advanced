"""Predictive maintenance (O3): device-health telemetry -> anomaly detection ->
proactive warning -> preventive plan compiled by the SAME verified pipeline.

Telemetry here is SYNTHETIC demo data generated in code (seeded, reproducible).
The real integration point would be SmartThings / device diagnostics; that is
documented as a roadmap item and NOT built.

Detection is plain statistics, no ML framework:
  * baseline window (first 21 days) mean/std vs. recent window (last 7 days) -> z-score
  * least-squares slope over the last 14 days -> trend and days-to-threshold forecast
  * per-day flags where a value is > baseline mean + 3 std
"""
from __future__ import annotations
import math, random
from statistics import mean, pstdev

METRICS = {
    'battery_drain_pct_per_hr': {'label': 'Battery drain (%/hour)', 'evidence': 'demo-battery-1', 'symptom': 'drain'},
    'storage_used_pct': {'label': 'Storage used (%)', 'evidence': 'pm-storage', 'symptom': 'storage'},
    'app_crashes_per_day': {'label': 'App crashes per day', 'evidence': 'fu-safe-mode', 'symptom': 'slow'},
}
BASE_DAYS, RECENT_DAYS, TREND_DAYS, DAYS = 21, 7, 14, 30
Z_WARN = 3.0
STORAGE_LIMIT = 95.0

DEVICES = {
    'demo-galaxy-healthy': 'Galaxy phone A (synthetic: healthy)',
    'demo-galaxy-battery': 'Galaxy phone B (synthetic: battery degrading)',
    'demo-galaxy-storage': 'Galaxy phone C (synthetic: storage filling, crashes rising)',
}

def synthetic_series(device_id):
    """Deterministic synthetic telemetry for the three demo devices."""
    rnd = random.Random(device_id)
    out = {m: [] for m in METRICS}
    for day in range(1, DAYS + 1):
        late = max(0, day - (DAYS - RECENT_DAYS))  # 0 for the first 23 days, then 1..7
        drain = 4.0 + rnd.gauss(0, 0.25)
        storage = 61.0 + 0.05 * day + rnd.gauss(0, 0.2)
        crashes = max(0, round(rnd.gauss(0.4, 0.5)))
        if device_id == 'demo-galaxy-battery':
            drain += 0.55 * late
        if device_id == 'demo-galaxy-storage':
            storage = 76.0 + 0.55 * day + rnd.gauss(0, 0.2)
            crashes = max(0, round(rnd.gauss(0.4 + 0.6 * late, 0.5)))
        out['battery_drain_pct_per_hr'].append((day, round(drain, 2)))
        out['storage_used_pct'].append((day, round(min(100.0, storage), 2)))
        out['app_crashes_per_day'].append((day, float(crashes)))
    return out

def seed(store):
    rows = []
    for dev in DEVICES:
        for m, pts in synthetic_series(dev).items():
            rows += [(dev, d, m, v, 1) for d, v in pts]
    store.put_telemetry_many(rows)

def slope(points):
    """Least-squares slope (units per day)."""
    n = len(points)
    if n < 2: return 0.0
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    mx, my = mean(xs), mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den

def analyse(points, metric):
    vals = [v for _, v in points]
    if len(vals) < BASE_DAYS + 2:
        return {'metric': metric, 'status': 'insufficient_data', 'points': points}
    base = vals[:BASE_DAYS]; recent = vals[-RECENT_DAYS:]
    bm, bs = mean(base), pstdev(base)
    floor = {'app_crashes_per_day': 0.5, 'storage_used_pct': 0.5}.get(metric, 0.1)
    z = (mean(recent) - bm) / max(bs, floor)
    sl = slope(points[-TREND_DAYS:])
    flags = [d for d, v in points if v > bm + 3 * max(bs, floor)]
    res = {'metric': metric, 'label': METRICS[metric]['label'], 'points': points,
           'baseline_mean': round(bm, 3), 'baseline_std': round(bs, 3), 'recent_mean': round(mean(recent), 3),
           'z_score': round(z, 2), 'slope_per_day': round(sl, 3), 'anomaly_days': flags, 'latest': vals[-1]}
    if metric == 'storage_used_pct':
        res['days_to_full'] = None if sl <= 0 else max(0, round((STORAGE_LIMIT - vals[-1]) / sl, 1))
    return res

def warning_for(a):
    """Decide whether an analysed metric deserves a proactive warning. Returns dict or None."""
    m = a['metric']
    if a.get('status') == 'insufficient_data': return None
    if m == 'battery_drain_pct_per_hr' and a['z_score'] >= Z_WARN and a['recent_mean'] >= 1.2 * a['baseline_mean']:
        pct = round(100 * (a['recent_mean'] / a['baseline_mean'] - 1))
        return {'severity': 'critical' if pct >= 50 else 'warning',
                'message': f"Battery drain is up {pct}% over the last {RECENT_DAYS} days (z = {a['z_score']}). Likely an app or setting is draining power before the battery degrades further."}
    if m == 'storage_used_pct':
        dtf = a.get('days_to_full')
        if a['latest'] >= 90 or (dtf is not None and dtf <= 30):
            return {'severity': 'critical' if (dtf is not None and dtf <= 7) or a['latest'] >= STORAGE_LIMIT else 'warning',
                    'message': f"Storage is {a['latest']}% full and growing {a['slope_per_day']}%/day; forecast to reach {STORAGE_LIMIT:.0f}% in about {dtf} days."}
    if m == 'app_crashes_per_day' and a['z_score'] >= Z_WARN and a['recent_mean'] >= 1.5:
        return {'severity': 'warning',
                'message': f"App crashes rose to {a['recent_mean']:.1f}/day from {a['baseline_mean']:.1f}/day (z = {a['z_score']}). A recently installed app may be misbehaving."}
    return None

class Predictor:
    def __init__(self, engine):
        self.e = engine; self.store = engine.store
        if not self.store.devices(): seed(self.store)

    def devices(self):
        known = self.store.devices()
        return [{'id': d, 'name': DEVICES.get(d, d), 'synthetic': d in DEVICES} for d in known]

    def ingest(self, device_id, day, metric, value):
        if metric not in METRICS: raise ValueError('unknown_metric')
        if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 64: raise ValueError('bad_device_id')
        v = float(value)
        if not math.isfinite(v) or v < 0: raise ValueError('bad_value')
        self.store.put_telemetry(device_id.strip(), int(day), metric, v, synthetic=False)
        return {'ok': True}

    def predict(self, device_id):
        series = self.store.telemetry(device_id)
        if not series: raise ValueError('unknown_device')
        by_id = {d['id']: d for d in self.e.siis + self.e.followups}
        analyses, warnings = {}, []
        for m in METRICS:
            if m not in series: continue
            a = analyse(series[m], m); analyses[m] = a
            w = warning_for(a)
            if not w: continue
            ev = by_id.get(METRICS[m]['evidence'])
            intent = {'domain': ev['domain'] if ev else 'Unknown', 'trigger': 'none', 'feature': m, 'symptom': METRICS[m]['symptom']}
            plan = self.e.compile_evidence(f'Predictive warning: {a["label"]}', ev, intent, purpose='predictive',
                                           extra_meta={'telemetry_evidence': {k: a[k] for k in ('z_score', 'slope_per_day', 'baseline_mean', 'recent_mean') if k in a},
                                                       'synthetic_telemetry': device_id in DEVICES}) if ev else None
            ok = bool(plan and plan['response']['contexts'])
            warnings.append({**w, 'metric': m, 'label': a['label'], 'plan': plan if ok else None,
                             'plan_status': 'validated' if ok else 'withheld_failed_validation'})
        return {'device': {'id': device_id, 'name': DEVICES.get(device_id, device_id)},
                'synthetic_data': device_id in DEVICES,
                'data_note': 'Synthetic demo telemetry generated in code (seeded). Real source would be SmartThings/device diagnostics (roadmap, not built).' if device_id in DEVICES else 'Telemetry ingested through /v1/telemetry.',
                'method': {'baseline_days': BASE_DAYS, 'recent_days': RECENT_DAYS, 'trend_days': TREND_DAYS, 'z_warn': Z_WARN},
                'metrics': analyses, 'warnings': warnings}
