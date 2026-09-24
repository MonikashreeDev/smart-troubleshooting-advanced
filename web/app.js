const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = (path, body) => fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {}).then(async r => { const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j; });
let SID = null, AI_ON = false;
const offline = () => $('#offline').checked;

// ---------- tabs ----------
const TITLES = {diagnose:'Diagnose', health:'Device health', insights:'Insights'};
document.querySelectorAll('nav.bottom button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav.bottom button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
  $('#bigTitle').textContent = TITLES[b.dataset.tab];
  if (b.dataset.tab === 'insights') insights();
  if (b.dataset.tab === 'health' && !$('#devices').children.length) devices();
});

// ---------- Galaxy AI hybrid toggle ----------
function modeLine() {
  $('#modeLine').textContent = offline()
    ? 'On-device mode: cloud AI off. Rules + n-gram retrieval run locally, same validators.'
    : (AI_ON ? 'Cloud AI understands (Llama 3.3 70B + bge). Code verifies.' : 'Cloud AI unavailable - running the on-device rules path.');
}
$('#offline').onchange = () => { modeLine(); if (SID) SID = null; bot('Switched to ' + (offline() ? 'on-device mode' : 'cloud mode') + '. Start a new complaint.'); };

// ---------- chat ----------
function add(html, cls) { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; $('#chat').appendChild(d); d.scrollIntoView({behavior:'smooth', block:'end'}); return d; }
const bot = t => add(esc(t), 'bubble bot');
const user = t => add(esc(t), 'bubble user');

function understandingNote(u, mode) {
  if (!u) return '';
  if (mode === 'on_device') return 'On-device rules understood the complaint';
  if (u.used && u.supported) return `AI understood${u.language ? ' (' + esc(u.language) + ')' : ''}: "${esc(u.english)}" -> ${esc(u.symptom)} · ${Math.round((u.confidence||0)*100)}% sure`;
  if (u.reason === 'ai_out_of_scope') return 'AI: outside supported scope';
  return 'Rules path: ' + esc(String(u.reason || '').replace(/_/g, ' '));
}

function planCard(plan, opts = {}) {
  const g = plan.response.contexts[0], a = g.actions[0], sg = a.stepGroups[0], p = plan.meta.proof[0], rk = plan.meta.ranking || {};
  const screen = sg.actionableDeeplink ? `<span class="screen">Opens: ${esc(sg.actionableDeeplink.description)}</span>` : '<span class="screen">Manual step - no screen to open</span>';
  const step = opts.step ? `Fix ${opts.step} of ${opts.size}` : '';
  const fb = opts.session
    ? `<div class="fb"><button class="yes" data-act="fixed">Fixed it</button><button class="no" data-act="still_stuck">Still stuck</button></div>`
    : `<div class="fb"><button class="yes" data-src="${esc(p.source_id)}" data-out="fixed">Did this fix it? Yes</button><button class="no" data-src="${esc(p.source_id)}" data-out="not_fixed">No</button></div>`;
  return `<div class="planTop"><div><b>${esc(g.title)}</b><small>${esc(a.actionName)} · ${esc(a.description)}</small></div><span class="badge ${esc(a.category)}">${esc(a.category.toUpperCase())}</span></div>
    <ol class="steps">${sg.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>${screen}
    <div class="meta">${step ? `<span>${step}</span>` : ''}${plan.meta.mode ? `<span>${plan.meta.mode === 'on_device' ? 'On-device' : 'Cloud'}</span>` : ''}${plan.meta.cache_hit ? `<span>cache: ${esc(plan.meta.cache_type)}</span>` : ''}${plan.meta.purpose ? `<span>${esc(plan.meta.purpose)}</span>` : ''}<span>${rk.verified_fixes || 0} verified fixes</span>${plan.meta.latency_ms ? `<span>${plan.meta.latency_ms} ms</span>` : ''}</div>
    <details class="proof"><summary>Why this plan?</summary><div class="checks"><span class="check">&#10003; schema</span><span class="check">&#10003; catalog screen</span><span class="check">&#10003; source support</span><span class="check">&#10003; safe ordering</span><span class="check">&#10003; no URLs</span></div>
    <div class="note">${understandingNote(plan.meta.understanding, plan.meta.mode)}</div>
    <p class="note"><b>Source:</b> ${esc(p.source_span)}</p><pre>${esc(JSON.stringify(p, null, 2))}</pre></details>${fb}`;
}

// ---------- service-centre handoff: send the report to Samsung ----------
const SAMSUNG_WA = '91180057267864'; // Samsung India support on WhatsApp: 1800-5-726-7864
const SAMSUNG_EMAIL = 'support.in@samsung.com';

