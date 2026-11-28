/* 工程控制台前端：配置 + 运行（二次确认）+ 底部输出面板 */
const TOKEN = (document.querySelector('meta[name=console-token]') || {}).content || '';
const H = {'Content-Type': 'application/json', 'X-Token': TOKEN};
const $ = (id) => document.getElementById(id);

let snapshot = {}, dirty = {}, actions = [], consoleCfg = {}, curAction = null;
let pollTimer = null, autoScroll = true, lastLen = 0;

function msg(t, c){ const el = $('msg'); if (el){ el.textContent = t; el.className = c || ''; } }
function setSvc(on){ const el = $('svc'); el.className = 'status ' + (on ? 'on' : 'off'); el.textContent = on ? '在线' : '离线'; }
function pretty(v, kind){ if (kind === 'json') return JSON.stringify(v, null, 2); return v === null || v === undefined ? '' : String(v); }
function badge(cls, text){ return '<span class="badge ' + cls + '">' + text + '</span>'; }

/* ---------- 配置区 ---------- */
function markChanged(row, key, changed){
  row.classList.toggle('changed', changed);
  if (changed) dirty[key] = true; else delete dirty[key];
  const b = $('save'); if (b) b.disabled = Object.keys(dirty).length === 0;
}
function renderField(f){
  const row = document.createElement('div'); row.className = 'row';
  const k = document.createElement('div'); k.className = 'k';
  k.innerHTML = '<b></b><span></span>';
  k.querySelector('b').textContent = f.key;
  k.querySelector('span').textContent = f.label;
  row.appendChild(k);
  const v = document.createElement('div'); let input;
  if (f.kind === 'bool'){
    input = document.createElement('input'); input.type = 'checkbox'; input.checked = !!f.value;
    input.addEventListener('change', () => {
      const ch = input.checked !== !!snapshot[f.key];
      markChanged(row, f.key, ch);
      if (ch) dirty[f.key] = input.checked; else delete dirty[f.key];
    });
  } else if (f.kind === 'json'){
    input = document.createElement('textarea'); input.value = pretty(f.value, f.kind);
    input.addEventListener('input', () => {
      let parsed, bad = false;
      try { parsed = JSON.parse(input.value); } catch (e) { bad = true; }
      input.style.borderColor = bad ? '#c62828' : '#cfd8e3';
      const ch = !bad && JSON.stringify(parsed) !== JSON.stringify(snapshot[f.key]);
      markChanged(row, f.key, ch);
      if (ch) dirty[f.key] = parsed; else delete dirty[f.key];
    });
  } else {
    input = document.createElement('input');
    input.type = f.kind === 'number' ? 'number' : 'text';
    input.value = pretty(f.value, f.kind);
    input.addEventListener('input', () => {
      const val = f.kind === 'number' ? Number(input.value) : input.value;
      const ch = String(val) !== String(snapshot[f.key]);
      markChanged(row, f.key, ch);
      if (ch) dirty[f.key] = val; else delete dirty[f.key];
    });
  }
  if (f.readonly){ input.disabled = true; input.classList.add('ro'); }
  v.appendChild(input); row.appendChild(v);
  return row;
}
function renderCfg(sections){
  const app = $('tab-cfg'); app.innerHTML = ''; snapshot = {}; dirty = {};
  for (const sec of sections){
    const s = document.createElement('section');
    const h = document.createElement('h2'); h.textContent = sec.title; s.appendChild(h);
    for (const f of sec.fields){ snapshot[f.key] = f.value; s.appendChild(renderField(f)); }
    app.appendChild(s);
  }
  const bar = document.createElement('section');
  const h = document.createElement('h2'); h.textContent = '保存';
  bar.appendChild(h);
  const row = document.createElement('div'); row.className = 'act';
  const btn = document.createElement('button'); btn.className = 'go'; btn.id = 'save'; btn.disabled = true;
  btn.textContent = '保存到 ini';
  btn.addEventListener('click', saveCfg);
  const tip = document.createElement('span'); tip.className = 'meta';
  tip.textContent = '只写 settings.ini；动作策略与面板外观分别在 actions.ini / console.ini';
  row.appendChild(btn); row.appendChild(tip); bar.appendChild(row);
  app.appendChild(bar);
}
async function saveCfg(){
  msg('保存中…');
  try {
    const r = await fetch('/api/config', {method: 'POST', headers: H, body: JSON.stringify({values: dirty})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    msg('已保存 ' + d.saved + ' 项；备份 ' + d.backup_dir, 'ok');
    await load();
  } catch (e){ msg('保存失败：' + e.message, 'err'); }
}


/* ---------- 运行区 ---------- */
const RISK_TXT = {safe: '安全', write: '会写盘', 'nas-write': '写影像盘'};
function riskCls(r){ return r === 'safe' ? 'b-safe' : (r === 'nas-write' ? 'b-nas' : 'b-write'); }
function renderRun(){
  const box = $('tab-run'); box.innerHTML = '';
  const s = document.createElement('section');
  const h = document.createElement('h2'); h.textContent = '可运行程序（策略见 actions.ini）';
  s.appendChild(h);
  for (const a of actions){
    const row = document.createElement('div'); row.className = 'act';
    const btn = document.createElement('button'); btn.className = 'go'; btn.textContent = '运行';
    btn.disabled = !a.enabled;
    btn.addEventListener('click', () => openConfirm(a));
    const mid = document.createElement('div');
    mid.innerHTML = '<b style="font-size:13px">' + a.label + '</b>'
      + badge(a.enabled ? 'b-safe' : 'b-off', a.enabled ? '启用' : '已禁用')
      + badge(riskCls(a.risk), RISK_TXT[a.risk] || a.risk)
      + (a.dry_run ? badge('b-dry', 'dry-run') : '')
      + (a.confirm ? badge('b-off', '需二次确认') : '');
    mid.title = a.risk_note || '';
    const side = document.createElement('span'); side.className = 'meta';
    side.textContent = a.kind === 'entry' ? '入口' : '工具';
    row.appendChild(btn); row.appendChild(mid); row.appendChild(side);
    s.appendChild(row);
  }
  box.appendChild(s);
}
function openConfirm(a){
  curAction = a;
  $('m-title').textContent = '确认执行：' + a.label;
  $('m-badges').innerHTML = badge(riskCls(a.risk), RISK_TXT[a.risk] || a.risk)
    + (a.dry_run ? badge('b-dry', 'dry-run（不写盘）') : '')
    + (a.confirm ? badge('b-off', '需二次确认') : '');
  const risk = $('m-risk');
  if (a.risk_note){ risk.hidden = false; risk.textContent = '影响：' + a.risk_note; } else { risk.hidden = true; }
  $('m-cmd').textContent = a.preview || '';
  $('m-phrase').textContent = a.phrase || '';
  $('m-input').value = '';
  $('m-confirmbox').style.display = a.confirm ? '' : 'none';
  $('m-ok').disabled = !!a.confirm;
  $('m-msg').textContent = '';
  $('modal').classList.add('on');
  if (a.confirm) $('m-input').focus();
}
function closeConfirm(){ $('modal').classList.remove('on'); curAction = null; }
async function doRun(){
  if (!curAction) return;
  const a = curAction;
  $('m-ok').disabled = true;
  $('m-msg').textContent = '启动中…';
  try {
    const r = await fetch('/api/run', {method: 'POST', headers: H,
      body: JSON.stringify({action: a.id, confirm: $('m-input').value.trim()})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    closeConfirm();
    switchTab('run');
    $('log').textContent = '';
    lastLen = 0;
    startPoll();
    msg('已启动：' + d.name, 'ok');
  } catch (e){
    $('m-msg').textContent = e.message;
    $('m-ok').disabled = false;
  }
}

/* ---------- 底部输出面板 ---------- */
function panelState(d){
  const running = d.running;
  $('p-name').textContent = d.name || '输出';
  $('p-state').textContent = d.name ? (running ? '运行中' : ('已结束 code=' + d.code)) : '空闲';
  $('p-time').textContent = d.elapsed ? (d.elapsed + 's') : '';
  $('p-stop').disabled = !running;
}
function paintLog(text){
  const el = $('log');
  el.textContent = text || '（无输出）';
  if (autoScroll) el.scrollTop = el.scrollHeight;
}
async function pollOnce(){
  try {
    const r = await fetch('/api/run', {cache: 'no-store'});
    const d = await r.json();
    panelState(d);
    const text = d.tail || '';
    if (text.length !== lastLen){ paintLog(text); lastLen = text.length; }
    return !!d.running;
  } catch (e){ return false; }
}
function startPoll(){
  const ms = Number(consoleCfg.POLL_MS) || 1500;
  if (pollTimer) clearInterval(pollTimer);
  const tick = async () => {
    const running = await pollOnce();
    if (!running && pollTimer){ clearInterval(pollTimer); pollTimer = null; }
  };
  tick();
  pollTimer = setInterval(tick, ms);
}
async function stopRun(){
  if (!confirm('终止当前任务？先 terminate，3 秒后 kill。')) return;
  try {
    const r = await fetch('/api/run/stop', {method: 'POST', headers: H, body: '{}'});
    const d = await r.json();
    panelState(d);
    msg(d.running ? '仍在运行' : '已终止', d.running ? 'err' : 'ok');
  } catch (e){ msg('终止失败：' + e.message, 'err'); }
}
function applyConsole(){
  const h = Number(consoleCfg.HEIGHT_PX) || 260;
  const fs = Number(consoleCfg.FONT_SIZE) || 12;
  document.documentElement.style.setProperty('--panel-h', h + 'px');
  document.documentElement.style.setProperty('--panel-fs', fs + 'px');
  autoScroll = consoleCfg.AUTOSCROLL !== false;
  $('p-scroll').textContent = '自动滚动：' + (autoScroll ? '开' : '关');
}

/* ---------- 装载与事件 ---------- */
async function load(){
  msg('读取中…');
  try {
    const r = await fetch('/api/config', {cache: 'no-store'});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    setSvc(true);
    $('title').textContent = data.project + ' 工程控制台';
    $('inipath').textContent = data.ini;
    $('actionsini').textContent = data.actions_ini || '';
    actions = data.actions || [];
    consoleCfg = data.console || {};
    renderCfg(data.sections || []);
    renderRun();
    applyConsole();
    const running = await pollOnce();
    if (running) startPoll();
    msg('已读取 ' + (data.sections || []).length + ' 段、' + actions.length + ' 个可运行程序');
  } catch (e){
    setSvc(false);
    const b = $('banner'); b.hidden = false;
    b.textContent = '读不到服务：' + e.message + '。请从工程根的 settings.html 启动控制台。';
    msg('未连接', 'err');
  }
}
function switchTab(name){
  for (const b of document.querySelectorAll('.tabs button')) b.classList.toggle('on', b.dataset.tab === name);
  $('tab-cfg').hidden = (name !== 'cfg');
  $('tab-run').hidden = (name !== 'run');
}

window.addEventListener('error', (ev) => {
  const b = $('banner'); if (b){ b.hidden = false; b.textContent = '页面脚本错误：' + (ev.message || ev.type); }
  setSvc(false);
});

document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));
$('m-cancel').addEventListener('click', closeConfirm);
$('m-ok').addEventListener('click', doRun);
$('m-input').addEventListener('input', () => {
  if (!curAction) return;
  $('m-ok').disabled = $('m-input').value.trim() !== (curAction.phrase || '');
});
$('p-stop').addEventListener('click', stopRun);
$('p-scroll').addEventListener('click', () => {
  autoScroll = !autoScroll;
  $('p-scroll').textContent = '自动滚动：' + (autoScroll ? '开' : '关');
  if (autoScroll) $('log').scrollTop = $('log').scrollHeight;
});
$('p-clear').addEventListener('click', () => { $('log').textContent = ''; lastLen = 0; });
$('p-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('log').textContent); msg('已复制日志', 'ok'); }
  catch (e){ msg('复制失败：' + e.message, 'err'); }
});
$('p-fold').addEventListener('click', () => {
  const p = $('panel');
  p.classList.toggle('collapsed');
  $('p-fold').textContent = p.classList.contains('collapsed') ? '展开' : '收起';
});
$('p-clean').addEventListener('click', async () => {
  const keep = Number((consoleCfg || {}).KEEP_LOG_FILES) || 20;
  if (!confirm('清理 ' + keep + ' 份以外的旧运行日志？此操作不可撤销。')) return;
  try {
    const r = await fetch('/api/logs/clean', {method: 'POST', headers: H, body: '{}'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    msg('已删除 ' + d.removed + ' 个旧日志（保留 ' + d.kept + ' 个，释放 ' + d.freed_kb + ' KB）', 'ok');
  } catch (e){ msg('清理失败：' + e.message, 'err'); }
});
load();
