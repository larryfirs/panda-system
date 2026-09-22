/* 熊猫系统前端 —— 无构建、原生 JS 单页应用 */
'use strict';

const LS_TOKEN = 'panda_token';
const $app = document.getElementById('app');

/* ---------------- utils ---------------- */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs && !attrs.nodeType && typeof attrs === 'object' && !Array.isArray(attrs)) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style') el.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'html') el.innerHTML = v;
      else el.setAttribute(k, v);
    }
  } else if (attrs !== undefined) children.unshift(attrs);
  for (const c of children.flat(9)) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, url, opt = {}) {
  const headers = { authorization: 'Bearer ' + (localStorage.getItem(LS_TOKEN) || '') };
  let full = url;
  const init = { method, headers };
  if (opt.params) {
    const qs = new URLSearchParams(
      Object.entries(opt.params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString();
    if (qs) full += (full.includes('?') ? '&' : '?') + qs;
  }
  if (opt.json !== undefined) {
    headers['content-type'] = 'application/json';
    init.body = JSON.stringify(opt.json);
  } else if (opt.form) {
    init.body = opt.form;
  }
  const resp = await fetch(full, init);
  if (resp.status === 401) {
    localStorage.removeItem(LS_TOKEN);
    location.hash = '#/login';
    throw new Error('未登录');
  }
  const data = await resp.json().catch(() => ({ code: 500, message: '响应解析失败' }));
  if (data.code !== undefined && data.code !== 200 && data.code !== 0) {
    if (data.code !== 420) toast(data.message || ('错误 ' + data.code), true);
    throw Object.assign(new Error(data.message || ''), { resp: data });
  }
  return data;
}

let toastTimer;
function toast(msg, isError) {
  let t = document.getElementById('_toast');
  if (!t) {
    t = h('div', { id: '_toast', style: 'position:fixed;top:70px;left:50%;transform:translateX(-50%);z-index:999;padding:10px 22px;border-radius:8px;color:#fff;font-size:14px;box-shadow:0 6px 20px rgba(0,0,0,.2);transition:.3s' });
    document.body.append(t);
  }
  t.textContent = msg;
  t.style.background = isError ? '#dc2626' : '#16a34a';
  t.style.opacity = '1';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.style.opacity = '0'), 2600);
}

function modal(title, bodyEl, onOk, opts = {}) {
  const mask = h('div', { class: 'mask' });
  const m = h('div', { class: 'modal' + (opts.wide ? ' wide' : '') },
    h('div', { class: 'body' }, bodyEl),
    opts.noFooter ? null : h('div', { class: 'footer' },
      h('button', { class: 'btn', onclick: () => mask.remove() }, '取消'),
      h('button', { class: 'btn primary', onclick: async () => { if (await onOk() !== false) mask.remove(); } }, '确定')),
  );
  m.prepend(h('header', {}, h('span', {}, title), h('span', { class: 'x', onclick: () => mask.remove() }, '✕')));
  mask.append(m);
  mask.addEventListener('click', e => { if (e.target === mask) mask.remove(); });
  $app.append(mask);
  return m;
}

function tag(status, map) {
  const [text, cls] = map[status] || map._ || ['未知', 'gray'];
  return h('span', { class: 'tag ' + cls }, text);
}
const CRON_STATUS = { 0: ['运行中', 'blue'], 1: ['空闲', 'green'], 2: ['禁用', 'gray'], 3: ['排队中', 'orange'], _: ['未知', 'gray'] };
const DEP_STATUS = { 0: ['安装中', 'blue'], 1: ['已安装', 'green'], 2: ['安装失败', 'red'], 3: ['卸载中', 'blue'], 4: ['已卸载', 'gray'], 5: ['卸载失败', 'red'], 6: ['排队中', 'orange'], 7: ['已取消', 'gray'], _: ['未知', 'gray'] };
const DEP_TYPES = { 0: 'nodejs', 1: 'python3', 2: 'linux' };
const INST_STATUS = { 0: ['运行中', 'blue'], 1: ['成功', 'green'], 2: ['已停止', 'gray'], 3: ['失败', 'red'], _: ['未知', 'gray'] };
function fmtTime(ts) { return ts ? new Date(ts).toLocaleString() : '-'; }

/* WebSocket */
let ws = null, wsHandlers = [];
function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const token = localStorage.getItem(LS_TOKEN) || '';
  try { ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${token}`); } catch { return; }
  ws.onmessage = ev => {
    try { const m = JSON.parse(ev.data); wsHandlers.forEach(fn => fn(m)); } catch { }
  };
  ws.onclose = () => setTimeout(connectWs, 5000);
}
function onWs(fn) { wsHandlers.push(fn); return () => { wsHandlers = wsHandlers.filter(x => x !== fn); }; }

/* ---------------- layout / routes ---------------- */
const MENU = [
  ['#/dashboard', '📊', '仪表盘'],
  ['#/crontab', '⏰', '定时任务'],
  ['#/records', '🕒', '运行记录'],
  ['#/scripts', '📄', '脚本管理'],
  ['#/logs', '📜', '日志管理'],
  ['#/diff', '🔍', '对比工具'],
  ['#/dependence', '📦', '依赖管理'],
  ['#/env', '🧩', '环境变量'],
  ['#/settings', '⚙️', '用户管理'],
  ['#/help', '📖', '帮助文档'],
];
let panelTitle = '熊猫系统';

async function renderShell(active) {
  const user = await api('GET', '/api/user').then(d => d.data).catch(() => ({ username: '?' }));
  $app.innerHTML = '';
  $app.append(h('div', { class: 'layout' },
    h('div', { class: 'sidebar' },
      h('div', { class: 'brand' }, h('img', { src: '/assets/logo.png' }), h('b', {}, panelTitle)),
      h('div', { class: 'menu' }, MENU.map(([href, icon, name]) =>
        h('a', { href, class: href === active ? 'active' : '' }, icon + ' ', h('span', { class: 'txt' }, name)))),
      h('div', { class: 'side-foot' }, 'Panda v1.0.0'),
    ),
    h('div', { class: 'main' },
      h('div', { class: 'topbar' },
        h('div', { class: 'title' }, MENU.find(m => m[0] === active)?.[2] || '熊猫系统'),
        h('div', { class: 'user' },
          h('span', {}, '👤 ' + (user.username || '')),
          h('button', { class: 'btn sm', onclick: logout }, '退出'))),
      h('div', { class: 'content', id: 'view' }, '加载中…'),
    ),
  ));
}

async function logout() {
  try { await api('POST', '/api/user/logout'); } catch { }
  localStorage.removeItem(LS_TOKEN);
  location.hash = '#/login';
}

const routes = {};
async function route() {
  const hash = location.hash || '#/login';
  const name = hash.split('/')[1] || 'login';
  if (!localStorage.getItem(LS_TOKEN) && name !== 'login') return renderLogin();
  if (name === 'login') return renderLogin();
  await renderShell('#/' + name);
  const view = document.getElementById('view');
  view.innerHTML = '';
  try {
    await (routes[name] || routes.dashboard)(view);
  } catch (e) {
    if (String(e.message) !== '未登录') view.append(h('div', { class: 'card' }, '页面加载失败: ' + e.message));
  }
}
window.addEventListener('hashchange', route);

/* ---------------- login / init ---------------- */
async function renderLogin() {
  const sys = await fetch('/api/system').then(r => r.json()).then(d => d.data).catch(() => ({ isInitialized: true }));
  $app.innerHTML = '';
  const username = h('input', { type: 'text', placeholder: '用户名', value: 'admin' });
  const password = h('input', { type: 'password', placeholder: '密码' });
  const codeIn = h('input', { type: 'text', placeholder: '两步验证码', style: 'display:none' });
  const err = h('div', { class: 'err' });
  const btn = h('button', { class: 'btn primary', style: 'width:100%;padding:9px', onclick: submit }, sys.isInitialized ? '登 录' : '初始化账号');

  async function submit() {
    err.textContent = '';
    try {
      if (!sys.isInitialized && !twoFactorMode) {
        await api('PUT', '/api/user/init', { json: { username: username.value, password: password.value } });
        toast('初始化成功，请登录');
        sys.isInitialized = true;
        btn.textContent = '登 录';
        return;
      }
      let r;
      if (twoFactorMode) {
        r = await api('PUT', '/api/user/two-factor/login', { json: { username: username.value, password: password.value, code: codeIn.value } });
      } else {
        r = await api('POST', '/api/user/login', { json: { username: username.value, password: password.value } });
      }
      localStorage.setItem(LS_TOKEN, r.data.token);
      connectWs();
      location.hash = '#/dashboard';
      route();
    } catch (e) {
      const resp = e.resp;
      if (resp && resp.code === 420) {
        twoFactorMode = true;
        codeIn.style.display = '';
        err.textContent = '请输入两步验证码';
      } else if (resp && resp.code === 410) {
        err.textContent = `尝试次数过多，请 ${resp.data} 秒后再试`;
      } else err.textContent = e.message || '登录失败';
    }
  }
  let twoFactorMode = false;
  [username, password, codeIn].forEach(el => el.addEventListener('keydown', e => e.key === 'Enter' && submit()));

  $app.append(h('div', { class: 'login-wrap' },
    h('div', { class: 'login-card' },
      h('img', { class: 'logo', src: '/assets/logo.png' }),
      h('h1', {}, panelTitle),
      h('div', { class: 'sub' }, sys.isInitialized ? '定时任务管理平台' : '首次使用，请设置管理员账号（不能为默认密码 admin）'),
      h('label', { class: 'field' }, h('span', {}, '用户名'), username),
      h('label', { class: 'field' }, h('span', {}, '密码'), password),
      codeIn, err, btn,
    )));
}

/* ---------------- dashboard ---------------- */
routes.dashboard = async (view) => {
  const [ov, trend, rt, sysInfo] = await Promise.all([
    api('GET', '/api/dashboard/overview').then(d => d.data),
    api('GET', '/api/dashboard/trend', { params: { days: 7 } }).then(d => d.data),
    api('GET', '/api/dashboard/runtime').then(d => d.data),
    api('GET', '/api/dashboard/system').then(d => d.data).catch(() => ({})),
  ]);
  const max = Math.max(1, ...trend.map(t => t.total));
  view.append(
    h('div', { class: 'statgrid' },
      h('div', { class: 'stat' }, h('div', { class: 'v' }, ov.total), h('div', { class: 'k' }, '任务总数')),
      h('div', { class: 'stat' }, h('div', { class: 'v' }, ov.enabled), h('div', { class: 'k' }, '已启用')),
      h('div', { class: 'stat' }, h('div', { class: 'v' }, ov.todayRuns), h('div', { class: 'k' }, '今日运行')),
      h('div', { class: 'stat' }, h('div', { class: 'v', style: 'color:#16a34a' }, ov.todaySuccess), h('div', { class: 'k' }, `成功率 ${ov.successRate}%`)),
      h('div', { class: 'stat' }, h('div', { class: 'v', style: 'color:#dc2626' }, ov.todayFail), h('div', { class: 'k' }, '今日失败')),
      h('div', { class: 'stat' }, h('div', { class: 'v' }, (ov.avgTime / 1000).toFixed(1) + 's'), h('div', { class: 'k' }, '平均耗时')),
    ),
    h('div', { class: 'card' }, h('h3', {}, '近 7 日趋势'),
      h('div', { style: 'display:flex;gap:14px;align-items:flex-end;height:140px;padding:8px 4px' },
        trend.map(t => h('div', { style: 'flex:1;text-align:center' },
          h('div', { style: `height:${(t.total / max) * 110}px;background:linear-gradient(#22c55e,#15803d);border-radius:4px 4px 0 0;margin:0 12%`, title: `成功${t.success} 失败${t.fail}` }),
          h('div', { class: 'muted' }, t.date)))),
    ),
    h('div', { class: 'card' }, h('h3', {}, `运行中任务（${rt.runningCount}）/ 排队（${rt.queuedCount}）`),
      rt.running.length === 0 ? h('div', { class: 'muted' }, '暂无运行中的任务') :
        h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, '任务'), h('th', {}, 'PID'), h('th', {}, '已运行'), h('th', {}, '操作')),
          rt.running.map(r => h('tr', {},
            h('td', {}, r.name), h('td', { class: 'mono' }, r.pid || '-'), h('td', {}, r.duration + 's'),
            h('td', {}, h('button', {
              class: 'btn sm danger', onclick: async () => { await api('PUT', '/api/crons/stop', { json: [r.cron_id] }); route(); }
            }, '停止')))))),
    h('div', { class: 'card' }, h('h3', {}, '系统信息'),
      h('div', { class: 'muted' }, `平台: ${sysInfo.platform || '-'} ｜ Python: ${sysInfo.python || '-'} ｜ CPU: ${sysInfo.cpus || '-'} ｜ 内存使用: ${sysInfo.memUsagePercent || '-'}%`)),
  );
};