function reportText(rep) {
  const L = ['GALAXY CARE - SERVICE REPORT',
    'Smart Guided Troubleshooting (student demo - synthetic data, not an official Samsung app)', '',
    'Complaint: ' + (rep.complaint || '-'),
    'Symptom: ' + (rep.symptom || '-'),
    'Mode: ' + (rep.mode === 'on_device' ? 'on-device rules' : 'cloud AI + validators'), '',
    'Verified fixes already tried:'];
  (rep.fixes_tried && rep.fixes_tried.length ? rep.fixes_tried : ['(none recorded)']).forEach((f, i) => L.push((i + 1) + '. ' + f));
  L.push('', 'Result: every validated fix in the catalog for this symptom was tried without success.',
    'Request: deeper diagnosis at a Samsung service centre.');
  return L.join('\n');
}

function escalateCard(rep) {
  const text = reportText(rep), enc = encodeURIComponent(text);
  const wa = 'https://wa.me/' + SAMSUNG_WA + '?text=' + enc;
  const mail = 'mailto:' + SAMSUNG_EMAIL + '?subject=' + encodeURIComponent('Galaxy service request - troubleshooting report') + '&body=' + enc;
  return `<b>Time for a service centre.</b><p class="note">Every validated fix in the catalog for this problem was tried, so it stops here instead of guessing. Hand this report to Samsung:</p><pre>${esc(JSON.stringify(rep, null, 2))}</pre>
    <div class="handoff"><h4>Send this report to Samsung</h4>
    <div class="handoffBtns">
      <a class="hsBtn primary" href="${wa}" target="_blank" rel="noopener">WhatsApp Samsung India support</a>
      <a class="hsBtn" href="${mail}">Email Samsung support</a>
      <button class="hsBtn" data-dl>Download report (.txt)</button>
      <button class="hsBtn" data-copy>Copy report</button>
    </div>
    <details class="proof"><summary>Official route: Samsung Members app</summary>
    <ol class="steps"><li>Long-press the <b>Samsung Members</b> app icon and tap <b>Error reports</b>.</li>
    <li>Pick the category and describe the issue - paste this report and attach a screenshot.</li>
    <li>Keep <b>Send system log data</b> ticked, then send.</li>
    <li>Samsung's team replies in the app under <b>Check feedback you sent</b>.</li></ol>
    <p class="note">Samsung India WhatsApp support (24/7): 1800-5-726-7864 · ${esc(SAMSUNG_EMAIL)}</p></details>
    <div class="note handoffNote" hidden></div></div>`;
}

function wireHandoff(d, rep) {
  const text = reportText(rep), note = d.querySelector('.handoffNote');
  const flash = t => { note.textContent = t; note.hidden = false; };
  const dl = d.querySelector('[data-dl]');
  if (dl) dl.onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], {type: 'text/plain'}));
    a.download = 'galaxy-care-service-report.txt'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    flash('Report downloaded. Attach it in Samsung Members or send it on WhatsApp / email.');
  };
  const cp = d.querySelector('[data-copy]');
  if (cp) cp.onclick = async () => {
    try { await navigator.clipboard.writeText(text); flash('Copied. Paste it into Samsung Members > Error reports, WhatsApp or email.'); }
    catch { const ta = document.createElement('textarea'); ta.value = text; d.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); flash('Copied. Paste it into Samsung Members > Error reports, WhatsApp or email.'); }
  };
}

