/* OKK 前端逻辑：认证 / 轮询 / 图表 / 引擎控制 / 设置管理 */
'use strict';

const $ = (id) => document.getElementById(id);
let TOKEN = localStorage.getItem('okk_token') || '';
let STATUS_POLL = null;
let editingModelId = null;

/* ---------------- 主题 ---------------- */
function applyTheme(dark) {
  document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem('okk_theme', dark ? 'dark' : 'light');
  const icon = $('themeIcon');
  if (icon) icon.className = dark ? 'ph ph-sun' : 'ph ph-moon';
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.setAttribute('content', dark ? '#0e1116' : '#faf9fd');
}
function toggleTheme() { applyTheme(!document.documentElement.classList.contains('dark')); }
applyTheme(localStorage.getItem('okk_theme') !== 'light');

/* ---------------- API ---------------- */
async function api(path, method = 'GET', body = null) {
  const opt = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (TOKEN) opt.headers['Authorization'] = 'Bearer ' + TOKEN;
  if (body) opt.body = JSON.stringify(body);
  let res;
  try {
    res = await fetch('/api' + path, opt);
  } catch (e) {
    toast('网络错误，请检查服务器');
    throw e;
  }
  if (res.status === 401) {
    showLogin();
    throw new Error('未登录');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.error || ('请求失败 ' + res.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), ms);
}

/* ---------------- 登录 ---------------- */
function showLogin() {
  $('loginPage').classList.remove('hidden');
  $('loginPage').style.display = 'flex';
  $('appPage').classList.add('hidden');
  if (STATUS_POLL) clearInterval(STATUS_POLL);
}
function showApp() {
  $('loginPage').style.display = 'none';
  $('appPage').classList.remove('hidden');
  refreshAll();
  if (STATUS_POLL) clearInterval(STATUS_POLL);
  STATUS_POLL = setInterval(refreshStatus, 5000);
}
function togglePwd(id, btn) {
  const inp = $(id);
  inp.type = inp.type === 'password' ? 'text' : 'password';
  btn.querySelector('i').className = inp.type === 'password' ? 'ph ph-eye' : 'ph ph-eye-slash';
}
async function doLogin() {
  const pw = $('loginPassword').value.trim();
  if (pw.length < 6) { toast('密码至少 6 位'); return; }
  $('loginBtnText').textContent = '登录中...';
  try {
    const r = await api('/login', 'POST', { password: pw });
    TOKEN = r.token;
    localStorage.setItem('okk_token', TOKEN);
    if (r.first_run) toast('管理密码已设置，请妥善保存！', 3600);
    showApp();
  } catch (e) {
    toast(e.message);
  } finally {
    $('loginBtnText').textContent = '登录';
  }
}
$('loginPassword')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

function logout() {
  TOKEN = '';
  localStorage.removeItem('okk_token');
  showLogin();
}