/* ---------------- crontab ---------------- */
routes.crontab = async (view) => {
  let page = 1, size = 20, search = '', selected = new Set();
  const tbody = h('tbody');
  const pager = h('div', { class: 'pager' });

  async function load() {
    selected.clear();
    const d = await api('GET', '/api/crons', { params: { searchValue: search, page, size } });
    tbody.innerHTML = '';
    const rows = d.data || [];
    rows.forEach(c => tbody.append(row(c)));
    const total = d.total || rows.length;
    pager.innerHTML = '';
    pager.append(
      h('span', {}, `共 ${total} 条`),
      h('button', { class: 'btn sm', disabled: page <= 1, onclick: () => { page--; load(); } }, '上一页'),
      h('span', {}, `${page}`),
      h('button', { class: 'btn sm', disabled: page * size >= total, onclick: () => { page++; load(); } }, '下一页'),
    );
  }

  function row(c) {
    return h('tr', {},
      h('td', {}, h('input', { type: 'checkbox', onchange: e => e.target.checked ? selected.add(c.id) : selected.delete(c.id) })),
      h('td', {}, c.isPinned ? '📌 ' : '', c.name || '-', h('div', { class: 'muted mono' }, c.command)),
      h('td', { class: 'mono' }, c.schedule),
      h('td', {}, tag(c.status, CRON_STATUS), (c.labels || []).map(l => h('span', { class: 'tag gray', style: 'margin-left:4px' }, l))),
      h('td', { class: 'muted' }, c.last_execution_time ? new Date(c.last_execution_time * 1000).toLocaleString() : '-'),
      h('td', {}, h('div', { class: 'row-actions' },
        c.status === 0
          ? h('button', { class: 'btn sm danger', onclick: async () => { await api('PUT', '/api/crons/stop', { json: [c.id] }); load(); } }, '停止')
          : h('button', { class: 'btn sm', onclick: async () => { await api('PUT', '/api/crons/run', { json: [c.id] }); toast('已加入运行队列'); setTimeout(load, 1500); } }, '运行'),
        h('button', { class: 'btn sm', onclick: () => showCronLog(c) }, '日志'),
        h('button', { class: 'btn sm', onclick: () => editModal(c) }, '编辑'),
        h('button', { class: 'btn sm', onclick: async () => {
          await api(c.isDisabled ? 'PUT' : 'PUT', c.isDisabled ? '/api/crons/enable' : '/api/crons/disable', { json: [c.id] }); load();
        } }, c.isDisabled ? '启用' : '禁用'),
        h('button', { class: 'btn sm danger', onclick: async () => {
          if (!confirm(`删除任务 ${c.name} ?`)) return;
          await api('DELETE', '/api/crons', { json: [c.id] }); load();
        } }, '删除'))));
  }

  function fields(c) {
    c = c || {};
    const f = {
      name: h('input', { type: 'text', value: c.name || '', placeholder: '留空则取命令前 20 字符' }),
      command: h('input', { type: 'text', value: c.command || '', placeholder: '如 demo.py arg1（task/ql 前缀可省略；-m 600 表示超时 600 秒）' }),
      schedule: h('input', { type: 'text', value: c.schedule || '0 9 * * *', placeholder: '如 0 9 * * *（每天9点）/ @boot / @once' }),
      labels: h('input', { type: 'text', value: (c.labels || []).join(','), placeholder: '多个标签用逗号分隔' }),
      work_dir: h('input', { type: 'text', value: c.work_dir || '', placeholder: '留空 = data/scripts；可填相对/绝对子目录' }),
      log_name: h('input', { type: 'text', value: c.log_name || '', placeholder: '留空按“命令_任务ID”自动命名；填 /dev/null 丢弃日志' }),
      task_before: h('textarea', { rows: 2, placeholder: '主脚本执行前运行的 bash 代码，可留空' }, c.task_before || ''),
      task_after: h('textarea', { rows: 2, placeholder: '主脚本执行后运行的 bash 代码，可留空' }, c.task_after || ''),
      allow_multiple: h('input', { type: 'checkbox', ...(c.allow_multiple_instances ? { checked: '' } : {}) }),
    };
    const body = h('div', { class: 'form-grid' },
      h('div', { style: 'grid-column:1/-1' }, hp('调度写法与命令语法详见「帮助文档 → 定时规则 / 命令语法」。调度触发时若上次仍在运行，默认会先停止旧实例再执行。')),
      h('label', { class: 'field' }, h('span', {}, '任务名称'), f.name),
      h('label', { class: 'field' }, h('span', {}, '命令 *'), f.command),
      h('label', { class: 'field' }, h('span', {}, '调度表达式 *'), f.schedule),
      h('label', { class: 'field' }, h('span', {}, '标签（逗号分隔）'), f.labels),
      h('label', { class: 'field' }, h('span', {}, '工作目录'), f.work_dir),
      h('label', { class: 'field' }, h('span', {}, '日志目录名'), f.log_name),
      h('label', { class: 'field' }, h('span', {}, '前置代码 (bash)'), f.task_before),
      h('label', { class: 'field' }, h('span', {}, '后置代码 (bash)'), f.task_after),
      h('label', { class: 'field' }, h('span', {}, '允许并行多实例（不互斥停止旧任务）'), f.allow_multiple),
    );
    return { body, f };
  }

  function editModal(c) {
    c = c || {};
    const { body, f } = fields(c);
    modal(c.id ? '编辑任务' : '新建任务', body, async () => {
      const payload = {
        name: f.name.value, command: f.command.value, schedule: f.schedule.value,
        labels: f.labels.value.split(/[,，]/).map(x => x.trim()).filter(Boolean),
        work_dir: f.work_dir.value || null, log_name: f.log_name.value || null,
        task_before: f.task_before.value || null, task_after: f.task_after.value || null,
        allow_multiple_instances: f.allow_multiple.checked ? 1 : 0,
      };
      if (c.id) payload.id = c.id;
      await api(c.id ? 'PUT' : 'POST', '/api/crons', { json: payload });
      toast('保存成功'); load();
    });
  }

  const searchIn = h('input', { type: 'text', class: 'inline', placeholder: '搜索任务名/命令', style: 'width:230px', onkeydown: e => e.key === 'Enter' && (search = e.target.value, page = 1, load()) });
  view.append(
    h('div', { class: 'toolbar' },
      h('button', { class: 'btn primary', onclick: () => editModal(null) }, '新建任务'),
      h('button', { class: 'btn', onclick: async () => { if (selected.size) { await api('PUT', '/api/crons/run', { json: [...selected] }); toast('批量运行已提交'); load(); } } }, '批量运行'),
      h('button', { class: 'btn', onclick: async () => { if (selected.size) { await api('PUT', '/api/crons/stop', { json: [...selected] }); load(); } } }, '批量停止'),
      h('button', { class: 'btn', onclick: async () => { if (selected.size) { await api('PUT', '/api/crons/disable', { json: [...selected] }); load(); } } }, '批量禁用'),
      h('div', { class: 'spacer' }), searchIn,
      h('button', { class: 'btn', onclick: () => (page = 1, load()) }, '刷新'),
    ),
    h('div', { class: 'card', style: 'padding:0;overflow:auto' },
      h('table', { class: 'tbl' }, h('thead', {}, h('tr', {},
        h('th', {}, h('input', { type: 'checkbox', onchange: e => tbody.querySelectorAll('input[type=checkbox]').forEach(x => x.checked = e.target.checked) })),
        h('th', {}, '名称/命令'), h('th', {}, '调度'), h('th', {}, '状态'), h('th', {}, '上次执行'), h('th', {}, '操作'))), tbody)),
    pager,
  );
  load();
};