function handle(r) {
  SID = r.session_id;
  if (r.type === 'question') {
    const d = add(`${esc(r.question.text)}<div class="opts">${r.question.options.map(o => `<button class="opt" data-opt="${esc(o.id)}">${esc(o.label)}</button>`).join('')}</div><div class="note" style="margin-top:8px">One question, fixed options${r.question.chosen_by === 'llm' ? ' · picked by Llama from a fixed set' : ''}.</div>`, 'bubble bot');
    d.querySelectorAll('[data-opt]').forEach(b => b.onclick = () => { d.querySelectorAll('button').forEach(x => x.disabled = true); user(b.textContent); turn({type:'answer', option:b.dataset.opt}); });
  } else if (r.type === 'plan') {
    const d = add(planCard(r.plan, {session:true, step:r.step, size:r.ladder_size}), 'plan');
    d.querySelectorAll('[data-act]').forEach(b => b.onclick = () => { d.querySelectorAll('.fb button').forEach(x => x.disabled = true); user(b.textContent); turn({type:b.dataset.act}); });
  } else if (r.type === 'escalate') {
    const d = add(escalateCard(r.report), 'plan');
    wireHandoff(d, r.report);
    SID = null;
  } else if (r.type === 'closed') { bot('Great - closed. That fix now counts as a verified fix and ranks higher next time.'); SID = null; }
  else if (r.type === 'no_match') { bot(`I can't match that to a verified fix, so I won't guess (${String(r.reason || 'no_match').replace(/_/g, ' ')}).`); SID = null; }
}
async function turn(t) { try { handle(await api(`/v1/session/${SID}/turn`, {...t, offline: offline()})); } catch (e) { bot('Error: ' + e.message); } }
async function send(text) {
  user(text);
  try { if (SID) handle(await api(`/v1/session/${SID}/turn`, {type:'message', text, offline: offline()})); else handle(await api('/v1/session', {message:text, offline: offline()})); }
  catch (e) { bot('Error: ' + e.message); }
}
$('#composer').onsubmit = e => { e.preventDefault(); const t = $('#msg').value.trim(); if (!t) return; $('#msg').value = ''; send(t); };
document.querySelectorAll('#examples button').forEach(b => b.onclick = () => { SID = null; send(b.dataset.q); });
$('#newChat').onclick = () => { SID = null; $('#chat').innerHTML = ''; bot('New conversation. What is happening?'); };
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-src]'); if (!b) return;
  b.parentElement.querySelectorAll('button').forEach(x => x.disabled = true);
  try { const r = await api('/v1/feedback', {source_id:b.dataset.src, outcome:b.dataset.out}); b.parentElement.insertAdjacentHTML('afterend', `<div class="note">Thanks - ${r.ranking.verified_fixes} verified fixes recorded for this plan.</div>`); } catch (err) { alert(err.message); }
});

// ---------- device health ----------
function spark(points, a) {
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const lo = Math.min(...ys, a.baseline_mean - 3 * a.baseline_std), hi = Math.max(...ys, a.baseline_mean + 3 * a.baseline_std) || 1;
  const X = x => (x - xs[0]) / (xs[xs.length - 1] - xs[0] || 1) * 300, Y = y => 64 - (y - lo) / (hi - lo || 1) * 58;
  const band = `<rect x="0" y="${Y(a.baseline_mean + 3 * Math.max(a.baseline_std, .1))}" width="300" height="${Math.max(1, Y(a.baseline_mean - 3 * Math.max(a.baseline_std, .1)) - Y(a.baseline_mean + 3 * Math.max(a.baseline_std, .1)))}" fill="rgba(62,145,255,.12)"/>`;
  const line = `<polyline fill="none" stroke="#3e91ff" stroke-width="2.2" points="${points.map(p => X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(' ')}"/>`;
  const dots = points.filter(p => a.anomaly_days.includes(p[0])).map(p => `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="3.2" fill="#e5484d"/>`).join('');
  return `<svg viewBox="0 0 300 70" preserveAspectRatio="none">${band}${line}${dots}</svg>`;
}
async function devices() {
  const r = await api('/v1/devices');
  $('#devices').innerHTML = r.devices.map(d => `<button data-dev="${esc(d.id)}">${esc(d.name)}</button>`).join('');
  $('#devices').querySelectorAll('button').forEach(b => b.onclick = () => predict(b.dataset.dev));
  const pick = r.devices.find(d => d.id === 'demo-galaxy-storage') || r.devices[0]; if (pick) predict(pick.id);
}
async function predict(id) {
  $('#devices').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.dev === id));
  const r = await api('/v1/predict?device=' + encodeURIComponent(id));
  const cards = Object.values(r.metrics).map(a => a.status === 'insufficient_data' ? '' : `<div class="metric"><h4>${esc(a.label)}</h4><div class="big">${esc(a.latest)}</div>${spark(a.points, a)}
     <div class="row">baseline ${a.baseline_mean} · last 7 days ${a.recent_mean} · z ${a.z_score} · trend ${a.slope_per_day}/day${a.days_to_full != null ? ' · full in ~' + a.days_to_full + ' days' : ''}</div></div>`).join('');
  const warns = r.warnings.length ? r.warnings.map(w => `<div class="card warn ${esc(w.severity)}"><div class="sev ${esc(w.severity)}">${esc(w.severity.toUpperCase())} · PREDICTED</div><p>${esc(w.message)}</p>
     ${w.plan ? `<div class="plan">${planCard(w.plan)}</div>` : '<p class="note">Preventive plan withheld: it failed validation.</p>'}</div>`).join('')
     : '<div class="card"><b>All clear.</b><p class="note">No anomaly beyond 3 standard deviations and no risky trend.</p></div>';
  $('#healthOut').innerHTML = `<div class="metrics">${cards}</div><h3 style="margin:18px 6px 10px">Proactive warnings</h3>${warns}<p class="note">${esc(r.data_note)} Method: baseline ${r.method.baseline_days} days vs last ${r.method.recent_days} days (z-score), ${r.method.trend_days}-day least-squares trend. Red dots = days above baseline + 3σ; blue band = normal range.</p>`;
}

