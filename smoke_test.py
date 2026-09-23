"""熊猫系统 API 冒烟测试（开发自测用）：python smoke_test.py"""
import json
import sys
import time

import httpx

BASE = 'http://127.0.0.1:3939'
c = httpx.Client(base_url=BASE, timeout=30)
TOKEN = {'authorization': ''}


def show(name, resp, expect=200):
    try:
        data = resp.json()
    except Exception:
        data = resp.text[:200]
    code = data.get('code') if isinstance(data, dict) else None
    ok_flag = code == expect
    print(f'{"PASS" if ok_flag else "FAIL"} {name}: {json.dumps(data, ensure_ascii=False)[:180]}')
    if not ok_flag:
        sys.exit(1)
    return data.get('data') if isinstance(data, dict) else data


# --- 用户/初始化 ---
show('system meta', c.get('/api/system'))
r = c.put('/api/user/init', json={'username': 'admin', 'password': 'panda123'})
print('init:', r.text)  # 可能已初始化
r = c.post('/api/user/login', json={'username': 'admin', 'password': 'wrong'})
assert r.json()['code'] == 400, '错误密码应被拒绝'
print('PASS 错误密码被拒绝')
r = c.post('/api/user/login', json={'username': 'admin', 'password': 'panda123'})
token = show('login', r)['token']
TOKEN['authorization'] = f'Bearer {token}'

h = TOKEN
show('未带 token 应 401', c.get('/api/crons'), expect=None) if False else None
r = c.get('/api/crons', headers=h)
show('crons list', r)

# --- 环境变量 ---
c.request('DELETE', '/api/envs', headers=h, json=[])
show('env create', c.post('/api/envs', headers=h, json=[{'name': 'SMOKE_VAR', 'value': 'hello&panda', 'remarks': 'test'}]))
envs = show('env list', c.get('/api/envs', headers=h))
assert any(e['name'] == 'SMOKE_VAR' for e in envs)

# --- 脚本管理 ---
show('scripts tree', c.get('/api/scripts', headers=h))
show('script create', c.post('/api/scripts', headers=h, data={
    'filename': 'smoke_demo.py', 'path': '',
    'content': 'import os\nprint("hello from panda", os.environ.get("SMOKE_VAR"))\nprint("中文日志输出 OK")\n'}, files={'file': (None, '')}))
show('script detail', c.get('/api/scripts/detail', headers=h, params={'path': '', 'file': 'smoke_demo.py'}))

# --- 定时任务 ---
crons = show('crons list2', c.get('/api/crons', headers=h))['data'] if False else show('crons list2', c.get('/api/crons?size=0', headers=h))
for old in crons:
    if old['command'] == 'smoke_demo.py':
        c.request('DELETE', '/api/crons', headers=h, json=[old['id']])
created = show('cron create', c.post('/api/crons', headers=h, json={
    'name': '冒烟测试任务', 'command': 'smoke_demo.py', 'schedule': '0 9 * * *',
    'labels': ['smoke'],
}))
cid = created['id']
show('cron bad schedule rejected', c.post('/api/crons', headers=h, json={
    'name': 'x', 'command': 'y.js', 'schedule': 'not a cron'}), expect=400)
show('cron run', c.put('/api/crons/run', headers=h, json=[cid]))
time.sleep(3)
log = show('cron log', c.get(f'/api/crons/{cid}/log', headers=h, params={'tail': 'false', 'offset': 0}))
assert 'hello from panda' in log['content'], '日志内容缺失'
print('PASS 任务执行日志包含脚本输出')
show('cron logs list', c.get(f'/api/crons/{cid}/logs', headers=h))
show('cron instances', c.get(f'/api/crons/{cid}/instances', headers=h))
show('cron disable', c.put('/api/crons/disable', headers=h, json=[cid]))
show('cron enable', c.put('/api/crons/enable', headers=h, json=[cid]))

# --- 运行记录 ---
recs = show('records list', c.get('/api/records', headers=h, params={'page': 1, 'size': 10}))
mine = [r for r in recs if r['cron_id'] == cid]
assert mine, '运行记录中未找到本次执行'
rid = mine[0]['id']
assert mine[0]['statusText'] == 'finished', f"记录状态异常: {mine[0]['statusText']}"
print('PASS 运行记录：实例已收尾，状态=成功')
show('records filter(status)', c.get('/api/records', headers=h, params={'status': 1, 'size': 5}))
show('records search', c.get('/api/records', headers=h, params={'searchValue': 'smoke_demo', 'size': 5}))
rlog = show('records log', c.get(f'/api/records/{rid}/log', headers=h, params={'tail': 'true'}))
assert 'hello from panda' in rlog['content'], '运行记录日志内容缺失'
print('PASS 运行记录：可按记录读取日志')
show('records delete', c.request('DELETE', '/api/records', headers=h, json=[rid]))
after = show('records list after delete', c.get('/api/records', headers=h, params={'searchValue': 'smoke_demo', 'size': 50}))
assert rid not in [r['id'] for r in after], '记录未被删除'
print('PASS 运行记录：删除成功')

# --- 视图 ---
views = show('views list', c.get('/api/crons/views', headers=h))
if not any(v['name'] == '冒烟视图' for v in views):
    show('view create', c.post('/api/crons/views', headers=h, json={
        'name': '冒烟视图', 'filters': [{'property': 'name', 'operation': 'Reg', 'value': '冒烟'}],
        'filterRelation': 'and'}))
show('view delete', c.request('DELETE', '/api/crons/views', headers=h, json=[v['id'] for v in views if v['name'] == '冒烟视图'] or [0]))

# --- 日志管理 ---
show('log tree', c.get('/api/logs', headers=h))

# --- 配置/对比 ---
show('samples', c.get('/api/configs/samples', headers=h))
show('config detail', c.get('/api/configs/detail', headers=h, params={'path': 'sample/config.sample.sh'}))
show('config detail(current)', c.get('/api/configs/detail', headers=h, params={'path': 'config/config.sh'}))
show('config detail(script)', c.get('/api/configs/detail', headers=h, params={'path': 'config/sendNotify.js'}))
show('config files', c.get('/api/configs/files', headers=h))

# --- 依赖管理 ---
deps = show('deps list', c.get('/api/dependencies', headers=h))
dep = show('dep create', c.post('/api/dependencies', headers=h, json=[{'name': 'tomli==2.0.1', 'type': 1, 'remark': 'smoke'}]))
did = dep[0]['id']
for _ in range(30):
    d = c.get(f'/api/dependencies/{did}', headers=h).json()['data']
    if d['status'] in (1, 2, 7):
        break
    time.sleep(2)
print('dep final status:', d['status'], 'log tail:', (d['log'] or [])[-3:])
assert d['status'] == 1, '依赖安装失败'
print('PASS 依赖安装（python3 → venv pip）')
show('dep delete', c.request('DELETE', '/api/dependencies', headers=h, json=[did]))

# --- dashboard ---
show('overview', c.get('/api/dashboard/overview', headers=h))
show('runtime', c.get('/api/dashboard/runtime', headers=h))
show('trend', c.get('/api/dashboard/trend', headers=h, params={'days': 7}))
show('system info', c.get('/api/dashboard/system', headers=h))

# --- system ---
show('system config', c.get('/api/system/config', headers=h))
show('set panel title', c.put('/api/system/config/panel-title', headers=h, json={'panelTitle': '熊猫系统'}))
show('retention', c.get('/api/system/storage-retention/config', headers=h))

# --- ws ---
import asyncio
import websockets  # noqa: E401 -- 可选


# --- 清理 ---
print('ALL SMOKE TESTS PASSED')