async function showCronLog(c) {
  const pre = h('pre', { class: 'logview' }, '加载中…');
  modal(`任务日志 - ${c.name}`, pre, null, { wide: true, noFooter: true });
  let offset = null, timer, stopped = false;
  async function pull() {
    try {
      const d = await api('GET', `/api/crons/${c.id}/log`, { params: offset === null ? { tail: true } : { offset } });
      const data = d.data;
      if (offset === null) { pre.textContent = data.content; offset = data.nextOffset; }
      else if (data.content) { pre.textContent += data.content; pre.scrollTop = pre.scrollHeight; offset = data.nextOffset; }
      if (data.status !== 'running') { clearInterval(timer); stopped = true; }
    } catch { if (stopped) return; }
  }
  await pull();
  timer = setInterval(() => { if (!document.body.contains(pre)) clearInterval(timer); pull(); }, 2000);
}

/* ---------------- records (运行记录) ---------------- */
routes.records = async (view) => {
  let page = 1, size = 20, search = '', status = '', selected = new Set(), timer = null;
  const tbody = h('tbody');
  const pager = h('div', { class: 'pager' });

  async function load(silent) {
    if (!silent) { selected.clear(); tbody.innerHTML = ''; }
    const d = await api('GET', '/api/records', { params: { page, size, searchValue: search, status: status === '' ? undefined : +status } });
    tbody.innerHTML = '';
    (d.data || []).forEach(r => tbody.append(row(r)));
    const total = d.total || 0;
    pager.innerHTML = '';
    pager.append(
      h('span', {}, `共 ${total} 条`),
      h('button', { class: 'btn sm', disabled: page <= 1, onclick: () => { page--; load(); } }, '上一页'),
      h('span', {}, `${page}`),
      h('button', { class: 'btn sm', disabled: page * size >= total, onclick: () => { page++; load(); } }, '下一页'),
    );
  }

  function row(r) {
    return h('tr', {},
      h('td', {}, r.status === 0 ? '' : h('input', { type: 'checkbox', onchange: e => e.target.checked ? selected.add(r.id) : selected.delete(r.id) })),
      h('td', {}, r.cron_name, h('div', { class: 'muted mono' }, r.command || '')),
      h('td', {}, tag(r.status, INST_STATUS), r.exit_code != null && r.status !== 1 ? h('span', { class: 'muted' }, ` (退出码 ${r.exit_code})`) : ''),
      h('td', { class: 'mono' }, r.pid || '-'),
      h('td', { class: 'muted' }, fmtTime(r.started_at * 1000)),
      h('td', { class: 'muted' }, r.finished_at ? fmtTime(r.finished_at * 1000) : '-'),
      h('td', {}, r.duration + 's'),
      h('td', {}, h('div', { class: 'row-actions' },
        h('button', { class: 'btn sm', onclick: () => showRecordLog(r) }, '日志'),
        r.status === 0
          ? h('button', { class: 'btn sm danger', onclick: async () => { await api('POST', `/api/crons/${r.cron_id}/instances/${r.id}/stop`); load(); } }, '停止')
          : h('button', { class: 'btn sm danger', onclick: async () => {
              if (!confirm(`删除记录 #${r.id} ?`)) return;
              await api('DELETE', '/api/records', { json: [r.id] }); load();
            } }, '删除'))));
  }

  function showRecordLog(r) {
    const pre = h('pre', { class: 'logview' }, '加载中…');
    modal(`运行日志 - ${r.cron_name} (#${r.id})`, pre, null, { wide: true, noFooter: true });
    let offset = null, t;
    async function pull() {
      try {
        const d = (await api('GET', `/api/records/${r.id}/log`, { params: offset === null ? { tail: true } : { offset } })).data;
        if (offset === null) { pre.textContent = d.content; offset = d.nextOffset; }
        else if (d.content) { pre.textContent += d.content; pre.scrollTop = pre.scrollHeight; offset = d.nextOffset; }
        if (d.status !== 'running') clearInterval(t);
      } catch { clearInterval(t); }
    }
    pull();
    t = setInterval(() => { if (!document.body.contains(pre)) clearInterval(t); pull(); }, 2000);
  }

  const searchIn = h('input', { type: 'text', class: 'inline', placeholder: '搜索任务名/命令', style: 'width:220px', onkeydown: e => e.key === 'Enter' && (search = e.target.value, page = 1, load()) });
  const statusSel = h('select', { class: 'inline', onchange: e => { status = e.target.value; page = 1; load(); } },
    h('option', { value: '' }, '全部状态'),
    h('option', { value: '0' }, '运行中'), h('option', { value: '1' }, '成功'),
    h('option', { value: '2' }, '已停止'), h('option', { value: '3' }, '失败'));
  view.append(
    h('div', { class: 'toolbar' },
      h('button', { class: 'btn', onclick: () => { if (selected.size) { api('DELETE', '/api/records', { json: [...selected] }).then(() => load()); } else toast('未选择', true); } }, '批量删除'),
      h('div', { class: 'spacer' }), statusSel, searchIn,
      h('button', { class: 'btn', onclick: () => { page = 1; load(); } }, '刷新'),
    ),
    h('div', { class: 'card', style: 'padding:0;overflow:auto' },
      h('table', { class: 'tbl' }, h('thead', {}, h('tr', {},
        h('th', {}, ''), h('th', {}, '任务'), h('th', {}, '状态'), h('th', {}, 'PID'),
        h('th', {}, '开始时间'), h('th', {}, '结束时间'), h('th', {}, '耗时'), h('th', {}, '操作'))), tbody)),
    pager,
  );
  await load();
  timer = setInterval(() => { if (!document.body.contains(tbody)) clearInterval(timer); else load(true); }, 5000);
};

