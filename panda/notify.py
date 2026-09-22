"""通知发送（青龙 services/notify.ts 的常用通道子集，配置字段名与青龙一致）。

支持: gotify / serverChan / pushDeer / bark / telegramBot / dingtalkBot /
weWorkBot / email / pushPlus / webhook / feishu(lark) / ntfy / pushMe
"""
import json
import smtplib
from email.mime.text import MIMEText

import httpx

from . import auth

TIMEOUT = 15


def _post(url, payload=None, **kw):
    with httpx.Client(timeout=TIMEOUT) as c:
        return c.post(url, json=payload, **kw)


def _send_channel(mode: str, cfg: dict, title: str, content: str):
    text = f'{title}\n{content}' if title else content

    if mode == 'gotify' and cfg.get('gotifyUrl') and cfg.get('gotifyToken'):
        url = f"{cfg['gotifyUrl'].rstrip('/')}/message?token={cfg['gotifyToken']}"
        _post(url, {'title': title, 'message': content, 'priority': cfg.get('gotifyPriority') or 0})
    elif mode == 'serverChan' and cfg.get('serverChanKey'):
        _post(f'https://sctapi.ftqq.com/{cfg["serverChanKey"]}.send', {'title': title or '熊猫系统', 'desp': content})
    elif mode == 'pushDeer' and cfg.get('pushDeerKey'):
        url = (cfg.get('pushDeerUrl') or 'https://api2.pushdeer.com').rstrip('/') + '/message/push'
        _post(url, {'key': cfg['pushDeerKey'], 'type': 'markdown', 'title': title or '熊猫系统', 'content': content})
    elif mode == 'bark' and cfg.get('barkPush'):
        import urllib.parse as up
        base = (cfg.get('barkUrl') or 'https://api.day.app').rstrip('/')
        url = f'{base}/{cfg["barkPush"]}/{up.quote(title or "熊猫系统")}/{up.quote(content)}'
        params = {}
        if cfg.get('barkSound'):
            params['sound'] = cfg['barkSound']
        if cfg.get('barkGroup'):
            params['group'] = cfg['barkGroup']
        if cfg.get('barkLevel'):
            params['level'] = cfg['barkLevel']
        with httpx.Client(timeout=TIMEOUT) as c:
            c.get(url, params=params)
    elif mode == 'telegramBot' and cfg.get('telegramBotToken'):
        api = (cfg.get('telegramBotApiHost') or cfg.get('telegramBotApiUrl') or 'https://api.telegram.org').rstrip('/')
        for uid in str(cfg.get('telegramBotUserId') or '').split('&'):
            if uid:
                _post(f'{api}/bot{cfg["telegramBotToken"]}/sendMessage',
                      {'chat_id': uid, 'text': text})
    elif mode == 'dingtalkBot' and cfg.get('dingtalkBotToken'):
        url = f'https://oapi.dingtalk.com/robot/send?access_token={cfg["dingtalkBotToken"]}'
        _post(url, {'msgtype': 'text', 'text': {'content': text}})
    elif mode == 'weWorkBot' and cfg.get('weWorkBotKey'):
        _post(f'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={cfg["weWorkBotKey"]}',
              {'msgtype': 'text', 'text': {'content': text}})
    elif mode == 'pushPlus' and cfg.get('pushPlusToken'):
        url = (cfg.get('pushPlusUrl') or 'https://www.pushplus.plus').rstrip('/') + '/send'
        body = {'token': cfg['pushPlusToken'], 'title': title or '熊猫系统', 'content': content}
        if cfg.get('pushPlusUser'):
            body['topic'] = cfg['pushPlusUser']
        _post(url, body)
    elif mode == 'webhook' and cfg.get('webhookUrl'):
        headers = {'Content-Type': cfg.get('webhookContentType') or 'application/json'}
        if cfg.get('webhookHeaders'):
            try:
                headers.update(json.loads(cfg['webhookHeaders']))
            except Exception:
                pass
        body = content
        if (cfg.get('webhookContentType') or 'application/json').startswith('application/json'):
            body = {'name': title or '熊猫系统', 'content': content, 'text': text}
        method = (cfg.get('webhookMethod') or 'POST').upper()
        with httpx.Client(timeout=TIMEOUT) as c:
            c.request(method, cfg['webhookUrl'], json=body if isinstance(body, dict) else None,
                      content=None if isinstance(body, dict) else str(body), headers=headers)
    elif mode in ('feishu', 'lark') and cfg.get('larkKey'):
        _post(f'https://open.feishu.cn/open-apis/bot/v2/hook/{cfg["larkKey"]}',
              {'msg_type': 'text', 'content': {'text': text}})
    elif mode == 'ntfy' and cfg.get('ntfyUrl') and cfg.get('ntfyTopic'):
        url = f"{cfg['ntfyUrl'].rstrip('/')}/{cfg['ntfyTopic']}"
        headers = {'Title': title or '熊猫系统'}
        if cfg.get('ntfyToken'):
            headers['Authorization'] = f'Bearer {cfg["ntfyToken"]}'
        with httpx.Client(timeout=TIMEOUT) as c:
            c.post(url, content=text.encode(), headers=headers)
    elif mode == 'pushMe' and cfg.get('pushMeKey'):
        base = (cfg.get('pushMeUrl') or 'https://push.ihonker.org').rstrip('/')
        _post(f'{base}/api/v1/push', {'token': cfg['pushMeKey'], 'title': title or '熊猫系统', 'desc': content})
    elif mode == 'email' and cfg.get('emailService') and cfg.get('emailUser') and cfg.get('emailPass'):
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['Subject'] = title or '熊猫系统'
        msg['From'] = cfg['emailUser']
        to = [t for t in str(cfg.get('emailTo') or '').split('&') if t]
        msg['To'] = ', '.join(to)
        with smtplib.SMTP_SSL(cfg['emailService'], 465, timeout=TIMEOUT) as server:
            server.login(cfg['emailUser'], cfg['emailPass'])
            server.sendmail(cfg['emailUser'], to, msg.as_string())
    else:
        return False
    return True


def send_notify(content: str, title: str = '熊猫系统'):
    """按已启用通道发送通知；返回成功的通道名列表。异常吞掉仅记录。"""
    cfg = auth.get_notification_info()
    mode = cfg.get('type')
    sent = []
    if not mode:
        return sent
    try:
        if _send_channel(mode, cfg, title, content):
            sent.append(mode)
    except Exception as e:  # 通知失败不应影响主流程
        import traceback
        traceback.print_exc()
        print(f'[notify] 通道 {mode} 发送失败: {e}')
    return sent