/* ---------------- 页面切换 ---------------- */
function switchPage(name) {
  document.querySelectorAll('.page').forEach((p) => p.classList.add('hidden'));
  const page = $('page-' + name);
  if (page) page.classList.remove('hidden');
  document.querySelectorAll('.nav-item,.bottom-nav-item').forEach((n) => {
    n.classList.toggle('active', n.dataset.page === name);
  });
  if (name === 'positions') loadPositions();
  if (name === 'markets') loadMarkets();
  if (name === 'decisions') loadDecisions();
  if (name === 'logs') loadLogs();
  if (name === 'settings') loadSettings();
  if (name === 'dashboard') loadEquity();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------------- 状态轮询 ---------------- */
function fmtNum(v, d = 2) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function pnlClass(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : ''; }

async function refreshStatus() {
  let s;
  try { s = await api('/status'); } catch (e) { return; }
  // KPI
  $('kpiEquity').textContent = s.exchange_connected ? fmtNum(s.equity) : '--';
  $('kpiUnreal').textContent = '未实现: ' + (s.exchange_connected ? fmtNum(s.unrealized) : '--');
  const tp = $('kpiToday');
  tp.textContent = (s.today_pnl >= 0 ? '+' : '') + fmtNum(s.today_pnl);
  tp.className = 'kpi-num mt-1.5 ' + pnlClass(s.today_pnl);
  const tpc = $('kpiTodayPct');
  tpc.textContent = (s.today_pct >= 0 ? '+' : '') + fmtNum(s.today_pct) + '%';
  tpc.className = 'text-[.72rem] mt-1 ' + pnlClass(s.today_pct);
  $('kpiModel').textContent = s.active_model || '未配置';
  $('kpiLatency').textContent = s.models && s.models.length ? s.models[0].latency_ms : '--';
  $('kpiPosCount').textContent = s.positions_count;
  $('kpiCycles').textContent = s.cycles;

  // 引擎状态
  const dotCls = s.paused ? 'pause' : s.running ? 'run' : 'stop';
  $('engineDot').className = 'pulse-dot ' + dotCls;
  $('engineDotM').className = 'pulse-dot ' + dotCls;
  let text, sub;
  if (s.paused) {
    const until = s.paused_until === -1 ? '无限期' : new Date(s.paused_until * 1000).toLocaleTimeString('zh-CN');
    text = '引擎已暂停';
    sub = '恢复时间: ' + until + '（到点自动继续，或手动恢复）';
  } else if (s.running) {
    text = '引擎运行中 · 24H 自动交易';
    sub = s.last_cycle ? '上轮: ' + new Date(s.last_cycle * 1000).toLocaleTimeString('zh-CN') : '等待第一轮扫描...';
  } else {
    text = '引擎已停止';
    sub = '点击启动开始 24H 自动交易';
  }
  $('engineStatusText').textContent = text;
  $('engineSubText').textContent = sub;
  $('engineTextM').textContent = text;
  $('modelTextM').textContent = '模型: ' + (s.active_model || '--');

  // 暂停横幅
  const banner = $('pauseBanner');
  if (s.paused) {
    banner.classList.remove('hidden');
    $('pauseBannerText').textContent = s.paused_until === -1
      ? '引擎已无限期暂停，直到手动恢复'
      : '引擎已暂停，将于 ' + new Date(s.paused_until * 1000).toLocaleString('zh-CN') + ' 自动恢复';
  } else {
    banner.classList.add('hidden');
  }

  // 错误横幅
  const errB = $('errBanner');
  if (s.last_error || s.error) {
    errB.classList.remove('hidden');
    $('errBannerText').textContent = s.last_error || s.error;
  } else {
    errB.classList.add('hidden');
  }

  // 模式 & 交易所
  const modeChip = $('modeChip');
  modeChip.textContent = s.trading_mode === 'live' ? '实盘' : '模拟盘';
  modeChip.style.background = s.trading_mode === 'live' ? 'var(--m3-error-container)' : 'var(--m3-tertiary-container)';
  modeChip.style.color = s.trading_mode === 'live' ? 'var(--m3-on-error-container)' : 'var(--m3-on-tertiary-container)';
  $('exChip').textContent = s.exchange_connected ? (s.exchange + ' 已连接') : '交易所未连接';

  renderModelDash(s.models || []);
}

function renderModelDash(models) {
  const box = $('modelListDash');
  if (!models.length) {
    box.innerHTML = '<div class="text-[.82rem] text-on-surface-var">尚未配置 AI 模型，请到 设置 → AI 模型 添加（支持 OpenAI / DeepSeek / Gemini / Kimi / Groq 等）。</div>';
    return;
  }
  box.innerHTML = models.map((m) => {
    const ok = m.latency_ms >= 0 && m.latency_ms < 30000;
    return `<div class="flex items-center gap-3 rounded-2xl px-4 py-3" style="background:var(--m3-surface-container-low)">
      <span class="pulse-dot ${ok ? 'run' : 'stop'}"></span>
      <div class="min-w-0 flex-1">
        <div class="text-[.86rem] font-medium truncate">${esc(m.label || m.model)}</div>
        <div class="text-[.7rem] text-on-surface-var truncate">${esc(m.model)}</div>
      </div>
      <div class="text-right">
        <div class="text-[.86rem] font-bold ${ok ? 'pos' : 'neg'}">${ok ? m.latency_ms + ' ms' : '不可用'}</div>
        <div class="text-[.68rem] text-on-surface-var">成功率 ${(m.ok_rate * 100).toFixed(0)}%</div>
      </div>
    </div>`;
  }).join('');
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------------- 净值曲线 ---------------- */
async function loadEquity() {
  let r;
  try { r = await api('/equity-curve?hours=48'); } catch (e) { return; }
  const pts = r.points || [];
  const svg = $('equityChart');
  if (pts.length < 2) {
    svg.innerHTML = `<text x="300" y="95" text-anchor="middle" fill="var(--m3-on-surface-variant)" font-size="13">数据积累中，运行几轮后展示净值曲线</text>`;
    return;
  }
  const W = 600, H = 180, PAD = 14;
  const eq = pts.map((p) => p.equity);
  const min = Math.min(...eq), max = Math.max(...eq);
  const span = (max - min) || 1;
  const x = (i) => PAD + (i / (pts.length - 1)) * (W - PAD * 2);
  const y = (v) => H - PAD - ((v - min) / span) * (H - PAD * 2);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(' ');
  const area = line + ` L${x(pts.length - 1).toFixed(1)},${H - PAD} L${PAD},${H - PAD} Z`;
  const last = eq[eq.length - 1];
  svg.innerHTML = `
    <defs>
      <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--m3-primary)" stop-opacity=".28"/>
        <stop offset="100%" stop-color="var(--m3-primary)" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <path d="${area}" fill="url(#eg)"/>
    <path d="${line}" fill="none" stroke="var(--m3-primary)" stroke-width="2.2" stroke-linejoin="round"/>
    <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="4" fill="var(--m3-primary)"/>
    <text x="${PAD}" y="16" fill="var(--m3-on-surface-variant)" font-size="11">高 ${fmtNum(max)}</text>
    <text x="${PAD}" y="${H - 4}" fill="var(--m3-on-surface-variant)" font-size="11">低 ${fmtNum(min)}</text>
  `;
}

/* ---------------- 引擎控制 ---------------- */
function openPauseMenu(ev) {
  ev.stopPropagation();
  $('pauseMenu').classList.toggle('open');
}
document.addEventListener('click', () => $('pauseMenu')?.classList.remove('open'));

async function engineAction(action) {
  try {
    await api('/engine/' + action, 'POST');
    toast(action === 'start' ? '引擎已启动' : '引擎已停止');
    refreshStatus();
  } catch (e) { toast(e.message); }
}
async function pauseEngine(seconds) {
  try {
    await api('/engine/pause', 'POST', { seconds });
    toast(seconds === -1 ? '已无限期暂停' : '已暂停，到点自动恢复');
    refreshStatus();
  } catch (e) { toast(e.message); }
}
async function resumeEngine() {
  try {
    await api('/engine/resume', 'POST');
    toast('已恢复运行');
    refreshStatus();
  } catch (e) { toast(e.message); }
}
function emergencyClose() {
  openConfirm('确认一键全平？', '将市价平掉全部持仓并撤销所有挂单。此操作不可撤销！', async () => {
    try {
      const r = await api('/engine/emergency-close', 'POST');
      if (r.ok) toast('已平仓 ' + (r.closed || 0) + ' 个仓位');
      else toast('全平失败: ' + (r.error || '未知错误'), 4000);
      refreshStatus();
    } catch (e) { toast(e.message); }
  });
}

/* ---------------- 确认弹窗 ---------------- */
function openConfirm(title, text, onOk) {
  $('confirmTitle').textContent = title;
  $('confirmText').textContent = text;
  $('confirmDialog').classList.add('open');
  const btn = $('confirmOk');
  btn.onclick = () => { closeConfirm(); onOk && onOk(); };
}
function closeConfirm() { $('confirmDialog').classList.remove('open'); }

/* ---------------- 持仓 ---------------- */
async function loadPositions() {
  let r;
  try { r = await api('/positions'); } catch (e) { return; }
  const body = $('positionsBody');
  const pos = r.positions || [];
  if (!pos.length) {
    body.innerHTML = '<tr><td colspan="9" class="text-center text-on-surface-var py-10">暂无持仓</td></tr>';
    return;
  }
  body.innerHTML = pos.map((p) => `
    <tr>
      <td class="font-semibold">${esc(p.symbol)}</td>
      <td><span class="m3-chip" style="${p.side === 'long' ? 'background:var(--m3-tertiary-container);color:var(--m3-on-tertiary-container)' : 'background:var(--m3-error-container);color:var(--m3-on-error-container)'}">
        <i class="ph ph-${p.side === 'long' ? 'trend-up' : 'trend-down'}"></i>${p.side === 'long' ? '多' : '空'}</span></td>
      <td>${fmtNum(p.contracts, 4)}</td>
      <td>${fmtNum(p.entryPrice, 4)}</td>
      <td>${fmtNum(p.markPrice, 4)}</td>
      <td class="font-bold ${pnlClass(p.unrealizedPnl)}">${(p.unrealizedPnl >= 0 ? '+' : '') + fmtNum(p.unrealizedPnl)}</td>
      <td>${fmtNum(p.leverage, 0)}x</td>
      <td>${fmtNum(p.liquidationPrice, 2)}</td>
      <td><button class="m3-btn m3-btn-error !py-1.5 !px-3.5 !text-[.76rem]" onclick="closePos('${esc(p.symbol)}')"><i class="ph ph-x"></i>平仓</button></td>
    </tr>`).join('');
}
function closePos(sym) {
  openConfirm('确认平仓 ' + sym + '？', '将按市价立即平掉该仓位。', async () => {
    try {
      await api('/positions/close', 'POST', { symbol: sym });
      toast('平仓指令已发送');
      loadPositions();
      refreshStatus();
    } catch (e) { toast(e.message); }
  });
}

/* ---------------- 行情 ---------------- */
async function loadMarkets() {
  let r;
  try { r = await api('/markets'); } catch (e) {
    $('marketsBody').innerHTML = `<tr><td colspan="4" class="text-center text-on-surface-var py-10">${esc(e.message)}</td></tr>`;
    return;
  }
  $('topNLabel').textContent = r.scan_top_n;
  const body = $('marketsBody');
  if (!(r.markets || []).length) {
    body.innerHTML = '<tr><td colspan="4" class="text-center text-on-surface-var py-10">暂无数据</td></tr>';
    return;
  }
  body.innerHTML = r.markets.map((m) => `
    <tr>
      <td class="font-semibold">${esc(m.symbol)}</td>
      <td>${fmtNum(m.price, 4)}</td>
      <td class="font-bold ${pnlClass(m.change_pct)}">${(m.change_pct >= 0 ? '+' : '') + fmtNum(m.change_pct)}%</td>
      <td>${fmtNum(m.quote_volume / 1e6, 1)}M USDT</td>
    </tr>`).join('');
}

/* ---------------- 决策 ---------------- */
async function loadDecisions() {
  let r;
  try { r = await api('/decisions'); } catch (e) { return; }
  const list = $('decisionsList');
  const ds = r.decisions || [];
  if (!ds.length) {
    list.innerHTML = '<div class="m3-card p-8 text-center text-on-surface-var text-[.86rem]">暂无 AI 决策记录。启动引擎后，AI 的每个指令（包括被风控拒绝的）都会记录在这里。</div>';
    return;
  }
  const actMap = { open_long: ['开多', 'var(--m3-tertiary-container)', 'var(--m3-on-tertiary-container)', 'trend-up'],
    open_short: ['开空', 'var(--m3-error-container)', 'var(--m3-on-error-container)', 'trend-down'],
    close: ['平仓', 'var(--m3-primary-container)', 'var(--m3-on-primary-container)', 'x-circle'],
    hold: ['观望', 'var(--m3-surface-variant)', 'var(--m3-on-surface-variant)', 'pause'] };
  list.innerHTML = ds.map((d) => {
    const a = actMap[d.action] || actMap.hold;
    return `<div class="m3-card p-4">
      <div class="flex flex-wrap items-center gap-2">
        <span class="m3-chip" style="background:${a[1]};color:${a[2]}"><i class="ph ph-${a[3]}"></i>${a[0]}</span>
        <span class="font-semibold text-[.9rem]">${esc(d.symbol)}</span>
        <span class="m3-chip">置信度 ${(d.confidence * 100).toFixed(0)}%</span>
        <span class="m3-chip ${d.approved ? '' : ''}" style="${d.approved ? 'background:var(--m3-tertiary-container);color:var(--m3-on-tertiary-container)' : 'background:var(--m3-error-container);color:var(--m3-on-error-container)'}">
          <i class="ph ph-${d.approved ? 'check-circle' : 'shield-warning'}"></i>${d.approved ? '已执行' : '风控拒绝'}</span>
        <span class="text-[.7rem] text-on-surface-var ml-auto">${new Date(d.ts * 1000).toLocaleString('zh-CN')} · ${esc(d.model)} · ${d.latency_ms}ms</span>
      </div>
      <div class="text-[.8rem] text-on-surface-var mt-2 leading-relaxed">${esc(d.reason || '（无说明）')}</div>
      ${d.reject_reason ? `<div class="text-[.76rem] mt-1.5 rounded-xl px-3 py-2" style="background:var(--m3-error-container);color:var(--m3-on-error-container)">拒绝原因：${esc(d.reject_reason)}</div>` : ''}
    </div>`;
  }).join('');
}

/* ---------------- 日志 ---------------- */
async function loadLogs() {
  let r;
  try { r = await api('/logs?limit=300'); } catch (e) { return; }
  const colors = { error: 'var(--m3-error)', warn: '#e0a020', trade: 'var(--m3-primary)', info: 'var(--m3-on-surface-variant)' };
  $('logsList').innerHTML = (r.logs || []).map((l) => `
    <div class="flex gap-2.5 rounded-xl px-2.5 py-1.5" style="background:var(--m3-surface-container-low)">
      <span class="text-on-surface-var shrink-0">${new Date(l.ts * 1000).toLocaleTimeString('zh-CN')}</span>
      <span class="shrink-0 font-semibold" style="color:${colors[l.level] || colors.info}">[${esc(l.level)}]</span>
      <span class="break-all">${esc(l.message)}</span>
    </div>`).join('') || '<div class="text-on-surface-var p-4">暂无日志</div>';
}

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  // 交易所
  try {
    const ex = await api('/exchange');
    $('exBoundInfo').classList.toggle('hidden', !ex.configured);
    if (ex.configured) {
      $('exBoundName').textContent = ex.exchange;
      $('exBoundMask').textContent = ex.api_key_masked;
      $('exName').value = ex.exchange.replace('binance-usdm', 'binance');
      $('exTestnet').value = ex.testnet ? '1' : '0';
    }
  } catch (e) {}
  // 模型
  loadModels();
  // 设置项
  try {
    const s = await api('/settings');
    for (const [k, v] of Object.entries(s)) {
      const el = $('s_' + k);
      if (el) el.value = v;
    }
  } catch (e) {}
}

async function saveSettings() {
  const keys = ['trading_mode', 'max_leverage', 'max_position_pct', 'max_concurrent', 'stop_loss_pct',
    'take_profit_pct', 'min_confidence', 'dedupe_minutes', 'daily_loss_limit_pct',
    'consecutive_loss_cooldown', 'cooldown_minutes', 'scan_interval', 'scan_top_n',
    'kline_limit', 'llm_timeout', 'notify_webhook', 'auto_start'];
  const body = {};
  for (const k of keys) {
    const el = $('s_' + k);
    if (el) body[k] = el.value;
  }
  const pw = $('s_admin_password')?.value;
  if (pw) body.admin_password = pw;
  try {
    await api('/settings', 'PUT', body);
    toast('设置已保存');
    if (pw) { $('s_admin_password').value = ''; toast('管理密码已修改，请重新登录'); logout(); }
  } catch (e) { toast(e.message); }
}

/* ---------------- 交易所绑定 ---------------- */
async function saveExchange() {
  const name = $('exName').value;
  const body = {
    exchange: name,
    api_key: $('exKey').value.trim(),
    api_secret: $('exSecret').value.trim(),
    passphrase: $('exPass').value.trim(),
    testnet: $('exTestnet').value === '1',
  };
  if (!body.api_key || !body.api_secret) { toast('请填写 API Key 和 Secret'); return; }
  if (name === 'okx' && !body.passphrase) { toast('OKX 需要 Passphrase'); return; }
  toast('正在测试连接并绑定...', 8000);
  try {
    const r = await api('/exchange', 'POST', body);
    toast(`绑定成功！余额 ${fmtNum(r.balance.total)} USDT，持仓 ${r.positions} 个`, 4000);
    $('exKey').value = $('exSecret').value = $('exPass').value = '';
    loadSettings();
    refreshStatus();
  } catch (e) { toast(e.message, 4500); }
}
function unbindExchange() {
  openConfirm('确认解绑交易所？', '将删除本地保存的 API 密钥，引擎会停止运行。', async () => {
    try {
      await api('/exchange', 'DELETE');
      toast('已解绑');
      loadSettings();
      refreshStatus();
    } catch (e) { toast(e.message); }
  });
}

/* ---------------- AI 模型 ---------------- */
async function loadModels() {
  let r;
  try { r = await api('/llm/models'); } catch (e) { return; }
  const list = $('llmList');
  const ms = r.models || [];
  if (!ms.length) {
    list.innerHTML = '<div class="text-[.84rem] text-on-surface-var rounded-2xl px-4 py-5" style="background:var(--m3-surface-container-low)">还没有模型。点击「添加模型」配置第一个（推荐 DeepSeek 或 Gemini Flash，便宜又快）。</div>';
    return;
  }
  list.innerHTML = ms.map((m) => `
    <div class="flex flex-wrap items-center gap-3 rounded-2xl px-4 py-3" style="background:var(--m3-surface-container-low)">
      <span class="pulse-dot ${m.latency_ms > 0 && m.latency_ms < 30000 ? 'run' : 'stop'}"></span>
      <div class="min-w-0 flex-1">
        <div class="text-[.88rem] font-medium truncate">${esc(m.label)} <span class="m3-chip !text-[.62rem] ml-1">优先级 ${m.priority}</span>${m.enabled ? '' : '<span class="m3-chip !text-[.62rem] ml-1" style="background:var(--m3-error-container);color:var(--m3-on-error-container)">已停用</span>'}</div>
        <div class="text-[.72rem] text-on-surface-var truncate">${esc(m.model)} · Key: ${esc(m.api_key_masked || '无')} · 延迟 ${m.latency_ms > 0 ? m.latency_ms + 'ms' : '--'}</div>
      </div>
      <div class="flex gap-1.5">
        <button class="m3-btn m3-btn-text !py-1.5 !px-3 !text-[.78rem]" onclick='testOneModel(${JSON.stringify(m.model)})'><i class="ph ph-lightning"></i>测试</button>
        <button class="m3-btn m3-btn-outlined !py-1.5 !px-3 !text-[.78rem]" onclick='openModelDialog(${JSON.stringify({ id: m.id, label: m.label, model: m.model, base_url: m.base_url, priority: m.priority, enabled: m.enabled })})'><i class="ph ph-pencil-simple"></i>编辑</button>
        <button class="m3-btn m3-btn-error !py-1.5 !px-3 !text-[.78rem]" onclick="delModel(${m.id})"><i class="ph ph-trash"></i></button>
      </div>
    </div>`).join('');
}

function openModelDialog(data) {
  editingModelId = data && data.id ? data.id : null;
  $('modelDlgTitle').textContent = editingModelId ? '编辑 AI 模型' : '添加 AI 模型';
  $('mLabel').value = data?.label || '';
  $('mModel').value = data?.model || '';
  $('mKey').value = '';
  $('mBase').value = data?.base_url || '';
  $('mPriority').value = data?.priority ?? 100;
  $('mEnabled').value = String(data?.enabled ?? 1);
  $('modelDialog').classList.add('open');
}
function closeModelDialog() { $('modelDialog').classList.remove('open'); }

async function saveModel() {
  const body = {
    id: editingModelId,
    label: $('mLabel').value.trim(),
    model: $('mModel').value.trim(),
    api_key: $('mKey').value.trim(),
    base_url: $('mBase').value.trim(),
    priority: parseInt($('mPriority').value || '100'),
    enabled: $('mEnabled').value === '1',
  };
  if (!body.model) { toast('模型串必填'); return; }
  try {
    await api('/llm/models', 'POST', body);
    toast('模型已保存');
    closeModelDialog();
    loadModels();
    refreshStatus();
  } catch (e) { toast(e.message); }
}
function delModel(id) {
  openConfirm('删除该模型？', '删除后不可恢复。', async () => {
    try {
      await api('/llm/models/' + id, 'DELETE');
      toast('已删除');
      loadModels();
    } catch (e) { toast(e.message); }
  });
}
async function speedTest() {
  toast('正在对所有启用模型测速...', 10000);
  try {
    const r = await api('/llm/speed-test', 'POST');
    const ok = (r.results || []).filter((x) => x.ok);
    toast(ok.length ? `测速完成：最快 ${ok[0].label} ${ok[0].latency_ms}ms` : '所有模型测速失败，请检查 Key', 4000);
    loadModels();
    refreshStatus();
  } catch (e) { toast(e.message); }
}
async function testOneModel(model) {
  toast('测试中...', 6000);
  try {
    const r = await api('/llm/test', 'POST', { model });
    if (r.ok) toast(`成功！延迟 ${r.latency_ms}ms · 回复: ${r.reply?.slice(0, 30) || 'OK'}`, 4000);
    else toast('失败: ' + r.error, 4500);
    loadModels();
  } catch (e) { toast(e.message); }
}

/* ---------------- 全局刷新 ---------------- */
function refreshAll() {
  refreshStatus();
  loadEquity();
}

/* ---------------- 启动 ---------------- */
(async function init() {
  if (!TOKEN) { showLogin(); return; }
  try {
    const me = await api('/me');
    if (me.auth) { showApp(); return; }
  } catch (e) {}
  showLogin();
})();