/* ---------------- scripts ---------------- */
routes.scripts = async (view) => {
  let current = { path: '', filename: '' };
  const contentEl = h('textarea', { class: 'grow-editor', spellcheck: 'false', placeholder: '选择左侧文件进行编辑…' });
  const outputEl = h('pre', { class: 'logview', style: 'height:180px;margin-top:10px;display:none' });
  const treeEl = h('div', { class: 'tree' });
  let runPid = null;

  async function loadTree() {
    const d = await api('GET', '/api/scripts').then(x => x.data);
    treeEl.innerHTML = '';
    treeEl.append(node(d, ''));
  }
  function node(n, depth) {
    const box = h('div');
    const line = h('div', { class: 'node', onclick: () => { if (n.type === 'file') openFile(n); else box.querySelector('.children')?.classList.toggle('hide'); } },
      (n.type === 'directory' ? (n.children?.length ? '📂 ' : '📁 ') : '📄 ') + n.title);
    box.append(line);
    if (n.children?.length) {
      const kids = h('div', { class: 'children' }, n.children.map(ch => node(ch, depth + 1)));
      box.append(kids);
    }
    return box;
  }
  async function openFile(n) {
    const dir = n.parent === '/' ? '' : n.parent;
    current = { path: dir, filename: n.title };
    const d = await api('GET', '/api/scripts/detail', { params: { path: dir, file: n.title } });
    contentEl.value = d.data;
    contentEl.disabled = false;
  }
  async function save() {
    if (!current.filename) return toast('未选择文件', true);
    await api('PUT', '/api/scripts', { json: { path: current.path, filename: current.filename, content: contentEl.value } });
    toast('已保存'); loadTree();
  }
  async function newFile() {
    const name = h('input', { type: 'text', placeholder: '如 demo.py（可含子目录 dir/demo.py）' });
    modal('新建文件', h('label', { class: 'field' }, h('span', {}, '文件名'), name), async () => {
      await api('POST', '/api/scripts', { form: { filename: name.value, path: '', content: '' } });
      toast('创建成功'); loadTree();
    });
  }
  async function newDir() {
    const name = h('input', { type: 'text', placeholder: '文件夹名' });
    modal('新建文件夹', h('label', { class: 'field' }, h('span', {}, '名称'), name), async () => {
      await api('POST', '/api/scripts', { form: { directory: name.value } });
      toast('创建成功'); loadTree();
    });
  }
  async function rename() {
    if (!current.filename) return toast('未选择文件', true);
    const name = h('input', { type: 'text', value: current.filename });
    modal('重命名 / 移动', h('label', { class: 'field' }, h('span', {}, '新名称（可带相对路径实现移动）'), name), async () => {
      await api('POST', '/api/scripts', { form: { filename: name.value, path: current.path, content: contentEl.value, originFilename: current.filename } });
      current = { path: name.value.includes('/') ? name.value.split('/').slice(0, -1).join('/') : current.path, filename: name.value.split('/').pop() };
      toast('已重命名'); loadTree();
    });
  }
  async function del() {
    if (!current.filename) return toast('未选择文件', true);
    if (!confirm('确认删除 ' + current.filename)) return;
    await api('DELETE', '/api/scripts', { json: { path: current.path, filename: current.filename } });
    contentEl.value = ''; current = { path: '', filename: '' };
    toast('已删除'); loadTree();
  }
  async function run() {
    if (!current.filename) return toast('未选择文件', true);
    outputEl.style.display = '';
    outputEl.textContent = '> 运行中…\n';
    runPid = (await api('PUT', '/api/scripts/run', { json: { path: current.path, filename: current.filename, content: contentEl.value } })).data;
  }
  const off = onWs(m => {
    if (m.type === 'manuallyRunScript' && document.body.contains(outputEl)) {
      outputEl.textContent += m.message;
      outputEl.scrollTop = outputEl.scrollHeight;
    }
  });
  new MutationObserver(() => { if (!document.body.contains(outputEl)) { off(); } }).observe($app, { childList: true, subtree: true });

  view.append(
    h('div', { class: 'toolbar' },
      h('button', { class: 'btn primary', onclick: save }, '💾 保存'),
      h('button', { class: 'btn', onclick: newFile }, '新建文件'),
      h('button', { class: 'btn', onclick: newDir }, '新建文件夹'),
      h('button', { class: 'btn', onclick: rename }, '重命名/移动'),
      h('button', { class: 'btn danger', onclick: del }, '删除'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'btn primary', onclick: run }, '▶ 运行'),
      h('button', { class: 'btn', onclick: async () => { await api('PUT', '/api/scripts/stop', { json: { filename: current.filename, pid: runPid } }); toast('已停止'); } }, '■ 停止'),
    ),
    h('div', { class: 'grid2' }, treeEl,
      h('div', {}, contentEl, outputEl)),
  );
  loadTree();
};

/* ---------------- logs ---------------- */
routes.logs = async (view) => {
  const treeEl = h('div', { class: 'tree' });
  const contentEl = h('pre', { class: 'logview' }, '选择左侧日志文件查看内容');
  let sel = null;

  async function load() {
    const d = await api('GET', '/api/logs').then(x => x.data);
    treeEl.innerHTML = '';
    treeEl.append(node(d));
  }
  function node(n) {
    const box = h('div');
    const line = h('div', { class: 'node', onclick: async () => {
      if (n.type !== 'file') { box.querySelector('.children')?.classList.toggle('hide'); return; }
      sel = n;
      await openFile(n, 0);
    } }, (n.type === 'directory' ? '📁 ' : '📄 ') + n.title + (n.size ? ` (${(n.size / 1024).toFixed(0)}K)` : ''));
    box.append(line);
    if (n.children?.length) box.append(h('div', {}, n.children.map(node)));
    return box;
  }
  async function openFile(n, offset) {
    const path = n.parent === '/' ? '' : n.parent;
    const d = await api('GET', '/api/logs/detail', { params: { path, file: n.title, offset: offset || undefined, tail: offset ? undefined : true } });
    if (!offset) contentEl.textContent = d.data;
    else contentEl.textContent += d.data;
    contentEl.scrollTop = contentEl.scrollHeight;
    if (d.logStatus === 'running') setTimeout(() => sel === n && openFile(n, d.nextOffset), 2000);
  }
  view.append(
    h('div', { class: 'toolbar' },
      h('button', { class: 'btn', onclick: load }, '刷新'),
      h('button', { class: 'btn danger', onclick: async () => {
        if (!sel) return toast('未选择', true);
        if (!confirm('删除 ' + sel.title)) return;
        await api('DELETE', '/api/logs', { json: { path: sel.parent === '/' ? '' : sel.parent, filename: sel.title } });
        contentEl.textContent = '…'; load();
      } }, '删除'),
      h('button', { class: 'btn', onclick: async () => {
        if (!sel) return toast('未选择', true);
        const form = new FormData();
        location.href = '/api/logs/detail?path=' + encodeURIComponent(sel.parent === '/' ? '' : sel.parent) + '&file=' + encodeURIComponent(sel.title) + '&limit=1048576';
      } }, '下载查看(尾部1M)'),
    ),
    h('div', { class: 'grid2' }, treeEl, contentEl),
  );
  load();
};

