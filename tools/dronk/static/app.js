'use strict';
const $ = id => document.getElementById(id);
const available = v => v == null ? 'Unavailable' : String(v);
const number = v => v == null ? '—' : Number(v).toLocaleString();
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
let pending = false;
let latest = null;
function buttons() {
  const state = latest?.host.state;
  $('start').disabled = pending || !['STOPPED','ERROR'].includes(state);
  $('stop').disabled = pending || !['STARTING','RUNNING','ERROR'].includes(state);
}
async function control(action) {
  pending = true; buttons();
  try {
    const tokenResponse = await fetch('/api/control-token');
    if (!tokenResponse.ok) throw Error('Control token unavailable');
    const {token} = await tokenResponse.json();
    const response = await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json','X-DRONK-CSRF':token}, body:JSON.stringify({action})});
    if (!response.ok) throw Error(await response.text());
    $('control-result').textContent = `Host ${(await response.json()).state.toLowerCase()}`;
  } catch(error) { $('control-result').textContent = error.message; }
  finally { pending = false; buttons(); }
}
$('start').addEventListener('click', () => control('start'));
$('stop').addEventListener('click', () => control('stop'));
const age = s => s == null ? 'None' : s < 60 ? `${s} s ago` : s < 3600 ? `${Math.floor(s/60)} min ${s%60} s ago` : `${Math.floor(s/3600)} h ${Math.floor(s%3600/60)} min ago`;
const DETECT = {DETECTED:['detected','DRONE DETECTED'], CLEAR:['clear','NO DRONES DETECTED'], OFFLINE:['offline','OFFLINE / UNKNOWN']};
function detection(d) {
  const [cls, title] = DETECT[d.state] || DETECT.OFFLINE;
  $('detect').className = 'detect ' + cls;
  $('detect-title').textContent = (d.synthetic && d.state === 'DETECTED' ? 'SYNTHETIC ' : '') + title;
  const expiry = d.stale_seconds ? ` · tracks expire ${d.stale_seconds} s after last decode` : '';
  const why = {no_valid_position:'position unavailable', no_textual_id:'no readable ID', no_basic_id:'no ID message', ambiguous_id:'conflicting IDs'};
  const untracked = (d.untracked || []).slice(0, 2).map(u => `${u.id || u.transmitter} (${why[u.reason] || u.reason}, CH ${u.channel})`).join(', ');
  const split = d.state === 'DETECTED' ? `${d.tracked_count} tracked · ${d.untracked_count} not trackable${untracked ? ': ' + untracked : ''} · ` : '';
  $('detect-detail').textContent = d.state === 'OFFLINE' ? `${d.reason}. Drone presence unknown.`
    : `${split}Valid decoded Remote ID only · ${d.receivers_online} / ${d.receivers_total} receivers reporting${expiry}`;
  $('detect-count').textContent = d.state === 'OFFLINE' ? '—' : String(d.active_count);
  $('detect-age').textContent = age(d.seconds_since_last);
}
const band = ch => ch == null ? '' : ch <= 11 ? '2.4 GHz' : '5 GHz';
function coverage(scan) {
  const box = node('div', undefined, 'scan');
  if (!scan) { box.append(node('small', 'No scan telemetry (older firmware or no status yet)', 'scan-note')); return box; }
  const total = scan.channels.reduce((n, c) => n + c.dwell_ms, 0) || 1;
  const last = scan.channels.filter(c => c.last_decode_age_s != null).sort((a, b) => a.last_decode_age_s - b.last_decode_age_s)[0];
  box.append(node('div', `${scan.mode === 'hop' ? 'HOPPING' : 'FIXED'} · CH ${available(scan.current)} ${band(scan.current)} · dwell ${scan.dwell_ms} ms · ${number(scan.switches)} switches`, 'scan-head'));
  box.append(node('small', `Last RID decode: ${last ? `CH ${last.channel}, ${age(last.last_decode_age_s)}` : 'none this session'} · switch max ${number(scan.switch_us_max)} µs · tune errors ${number(scan.tune_errors)} · transition drops ${number(scan.transition_drops)}`));
  if (scan.mode === 'hop' && scan.on_channel_ms != null && scan.on_channel_ms > Math.max(4 * scan.dwell_ms, 2000))
    box.append(node('small', `Scan stalled: on CH ${scan.current} for ${Math.floor(scan.on_channel_ms / 1000)} s`, 'scan-warn'));
  const grid = node('div', undefined, 'scan-grid');
  for (const c of scan.channels) {
    const share = Math.round(100 * c.dwell_ms / total);
    const cls = ['scan-cell', c.channel === scan.current && 'current', c.hot && 'hot', c.decoded && 'decoded', [6, 149].includes(c.channel) && scan.mode === 'hop' && 'anchor'].filter(Boolean).join(' ');
    const cell = node('div', undefined, cls);
    cell.title = `CH ${c.channel} ${band(c.channel)}: ${share}% dwell, ${c.rf_frames} frames, ${c.candidates} RID candidates, ${c.decoded} decoded` + (c.last_decode_age_s == null ? '' : `, last decode ${age(c.last_decode_age_s)}`) + (c.hot ? ', prioritized' : '');
    cell.append(node('b', String(c.channel)), node('span', `${share}%`), node('span', c.decoded ? `${number(c.decoded)} RID` : number(c.rf_frames)));
    grid.append(cell);
  }
  box.append(grid);
  return box;
}
const STATES = {DETECTED_NO_POSITION:['nopos','Detected, Position Unavailable'], TRACKING:['tracking','Tracking'], STALE:['stale','Stale'], LAST_KNOWN:['stale','Last known']};
const LAST_KNOWN_REASONS = {no_receptions:'no Remote ID received', receiver_interrupted:'receiver coverage interrupted', monitoring_stopped:'monitoring stopped'};
function detections(list) {
  $('detections').replaceChildren();
  if (!list.length) { $('detections').append(node('p','No aircraft detected.','empty')); return; }
  for (const d of list) {
    const [cls, base] = STATES[d.state] || ['stale', d.state];
    const label = d.state === 'LAST_KNOWN' && d.marker ? `${base} (${LAST_KNOWN_REASONS[d.marker.reason] || d.marker.reason})` : base;
    const row = node('article', undefined, 'detection-row ' + cls);
    const p = d.last_position;
    const where = d.state === 'LAST_KNOWN' && p ? `Last known ${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)} at ${d.last_position_at}`
      : d.position_valid && p ? `${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}` : p ? `Last position ${d.last_position_at}` : 'No position reported';
    row.append(node('b', (d.synthetic ? 'SYNTHETIC · ' : '') + d.aircraft_id), node('span', label, 'detection-state'),
      node('small', `${d.identifier_type} · ${d.transport || 'transport unavailable'} · CH ${available(d.channel)}${d.frequency_mhz ? ' / ' + d.frequency_mhz + ' MHz' : ''} · ${available(d.rssi_dbm)} dBm · ${number(d.observation_count)} receptions`),
      node('small', `${where} · first ${d.first_seen} · last ${d.last_seen} · receivers ${d.receiver_ids.join(', ') || 'unavailable'}`));
    $('detections').append(row);
  }
}
const PROBLEM = [['usb_output_drops','USB output drops'],['malformed_frames','malformed'],['queue_drops','queue drops'],['oversize_drops','oversize']];
function capture(c) {
  const box = $('capture-status');
  if (!c) { box.textContent = 'Capture: none this session'; box.className = 'capture-line'; return; }
  const size = c.bytes < 1048576 ? `${(c.bytes / 1024).toFixed(1)} KB` : `${(c.bytes / 1048576).toFixed(1)} MB`;
  const receivers = c.receivers.map(r => `${r.stream}: ` + PROBLEM.map(([k, label]) => `${label} +${number(r.delta[k] ?? null)}`).join(', ') + `, USB disconnects ${r.usb_disconnects}`).join(' · ');
  box.textContent = `Capture ${c.state}${c.suspended_reason ? ' (' + c.suspended_reason.replaceAll('_', ' ') + ')' : ''}: ${c.file} · ${number(c.records)} records · ${size}` + (receivers ? ` · this session ${receivers}` : '');
  box.className = 'capture-line ' + c.state.toLowerCase();
}
async function history(q) {
  const box = $('history-results'); box.replaceChildren(); $('history-detail').replaceChildren();
  try {
    const response = await fetch('/api/aircraft?q=' + encodeURIComponent(q));
    if (!response.ok) throw Error(await response.text());
    const list = await response.json();
    if (!list.length) { box.append(node('p', 'No stored aircraft match.', 'empty')); return; }
    for (const a of list) {
      const row = node('button', undefined, 'history-row'); row.type = 'button';
      row.append(node('b', (a.synthetic ? 'SYNTHETIC · ' : '') + a.identifier), node('small', `${a.identifier_type} · ${number(a.reception_count)} receptions (${number(a.positioned_count)} with position) · ${number(a.sightings)} sightings · first ${a.first_seen} · last ${a.last_seen}`));
      row.addEventListener('click', () => detail(a.identifier_type, a.identifier));
      box.append(row);
    }
  } catch (error) { box.append(node('p', error.message, 'empty')); }
}
async function detail(type, id) {
  const box = $('history-detail'); box.replaceChildren();
  try {
    const response = await fetch(`/api/aircraft/detail?type=${encodeURIComponent(type)}&id=${encodeURIComponent(id)}&limit=100`);
    if (!response.ok) throw Error(await response.text());
    const d = await response.json(), a = d.aircraft;
    box.append(node('h3', `${a.identifier} · ${a.identifier_type}`), node('small', `First ${a.first_seen} · last ${a.last_seen} · ${number(a.reception_count)} receptions · ${number(a.positioned_count)} with position · ${number(a.sightings)} sightings`));
    box.append(node('h4', 'Sightings (observation windows, not flights)'));
    for (const s of d.sightings) box.append(node('small', `${s.start} → ${s.end} · ${s.duration_s.toFixed(1)} s · ${number(s.reception_count)} receptions · ${number(s.positioned_count)} positioned · ${s.receivers.join(', ')} · gap ${s.gap_seconds} s (${s.derivation})`));
    box.append(node('h4', `Latest ${d.timeline.length} receptions`));
    for (const r of d.timeline) box.append(node('small', `${r.received_at} · ${r.receiver || '—'} · CH ${available(r.channel)} · ${available(r.rssi_dbm)} dBm · ${r.position_valid ? `${r.position.latitude.toFixed(5)}, ${r.position.longitude.toFixed(5)}` : 'position unavailable' + (r.reason ? ` (${r.reason})` : '')} · ${r.provenance.join(' ')}`));
  } catch (error) { box.append(node('p', error.message, 'empty')); }
}
$('history-form').addEventListener('submit', event => { event.preventDefault(); history($('history-q').value.trim()); });
function database(db) {
  if (!db) return;
  const sync = db.sync ? (db.sync.error ? ` · sync failed` : ` · synced ${db.sync.files} file(s), +${db.sync.accepted}`) : '';
  $('db-status').textContent = db.state + sync;
  $('db-status').title = db.error || '';
}
function render(data) {
  latest = data; buttons(); detection(data.detection); detections(data.detections || []); capture(data.capture); database(data.db);
  $('mode').textContent = data.mode; $('mode').className = 'badge '+data.mode.toLowerCase();
  $('demo-banner').hidden = !data.demo;
  $('clock').textContent = new Date(data.server_time).toISOString().slice(11,19)+' UTC';
  $('host-state').textContent = data.host.state;
  if (!pending) $('control-result').textContent = data.host.error || data.host.reason || `Host ${data.host.state.toLowerCase()}`;
  $('host-pid').textContent = data.host.pid ? `Monitoring PID ${data.host.pid}` : data.host.error || 'No monitoring process';
  $('receiver-count').textContent = `${data.receivers.filter(r=>r.connected).length} / ${data.receivers.length}`;
  $('decoded-count').textContent = number(data.receivers.reduce((n,r)=>n+Number(r.counters.decoded_packs||0),0));
  $('cot-count').textContent = number(data.tak.written);
  $('tak-state').textContent = data.tak.state;
  for (const [id,key] of [['queue-drops','queue_drops'],['queued','queued'],['timeouts','timeouts']]) $('tak-'+id).textContent = number(data.tak[key] ?? 0);
  for (const key of ['attempts','written','failed']) $('tak-'+key).textContent = number(data.tak[key]);
  $('receivers').replaceChildren();
  if (!data.receivers.length) $('receivers').append(node('p','No USB receivers observed. Host service and receiver power are independent.','empty'));
  for (const r of data.receivers) {
    const card=node('article',undefined,'receiver-card'), title=node('div',undefined,'receiver-title');
    title.append(node('b',r.stream),node('span',r.connected?'● USB CONNECTED':r.connection_state==='not_observed'?'○ NOT OBSERVED':'○ USB DISCONNECTED',r.connected?'online':'offline'));
    card.append(title,node('small',r.receiver_id||'Identity unavailable'));
    card.append(node('small',`${r.frequency_mhz ? (r.frequency_mhz<3000?'2.4 GHz':'5 GHz'):'Band unavailable'} / CH ${available(r.channel)} · last reported: ${r.state}`));
    card.append(node('small',`Reported uptime: ${r.uptime_us==null?'Unavailable':Math.floor(r.uptime_us/1000000)+' s'} · reset reason: ${available(r.reset_reason)}`));
    const values=node('div',undefined,'receiver-values');
    for (const [label,value] of [['RF FRAMES',r.counters.rf_frames],['CANDIDATES',r.counters.candidate_frames],['RID PACKS',r.counters.decoded_packs],['MESSAGES',r.counters.decoded_messages],['RF ERRORS',r.counters.errors],['QUEUE DROP',r.counters.queue_drops],['OVERSIZE',r.counters.oversize_drops],['OUTPUT ERR',r.counters.output_errors],['HOST REJECT',r.rejected],['DUPLICATES',r.duplicates],['USB ERRORS',r.transport_errors]]) {
      const stat=node('div',number(value)); stat.append(node('label',label)); values.append(stat);
    }
    card.append(values, coverage(r.scan)); $('receivers').append(card);
  }
  $('logs').replaceChildren();
  for (const entry of data.logs.slice(0,150)) {
    const row=node('article',undefined,'log-row');
    const event=entry.event, r=event.receiver, a=event.aircraft;
    const detail=node('details'), summary=node('summary');
    const message=r ? `${r.stream} · ${r.connected?'USB connected':'USB disconnected'} · ${r.state} · CH ${available(r.channel)} · RF ${number(r.counters.rf_frames)} / RID ${number(r.counters.decoded_packs)}`
      : a ? `${a.synthetic?'SYNTHETIC · ':''}${a.id} · ${a.rf?.transport||a.source} · ${available(a.rf?.rssi_dbm)} dBm`
      : JSON.stringify(event);
    summary.append(node('time',entry.time),node('b',entry.kind.toUpperCase()),node('span',message));
    detail.append(summary,node('pre',JSON.stringify(event,null,2))); row.append(detail);
    $('logs').append(row);
  }
  if (!data.logs.length) $('logs').append(node('p','Waiting for host monitoring. Receiver RF state is not observed.','empty'));
}
const events=new EventSource('/events');
events.addEventListener('snapshot', e=>render(JSON.parse(e.data)));
events.onerror=()=>{
  $('mode').textContent='DISCONNECTED'; $('mode').className='badge disconnected';
  detection({state:'OFFLINE', reason:'Dashboard connection lost', seconds_since_last:null});
  $('detect-age').textContent='Unknown';
  $('control-result').textContent='Dashboard connection lost; host process state unknown';
  $('start').disabled=true; $('stop').disabled=true;
};