// ---------- insights ----------
async function insights() {
  const m = await api('/v1/metrics');
  $('#stats').innerHTML = [['Requests', m.requests], ['Cache hit rate', Math.round(m.cache_hit_rate * 100) + '%'], ['p50 latency', m.latency_ms.p50 + ' ms'], ['p95 latency', m.latency_ms.p95 + ' ms'], ['Sessions', m.sessions], ['On-device requests', m.on_device_requests], ['Safe abstentions', m.abstentions], ['Invented links', m.url_leaks]].map(x => `<div><strong>${esc(x[1])}</strong><span>${x[0]}</span></div>`).join('');
  const ai = m.ai || {};
  $('#aiStatus').innerHTML = `<p class="note">Cloud: ${ai.enabled ? 'connected' : 'not configured'} · ${esc(ai.llm_model || '')} · embeddings ${ai.embeddings_ready ? 'ready' : 'not ready'}<br>LLM calls ${ai.llm_calls || 0} (failures ${ai.llm_failures || 0}) · understood ${ai.understood || 0} · fell back to rules ${ai.fallback || 0}<br>On-device mode requests: ${m.on_device_requests}. Flip the toggle at the top to run with cloud AI off.</p>`;
  const f = m.feedback;
  $('#board').innerHTML = f.by_fix.length ? `<p class="note">${f.verified_fixes} verified fixes from ${f.total_feedback} answers. Feedback reorders fixes only within the same risk class.</p><table><tr><th>Fix</th><th>Risk</th><th>Fixed</th><th>Not fixed</th><th>Score</th></tr>${f.by_fix.map(r => `<tr><td>${esc(r.title)}</td><td>${esc(r.risk)}</td><td>${r.verified_fixes}</td><td>${r.not_fixed}</td><td>${r.fix_score}</td></tr>`).join('')}</table>` : '<p class="note">No feedback yet. Answer "Did this fix it?" on a plan and it shows up here.</p>';
  const b = m.benchmark;
  $('#bench').innerHTML = b && b.configs ? `<table><tr><th>Configuration</th><th>Accuracy</th><th>Wrong plans</th></tr>${[['rules_only', 'Rules + hashed n-grams (on-device path)'], ['plus_embeddings', '+ bge-small embeddings'], ['full_ai', '+ Llama understanding']].filter(k => b.configs[k[0]]).map(k => `<tr><td>${k[1]}</td><td>${(b.configs[k[0]].accuracy * 100).toFixed(1)}% (${b.configs[k[0]].correct}/${b.configs[k[0]].total})</td><td>${b.configs[k[0]].wrong_plans}</td></tr>`).join('')}</table><p class="note">${esc(b.dataset && b.dataset.note)}</p>` : '<p class="note">No benchmark file.</p>';
}

// ---------- boot ----------
async function boot() {
  try { const h = await api('/health'); AI_ON = !!(h.ai && h.ai.enabled); $('#health').textContent = `Engine ready · ${h.assets === 'synthetic_demo' ? 'synthetic demo data' : 'official data'}`; $('#health').classList.add('ok'); }
  catch { $('#health').textContent = 'Engine offline'; }
  modeLine();
}
boot();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
// Deep links for demos: /#health, /#insights, /?q=complaint[&answer=option_id]
(async () => {
  const tab = location.hash.slice(1); if (TITLES[tab]) document.querySelector(`nav.bottom button[data-tab="${tab}"]`).click();
  const qs = new URLSearchParams(location.search), q = qs.get('q');
  if (qs.get('offline') === '1') { $('#offline').checked = true; modeLine(); }
  if (q) { await send(q); const a = qs.get('answer'); if (a && SID) { const ob = document.querySelector(`[data-opt="${CSS.escape(a)}"]`); user(ob ? ob.textContent : a); if (ob) ob.parentElement.querySelectorAll('button').forEach(x => x.disabled = true); await turn({type:'answer', option:a}); }
    if (qs.get('stuck') && SID) { for (let i = 0; i < +qs.get('stuck') && SID; i++) { user('Still stuck'); await turn({type:'still_stuck'}); } } }
})();