/* ---------------- diff (对比工具) ---------------- */
routes.diff = async (view) => {
  const samples = (await api('GET', '/api/configs/samples').then(d => d.data)).map(s => ({
    label: `${s.origin} → ${s.extra.target}`, origin: s.origin, target: s.extra.target,
  }));
  const left = h('textarea', { spellcheck: 'false', readonly: '' });
  const right = h('textarea', { spellcheck: 'false' });
  const status = h('span', { class: 'muted' });
  let cur = samples[0];

  async function pick(sample) {
    cur = sample;
    const [a, b] = await Promise.all([
      api('GET', '/api/configs/detail', { params: { path: sample.origin } }).then(d => d.data),
      api('GET', '/api/configs/detail', { params: { path: 'config/' + sample.target } }).then(d => d.data).catch(() => '(当前文件不存在)'),
    ]);
    left.value = a; right.value = b;
    status.textContent = '左：官方样本 ｜ 右：当前配置（可直接编辑并保存）';
  }
  function renderDiff() {
    const a = left.value.split('\n'), b = right.value.split('\n');
    const max = Math.max(a.length, b.length);
    const rows = [];
    for (let i = 0; i < max; i++) {
      const same = a[i] === b[i];
      rows.push(h('tr', {},
        h('td', { style: 'width:40px;color:#999' }, i + 1),
        h('td', { style: same ? 'white-space:pre-wrap' : 'white-space:pre-wrap;background:#fde8e8' }, a[i] ?? ''),
        h('td', { style: same ? 'white-space:pre-wrap' : 'white-space:pre-wrap;background:#e6ffed' }, b[i] ?? '')));
    }
    modal('逐行对比', h('div', { style: 'max-height:70vh;overflow:auto' },
      h('table', { style: 'width:100%;border-collapse:collapse;font:12px Consolas,monospace' },
        h('tr', {}, h('th', {}, '#'), h('th', { style: 'text-align:left' }, '样本'), h('th', { style: 'text-align:left' }, '当前')), rows)),
      null, { wide: true, noFooter: true });
  }
  view.append(
    h('div', { class: 'toolbar' },
      samples.map(s => h('span', { class: 'tab', style: 'cursor:pointer', onclick: () => pick(s) }, s.label)),
      h('div', { class: 'spacer' }),
      h('button', { class: 'btn', onclick: renderDiff }, '🎨 逐行高亮对比'),
      h('button', { class: 'btn primary', onclick: async () => {
        await api('POST', '/api/configs/save', { json: { name: 'config/' + cur.target, content: right.value } });
        toast('已保存到当前配置');
      } }, '保存右侧'),
    ),
    h('div', { class: 'card' }, status),
    h('div', { class: 'diffwrap' }, left, right),
  );
  pick(samples[0]);
};

/* ---------------- dependence ---------------- */
routes.dependence = async (view) => {
  const tbody = h('tbody');
  async function load() {
    const d = await api('GET', '/api/dependencies');
    tbody.innerHTML = '';
    (d.data || []).forEach(x => tbody.append(h('tr', {},
      h('td', { class: 'mono' }, x.name),
      h('td', {}, DEP_TYPES[x.type]),
      h('td', {}, tag(x.status, DEP_STATUS)),
      h('td', { class: 'muted' }, x.remark || '-'),
      h('td', {}, h('div', { class: 'row-actions' },
        h('button', { class: 'btn sm', onclick: () => {
          modal('安装日志 - ' + x.name, h('pre', { class: 'logview' }, (x.log || []).join('\n') || '(无)'), null, { noFooter: true, wide: true });
        } }, '日志'),
        [6, 0, 3].includes(x.status) ? h('button', { class: 'btn sm', onclick: async () => { await api('PUT', '/api/dependencies/cancel', { json: [x.id] }); load(); } }, '取消') : null,
        h('button', { class: 'btn sm', onclick: async () => { await api('PUT', '/api/dependencies/reinstall', { json: [x.id] }); toast('已重新入队'); load(); } }, '重装'),
        h('button', { class: 'btn sm danger', onclick: async () => {
          if (!confirm('删除 ' + x.name + '（已安装则先卸载）')) return;
          await api('DELETE', '/api/dependencies', { json: [x.id] }); load();
        } }, '删除'))))));
  }
  function addModal() {
    const name = h('input', { type: 'text', placeholder: '包名，可带版本 requests==2.31 / got@2 / jq=1.6' });
    const type = h('select', {}, h('option', { value: '1' }, 'python3（安装到本系统虚拟环境）'), h('option', { value: '0' }, 'nodejs（npm 全局）'), h('option', { value: '2' }, 'linux（apt/apk）'));
    const remark = h('input', { type: 'text' });
    modal('新增依赖', h('div', {},
      hp('python3 依赖安装到熊猫系统使用的虚拟环境；nodejs 走 npm 全局（可配镜像源，见系统设置）；linux 仅支持 Linux/容器环境。'),
      h('label', { class: 'field' }, h('span', {}, '名称 *'), name),
      h('label', { class: 'field' }, h('span', {}, '类型'), type),
      h('label', { class: 'field' }, h('span', {}, '备注'), remark)), async () => {
      await api('POST', '/api/dependencies', { json: [{ name: name.value, type: +type.value, remark: remark.value }] });
      toast('已加入安装队列'); load();
    });
  }
  const off = onWs(m => {
    if ((m.type === 'installDependence' || m.type === 'uninstallDependence') && document.body.contains(tbody)) {
      /* 增量日志弹窗打开时实时展示 */
      const dlg = document.querySelector('.mask pre.logview');
      if (dlg) { dlg.textContent += m.message + '\n'; dlg.scrollTop = dlg.scrollHeight; }
    }
  });
  new MutationObserver(() => { if (!document.body.contains(tbody)) off(); }).observe($app, { childList: true, subtree: true });
  view.append(
    h('div', { class: 'toolbar' },
      h('button', { class: 'btn primary', onclick: addModal }, '新增依赖'),
      h('div', { class: 'spacer' }), h('button', { class: 'btn', onclick: load }, '刷新')),
    h('div', { class: 'card', style: 'padding:0;overflow:auto' },
      h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, '名称'), h('th', {}, '类型'), h('th', {}, '状态'), h('th', {}, '备注'), h('th', {}, '操作')), tbody)),
  );
  load();
};

/* ---------------- env ---------------- */
routes.env = async (view) => {
  const tbody = h('tbody');
  async function load() {
    const d = await api('GET', '/api/envs');
    tbody.innerHTML = '';
    (d.data || []).forEach((e, i, arr) => tbody.append(h('tr', {},
      h('td', {}, e.isPinned ? '📌' : '', e.status === 1 ? tag(2, { 2: ['禁用', 'gray'] }) : null),
      h('td', { class: 'mono' }, e.name),
      h('td', { class: 'mono', style: 'max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, e.value),
      h('td', { class: 'muted' }, e.remarks || '-'),
      h('td', {}, (e.labels || []).map(l => h('span', { class: 'tag gray' }, l))),
      h('td', {}, h('div', { class: 'row-actions' },
        h('button', { class: 'btn sm', onclick: () => editModal(e) }, '编辑'),
        h('button', { class: 'btn sm', onclick: async () => {
          await api('PUT', e.status === 1 ? '/api/envs/enable' : '/api/envs/disable', { json: [e.id] }); load();
        } }, e.status === 1 ? '启用' : '禁用'),
        h('button', { class: 'btn sm', onclick: async () => {
          await api('PUT', e.isPinned ? '/api/envs/unpin' : '/api/envs/pin', { json: [e.id] }); load();
        } }, e.isPinned ? '取消置顶' : '置顶'),
        h('button', { class: 'btn sm', disabled: i === 0, onclick: () => move(arr, i, i - 1) }, '↑'),
        h('button', { class: 'btn sm', disabled: i === arr.length - 1, onclick: () => move(arr, i, i + 1) }, '↓'),
        h('button', { class: 'btn sm danger', onclick: async () => {
          if (!confirm('删除变量 ' + e.name)) return;
          await api('DELETE', '/api/envs', { json: [e.id] }); load();
        } }, '删除'))))));
  }
  async function move(arr, from, to) {
    await api('PUT', `/api/envs/${arr[from].id}/move`, { json: { fromIndex: from, toIndex: to } });
    load();
  }
  function editModal(e) {
    const name = h('input', { type: 'text', value: e?.name || '' });
    const value = h('textarea', { rows: 3 }, e?.value || '');
    const remarks = h('input', { type: 'text', value: e?.remarks || '' });
    modal(e ? '编辑环境变量' : '新增环境变量', h('div', {},
      hp('名称只能包含字母、数字、下划线且不能以数字开头；值将直接注入任务进程环境（Python/JS/Shell 均可读取）。'),
      h('label', { class: 'field' }, h('span', {}, '名称 *'), name),
      h('label', { class: 'field' }, h('span', {}, '值 *'), value),
      h('label', { class: 'field' }, h('span', {}, '备注'), remarks)), async () => {
      const payload = { name: name.value, value: value.value, remarks: remarks.value };
      if (e) { payload.id = e.id; await api('PUT', '/api/envs', { json: payload }); }
      else await api('POST', '/api/envs', { json: [payload] });
      toast('保存成功'); load();
    });
  }
  view.append(
    h('div', { class: 'toolbar' },
      h('button', { class: 'btn primary', onclick: () => editModal(null) }, '新增变量'),
      h('div', { class: 'spacer' }),
      h('span', { class: 'muted' }, '同名多值将以 & 连接后注入任务进程环境'),
      h('button', { class: 'btn', onclick: load }, '刷新')),
    h('div', { class: 'card', style: 'padding:0;overflow:auto' },
      h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, ''), h('th', {}, '名称'), h('th', {}, '值'), h('th', {}, '备注'), h('th', {}, '标签'), h('th', {}, '操作')), tbody)),
  );
  load();
};

/* ---------------- settings (用户管理/系统设置/通知) ---------------- */
const NOTIFY_MODES = [
  ['', '未启用'], ['gotify', 'Gotify'], ['serverChan', 'Server酱'], ['pushDeer', 'PushDeer'], ['bark', 'Bark'],
  ['telegramBot', 'Telegram 机器人'], ['dingtalkBot', '钉钉机器人'], ['weWorkBot', '企业微信群机器人'],
  ['email', '邮件'], ['pushPlus', 'PushPlus'], ['webhook', 'Webhook'], ['feishu', '飞书'], ['ntfy', 'ntfy'], ['pushMe', 'PushMe'],
];
const NOTIFY_FIELDS = {
  gotify: [['gotifyUrl', 'URL', '如 http://192.168.1.10:8080，自建 Gotify 服务器地址'], ['gotifyToken', 'Token', 'Gotify 网页 → APP/Tokens → 新建应用令牌（形如 Axxx...）'], ['gotifyPriority', '优先级', '1-10，数字越大越紧急，一般填 5']],
  serverChan: [['serverChanKey', 'SendKey', 'https://sct.ftqq.com 登录后获取，形如 SCTxxxxx...']],
  pushDeer: [['pushDeerKey', 'Key', 'https://api2.pushdeer.com 或自建服务，个人中心 → APP Token（xxxxxxx:xxxxxxx）'], ['pushDeerUrl', '服务器地址(可选)', '自建 PushDeer 时填 API 地址，留空使用官方']],
  bark: [['barkUrl', 'API地址(默认 api.day.app)', 'iOS 官方服务器留空即可；使用 Bark+ 自建时填完整地址'], ['barkPush', 'Key', 'Bark App 主界面显示的设备码（6 位左右字符串），多设备用 & 分隔'], ['barkGroup', '分组', '推送通知的分组名，默认 Panda'], ['barkSound', '声音', 'Bark 铃声名，如 choo、birdsong，留空为默认音']],
  telegramBot: [['telegramBotToken', 'Bot Token', '在 Telegram 找 @BotFather 发送 /newbot 获得，形如 1234567:AAxxxx...'], ['telegramBotUserId', 'Chat ID(多个用&分隔)', '给 @getuseridbot 发一条消息即可获得纯数字 ID'], ['telegramBotApiHost', 'API 地址(可选)', '被墙时填自建反代地址，如 https://tg.example.com，不带 /bot']],
  dingtalkBot: [['dingtalkBotToken', 'access_token', '钉钉群 → 群机器人 → 添加自定义机器人，Webhook URL 中 access_token= 后面的值'], ['dingtalkBotSecret', '加签 Secret(可选)', '安全设置选“加签”时 SEC 开头的密钥；选关键词/IP 白名单则留空']],
  weWorkBot: [['weWorkBotKey', 'Webhook Key', '企业微信群 → 群机器人 → 添加，Webhook URL 中 key= 后面的值']],
  email: [['emailService', 'SMTP 服务器', 'QQ 邮箱填 smtp.qq.com，163 填 smtp.163.com（465 端口 SSL）'], ['emailUser', '发件邮箱', '完整地址，如 xxx@qq.com；需先在邮箱设置中开启 SMTP/IMAP'], ['emailPass', '授权码', '⚠️ 不是登录密码！QQ/163 需在“账户-POP3/SMTP”中获取授权码'], ['emailTo', '收件人(多个用&分隔)', '完整收件邮箱地址，多个用 & 分隔']],
  pushPlus: [['pushPlusToken', 'Token', 'https://www.pushplus.plus 微信扫码登录，官网首页 → 一对一发送消息填写池中的 token'], ['pushPlusUser', '群组编码(可选)', '一对多发送时填写群组 code，一对一留空']],
  webhook: [['webhookUrl', 'URL', '接收通知的完整 HTTP(S) 地址'], ['webhookMethod', '方法(默认POST)', 'GET / POST / PUT 等'], ['webhookContentType', 'Content-Type', '如 application/json 或 text/plain，默认 application/json'], ['webhookHeaders', '自定义 Header JSON', 'JSON 对象，如 {"Authorization":"Bearer xxx"}，通知将以该 Header 发送']],
  feishu: [['larkKey', 'Webhook Key', '飞书群 → 设置 → 群机器人 → 自定义机器人，Webhook URL 中 hook= 后面的地址（或完整 URL）']],
  ntfy: [['ntfyUrl', '服务器', '官方 https://ntfy.sh，或自建如 http://192.168.1.10:80'], ['ntfyTopic', 'Topic', '订阅的主题名（自己起名，客户端订阅同名即收到）'], ['ntfyToken', 'Token(可选)', '服务器开启鉴权时填，ntfy 用户设置中生成']],
  pushMe: [['pushMeUrl', '服务器', '官方 https://push.me 或自建地址'], ['pushMeKey', 'Token', 'PushMe App 内生成的用户 token']],
};
const NOTIFY_HELP = {
  gotify: 'Gotify：开源自建推送服务，需自行部署后在网页端创建应用令牌。',
  serverChan: 'Server酱（Turbo 增强版）：微信扫码绑定方即可收消息，只需 SendKey。',
  pushDeer: 'PushDeer：开源推送方案，支持自定义图标与声音，Key 在个人中心获取。',
  bark: 'Bark：iOS 端 App，安装后主界面显示的即为设备码 Key；Android 也可安装。',
  telegramBot: 'Telegram：需要先向自己机器人发过消息才能收到推送。',
  dingtalkBot: '钉钉：机器人安全设置推荐选“加签”，否则需包含自定义关键词。',
  weWorkBot: '企业微信：仅支持群机器人 Webhook。',
  email: '邮件：SMTP 走 465 端口 SSL，务必使用授权码而非邮箱密码。',
  pushPlus: 'PushPlus：微信推送，免费版每天有限额。',
  webhook: 'Webhook：面板将向目标地址发送 JSON {title, content, type} 请求。',
  feishu: '飞书：群自定义机器人 Webhook（安全设置需选“自定义关键词”，内容含“熊猫”或自定）。',
  ntfy: 'ntfy：开源推送，Android/iOS 订阅主题即可。',
  pushMe: 'PushMe：轻量开源推送，App 内生成 token。',
};

routes.settings = async (view) => {
  const tabs = ['账号', '通知设置', '系统设置', '登录日志', 'IP 黑名单', '存储保留'];
  const panel = h('div');
  const tabBar = h('div', { class: 'tabs' }, tabs.map((t, i) =>
    h('span', { class: 'tab' + (i === 0 ? ' on' : ''), onclick: e => {
      tabBar.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
      e.target.classList.add('on'); renderTab(t);
    } }, t)));

  async function renderTab(t) {
    panel.innerHTML = '加载中…';
    if (t === '账号') panel.innerHTML = '', tabAccount(panel);
    if (t === '通知设置') { panel.innerHTML = ''; tabNotify(panel); }
    if (t === '系统设置') { panel.innerHTML = ''; tabSystem(panel); }
    if (t === '登录日志') { panel.innerHTML = ''; tabLoginLog(panel); }
    if (t === 'IP 黑名单') { panel.innerHTML = ''; tabBlacklist(panel); }
    if (t === '存储保留') { panel.innerHTML = ''; tabRetention(panel); }
  }

  async function tabAccount(panel) {
    const me = (await api('GET', '/api/user')).data;
    const u = h('input', { type: 'text', value: me.username });
    const p1 = h('input', { type: 'password', placeholder: '新密码（留空不修改）' });
    const tfState = h('span', { class: 'tag ' + (me.twoFactorActivated ? 'green' : 'gray') }, me.twoFactorActivated ? '已开启' : '未开启');
    panel.append(h('div', { class: 'card', style: 'max-width:520px' },
      h('h3', {}, '账号信息'),
      h('label', { class: 'field' }, h('span', {}, '用户名'), u),
      h('label', { class: 'field' }, h('span', {}, '密码'), p1),
      h('button', { class: 'btn primary', onclick: async () => {
        await api('PUT', '/api/user', { json: { username: u.value, password: p1.value || undefined } });
        toast('已保存，请重新登录'); setTimeout(logout, 800);
      } }, '保存'),
      h('div', { style: 'margin-top:18px;display:flex;gap:10px;align-items:center' },
        '两步验证: ', tfState,
        me.twoFactorActivated
          ? h('button', { class: 'btn', onclick: async () => { await api('PUT', '/api/user/two-factor/deactivate'); toast('已关闭'); renderTab('账号'); } }, '关闭')
          : h('button', { class: 'btn', onclick: async () => {
              const d = (await api('GET', '/api/user/two-factor/init')).data;
              const code = h('input', { type: 'text', placeholder: '6 位验证码' });
              modal('开启两步验证', h('div', {},
                h('p', { class: 'muted' }, '在身份验证器中添加密钥（TOTP）：'),
                h('p', { class: 'mono', style: 'margin:8px 0' }, d.secret),
                h('p', { class: 'muted', style: 'word-break:break-all' }, d.qrMessage),
                h('label', { class: 'field' }, h('span', {}, '验证码'), code)),
              async () => {
                await api('PUT', '/api/user/two-factor/active', { json: { code: code.value } });
                toast('已开启，请重新登录'); setTimeout(logout, 800);
              });
            } }, '开启'))));
  }

  async function tabNotify(panel) {
    const cfg = (await api('GET', '/api/user/notification')).data || {};
    const modeSel = h('select', {}, NOTIFY_MODES.map(([v, l]) => h('option', { value: v, ...(cfg.type === v ? { selected: '' } : {}) }, l)));
    const fieldsBox = h('div');
    const hint = h('p', { class: 'muted', style: 'margin:4px 0 10px;font-size:12.5px' });
    function drawFields() {
      fieldsBox.innerHTML = '';
      hint.textContent = NOTIFY_HELP[modeSel.value] || '';
      (NOTIFY_FIELDS[modeSel.value] || []).forEach(([key, label, ph]) => {
        const inp = h('input', { type: 'text', value: cfg[key] || '', placeholder: ph || '' });
        inp.dataset.key = key;
        fieldsBox.append(h('label', { class: 'field' }, h('span', {}, label), inp));
      });
    }
    modeSel.onchange = drawFields; drawFields();
    panel.append(h('div', { class: 'card', style: 'max-width:620px' },
      h('h3', {}, '通知渠道'),
      hp('任务完成/失败等事件将推送到下面选中的渠道；保存时会发送一条测试通知。带 ⚠️ 或说明的字段请按提示获取参数。'),
      h('label', { class: 'field' }, h('span', {}, '启用渠道'), modeSel),
      hint,
      fieldsBox,
      h('button', { class: 'btn primary', onclick: async () => {
        const payload = { type: modeSel.value };
        fieldsBox.querySelectorAll('input').forEach(i => payload[i.dataset.key] = i.value);
        await api('PUT', '/api/user/notification', { json: payload });
        toast('已保存并发送测试通知');
      } }, '保存并发送测试通知')));
  }

  async function tabSystem(panel) {
    const conf = (await api('GET', '/api/system/config')).data;
    const mk = (key, label, type, ph) => {
      const inp = type === 'number'
        ? h('input', { type: 'number', value: conf[key] ?? '', placeholder: ph || '' })
        : h('input', { type: 'text', value: conf[key] ?? '', placeholder: ph || '' });
      return { label, inp, key, save: v => api('PUT', `/api/system/config/${key.replace(/[A-Z]/g, m => '-' + m.toLowerCase())}`, { json: { [key]: v } }) };
    };
    const items = [
      mk('panelTitle', '面板标题'),
      mk('timezone', '时区', 'text', '如 Asia/Shanghai，修改后立即生效并重建调度'),
      mk('logRemoveFrequency', '日志保留天数（0=不删）', 'number', '每天 00:05 清理 data/log 下超期日志文件'),
      mk('cronConcurrency', '任务并发数（空=CPU 核数）', 'number', '同时运行任务数上限，保存后需点“重启面板”生效'),
      mk('commandTimeout', '默认命令超时秒数（0=不限制）', 'number', '未写 -m 的任务的全局超时'),
      mk('nodeMirror', 'Node 镜像源', 'text', '如 https://registry.npmmirror.com（安装 nodejs 依赖时使用）'),
      mk('pythonMirror', 'Python 镜像源', 'text', '如 https://pypi.tuna.tsinghua.edu.cn/simple（安装 python3 依赖时使用）'),
    ];
    panel.append(h('div', { class: 'card', style: 'max-width:560px' },
      h('h3', {}, '系统设置'),
      items.map(i => h('label', { class: 'field' }, h('span', {}, i.label), i.inp)),
      h('button', { class: 'btn primary', onclick: async () => {
        for (const i of items) await i.save(i.inp.value === '' ? null : (i.inp.type === 'number' ? +i.inp.value : i.inp.value));
        toast('已保存（并发数调整需重启任务队列后生效）');
      } }, '保存全部'),
      ' ', h('button', { class: 'btn', onclick: async () => { await api('PUT', '/api/system/reload', { json: {} }); toast('系统重启中…'); } }, '♻️ 重启面板')));
  }

  async function tabLoginLog(panel) {
    const logs = (await api('GET', '/api/user/login-log')).data || [];
    panel.append(h('div', { class: 'card' }, h('h3', {}, '最近登录记录'),
      h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, '时间'), h('th', {}, 'IP'), h('th', {}, '平台'), h('th', {}, '结果')),
        logs.map(l => h('tr', {},
          h('td', {}, new Date(l.timestamp).toLocaleString()), h('td', { class: 'mono' }, l.ip),
          h('td', {}, l.platform), h('td', {}, tag(l.status, { 0: ['成功', 'green'], 1: ['失败', 'red'], _: ['?', 'gray'] })))))));
  }

  async function tabBlacklist(panel) {
    const list = (await api('GET', '/api/user/ip-blacklist')).data || [];
    const inp = h('input', { type: 'text', class: 'inline', placeholder: 'IP，如 1.2.3.4', style: 'width:240px' });
    const box = h('div');
    function draw() {
      box.innerHTML = '';
      list.forEach(ip => box.append(h('div', { style: 'display:flex;gap:10px;align-items:center;margin:6px 0' },
        h('span', { class: 'mono' }, ip),
        h('button', { class: 'btn sm danger', onclick: async () => {
          await api('DELETE', '/api/user/ip-blacklist', { json: { ip } });
          list.splice(list.indexOf(ip), 1); draw();
        } }, '移除'))));
      if (!list.length) box.append(h('span', { class: 'muted' }, '暂无黑名单 IP'));
    }
    draw();
    panel.append(h('div', { class: 'card', style: 'max-width:560px' }, h('h3', {}, 'IP 黑名单（禁止登录）'),
      h('div', { class: 'toolbar' }, inp, h('button', { class: 'btn primary', onclick: async () => {
        const d = await api('PUT', '/api/user/ip-blacklist', { json: { ip: inp.value } });
        list.push(inp.value); inp.value = ''; draw();
      } }, '添加')), box));
  }

  async function tabRetention(panel) {
    const rc = (await api('GET', '/api/system/storage-retention/config')).data;
    const ri = h('input', { type: 'number', value: rc.runningInstanceRetentionDays ?? '' });
    const cs = h('input', { type: 'number', value: rc.cronStatRetentionDays ?? '' });
    const pv = h('div', { class: 'muted' });
    panel.append(h('div', { class: 'card', style: 'max-width:560px' },
      h('h3', {}, '存储保留'),
      h('label', { class: 'field' }, h('span', {}, '运行实例记录保留天数'), ri),
      h('label', { class: 'field' }, h('span', {}, '任务统计记录保留天数'), cs),
      h('div', { class: 'toolbar' },
        h('button', { class: 'btn', onclick: async () => {
          await api('PUT', '/api/system/storage-retention/config', { json: {
            runningInstanceRetentionDays: ri.value === '' ? null : +ri.value,
            cronStatRetentionDays: cs.value === '' ? null : +cs.value } });
          toast('已保存');
        } }, '保存'),
        h('button', { class: 'btn', onclick: async () => {
          const d = (await api('POST', '/api/system/storage-retention/preview', { json: {} })).data;
          pv.textContent = `可清理: 运行实例 ${d.runningInstances} 条 / 统计 ${d.cronStats} 条`;
        } }, '预览'),
        h('button', { class: 'btn danger', onclick: async () => {
          if (!confirm('确认清理历史数据？')) return;
          await api('POST', '/api/system/storage-retention/cleanup', { json: { confirmation: 'CLEAN' } });
          toast('清理完成');
        } }, '立即清理')),
      pv));
  }

  view.append(tabBar, panel);
  renderTab('账号');
};

/* ---------------- help (帮助文档) ---------------- */
function hp(...parts) { return h('p', { class: 'muted', style: 'margin:6px 0' }, ...parts); }
function hc(text) { return h('pre', { class: 'logview', style: 'height:auto;padding:10px;margin:8px 0' }, text); }
function htable(cols, rows) {
  return h('table', { class: 'tbl', style: 'margin:8px 0' },
    h('tr', {}, cols.map(c => h('th', {}, c))),
    rows.map(r => h('tr', {}, r.map((c, i) => h('td', i > 0 ? { class: 'mono' } : {}, c)))));
}
function hsec(id, title, ...children) {
  return h('div', { class: 'card', id, style: 'scroll-margin-top:70px' }, h('h3', {}, title), ...children);
}

routes.help = async (view) => {
  const toc = [
    ['quickstart', '快速上手'], ['cron-usage', '定时任务用法'], ['schedule', '定时规则'],
    ['command', '命令语法'], ['scripts-help', '脚本管理'], ['records-help', '运行记录'],
    ['env-help', '环境变量'], ['dep-help', '依赖管理'], ['diff-help', '对比工具'],
    ['notify-help', '通知设置'], ['user-help', '用户管理'], ['faq', '常见问题'],
  ];
  view.append(
    h('div', { class: 'card' }, h('h3', {}, panelTitle + ' 使用帮助'),
      hp('熊猫系统是青龙面板的 Python/FastAPI 重构版：内置调度器直接管理任务，无需系统 crontab；',
        '所有数据（脚本 / 配置 / 日志 / SQLite 数据库）都在安装目录的 data/ 下，整体拷贝即可完成迁移备份。'),
      h('div', { style: 'display:flex;flex-wrap:wrap;gap:8px;margin-top:8px' },
        toc.map(([id, t]) => h('a', { href: 'javascript:void(0)', class: 'btn sm', onclick: () => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' }) }, t)))),

    hsec('quickstart', '一、快速上手',
      hp('1. 在「脚本管理」中上传或新建脚本（支持 .py / .js / .mjs / .cjs / .ts / .sh / .ps1）。'),
      hp('2. 在「环境变量」中配置脚本需要的变量（Cookie、Token 等），保存后自动注入任务进程。'),
      hp('3. 在「定时任务」中新建任务：填写命令与调度表达式，点“运行”可立即试跑。'),
      hp('4. 在「运行记录」/「日志管理」中查看每次执行的输出与耗时。'),
      hp('5. 如需消息推送，在「用户管理 → 通知设置」中配置任一渠道。')),

    hsec('schedule', '二、定时规则（cron 表达式）',
      hp('支持 6 段（含秒）与 5 段（标准 crontab，自动按 0 秒执行）两种写法。时区跟随「系统设置 → 时区」（默认 Asia/Shanghai）。'),
      htable(['段', '取值', '示例'], [
        ['第1段 秒', '0-59（5 段写法时省略）', '0 / */30'],
        ['第2段 分', '0-59', '5 / */10'],
        ['第3段 时', '0-23', '9 / 0-18/2'],
        ['第4段 日', '1-31', '1,15 / L(每月最后一天可用 ? 代替)'],
        ['第5段 月', '1-12', '*/3'],
        ['第6段 周', '0-7（0 与 7 均为周日）', '1-5 / MON-FRI / 1,7'],
      ]),
      htable(['示例', '含义'], [
        ['0 9 * * *', '每天 9:00'],
        ['0 9 * * 1-5', '工作日 9:00'],
        ['0 */2 * * *', '每隔 2 小时'],
        ['0 30 8 * * 6', '每周六 8:30'],
        ['*/10 * * * * *', '每 10 秒（6 段）'],
        ['0 0 1 * *', '每月 1 日 0:00'],
      ]),
      hp('特殊规则：', h('code', {}, '@boot'), ' 面板启动时执行一次；', h('code', {}, '@once'),
        ' 只执行一次，执行后自动禁用；两者均不会按时间重复触发。不支持纯步长如 ', h('code', {}, '*/5'),
        ' 单独作为“每 5 分钟”以外的省略写法（必须写满段数）。'),
      hp('错过补偿：面板停机期间错过的调度不会补跑（与青龙一致）。任务可在“更多设置”里添加附加调度（extra schedules），一个任务多条 cron。')),

    hsec('command', '三、任务命令语法',
      hp('命令即要执行的脚本及参数，解释器按后缀自动选择（.py 使用熊猫系统虚拟环境）。以下前缀会被自动识别并可省略：'),
      hc('task demo.js run arg1        # 等价于 demo.js arg1\nql demo.py\n-m 600 demo.py arg     # 单任务超时 600 秒\nnow                    # 参数 now 会被忽略（青龙兼容）'),
      hp('多账号并发（青龙 otask 语义）：脚本读取某个环境变量作为账号 Cookie，一行一个用 & 连接——'),
      hc('conc COOKIES_VAR 1-max demo.js   # 按 & 拆分后，第 1~最后一个值各起一个进程并行执行\ndesi COOKIES_VAR 1,3 demo.js     # 只跑第 1、3 个值'),
      hp('任务前后置代码：任务编辑弹窗中的“前置/后置代码(bash)”在该任务主脚本执行前后运行（需 bash，Windows 需安装 Git Bash）。')),

    hsec('scripts-help', '四、脚本管理',
      hp('支持子目录树、在线编辑保存；同名文件自动备份到 data/bak。重命名/移动在同一弹窗完成。'),
      hp('“▶ 运行”以临时文件方式立即执行当前编辑内容（含未保存修改），输出经 WebSocket 实时回显；该方式不落盘正式文件。')),

    hsec('records-help', '五、运行记录',
      hp('每次执行都会生成一条记录：状态（运行中/成功/已停止/失败）、PID、起止时间、耗时与退出码。'),
      hp('运行中的记录每 5 秒自动刷新，可直接“停止”本次运行或查看实时日志。'),
      hp('历史记录过多时可在「用户管理 → 存储保留」设置自动清理天数（仅清理已结束记录，不影响进行中的运行）。')),

    hsec('env-help', '六、环境变量',
      hp('名称需满足：以字母或下划线开头，仅含字母数字下划线。同名多个值会按顺序用 & 连接为一个值注入。'),
      hp('保存后自动生成 config/env.sh / env.js / env.py（与青龙 preload 机制兼容），Python/JS/Shell 任务进程均可直接读取。'),
      hp('禁用 = 不注入；置顶与 ↑↓ 控制写入顺序；标签用于批量筛选管理。')),

    hsec('dep-help', '七、依赖管理',
      htable(['类型', '版本写法示例', '实际执行'], [
        ['python3', 'requests==2.31 / requests（最新）', '.venv\\Scripts\\python -m pip install（自动使用虚拟环境）'],
        ['nodejs', 'got@2 / axios', 'pnpm add -g（无 pnpm 则 npm install -g）'],
        ['linux', 'jq=1.6 / jq', 'apt-get install / apk add（Windows 不支持）'],
      ]),
      hp('支持填写代理与镜像源（系统设置）。安装过程实时日志，可取消排队/安装中的任务；“重装”会先卸载再安装。')),

    hsec('diff-help', '八、对比工具',
      hp('左侧为官方样本（config.sample.sh / notify.js / notify.py），右侧为当前生效文件，逐行高亮差异后可直接保存右侧。'),
      hp('config.sh 位于 data/config/；sendNotify.js 与 notify.py 位于 data/scripts/（与青龙一致）。')),

    hsec('notify-help', '九、通知设置',
      hp('选择“启用渠道”后填写对应参数即可，保存时会自动发送一条测试通知验证配置。各渠道参数获取方式见页内字段说明；',
        '所有渠道均通过面板内配置生效，等价于青龙的 PUSH_* 环境变量体系。')),

    hsec('user-help', '十、用户管理',
      hp('密码格式与青龙兼容（scrypt），迁移旧数据后可直接沿用原密码；修改密码会使全部已登录设备失效。'),
      hp('登录保护：连续失败 3 次后按 3^失败次数 秒锁定；IP 黑名单中的地址直接拒绝登录。'),
      hp('两步验证（TOTP）：使用 Google Authenticator / 微软身份验证器等扫码或录入密钥，开启后需重新登录。'),
      hp('登录日志保留最近 100 条；头像支持 png/jpg/gif/webp/avif（≤5MB）。')),

    hsec('faq', '十一、常见问题',
      hp('Q: 任务一直“排队中/运行中”不动？→ 检查并发数（系统设置）与依赖是否缺失导致脚本卡住；可在运行记录中停止本次运行。'),
      hp('Q: .sh 任务在 Windows 报错？→ 安装 Git for Windows 并将 bash 加入 PATH。'),
      hp('Q: Python 脚本 import 失败？→ 在依赖管理中安装对应 python3 依赖，它会装入熊猫系统使用的虚拟环境。'),
      hp('Q: 如何更换端口/子路径？→ 环境变量 PD_PORT、PD_BASE_URL，详见 README.md。'),
      hp('Q: 数据如何备份？→ 停止面板后整体拷贝 data/ 目录即可。')),
  );
};

/* ---------------- boot ---------------- */
(async function boot() {
  if (localStorage.getItem(LS_TOKEN)) {
    api('GET', '/api/system/config').then(d => {
      panelTitle = d.data?.panelTitle || '熊猫系统';
      document.title = panelTitle;
    }).catch(() => { });
    connectWs();
  }
  route();
})();
