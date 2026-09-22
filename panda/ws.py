"""WebSocket 广播（青龙 services/sock.ts）：消息协议保持一致
{type, message, references, status}，type ∈ ping|installDependence|uninstallDependence|manuallyRunScript|...
"""
import asyncio
import json


class WsManager:
    def __init__(self):
        self.clients = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop):
        self._loop = loop

    async def connect(self, ws):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws):
        if ws in self.clients:
            self.clients.remove(ws)

    async def _send(self, payload: str):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast(self, msg_type: str, message: str = '', references=None, status='success'):
        """供同步代码（线程池中的执行器）调用的广播入口。"""
        payload = json.dumps(
            {'type': msg_type, 'message': message, 'references': references or [], 'status': status},
            ensure_ascii=False,
        )
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._send(payload), loop)

    async def abroadcast(self, msg_type: str, message: str = '', references=None, status='success'):
        await self._send(
            json.dumps(
                {'type': msg_type, 'message': message, 'references': references or [], 'status': status},
                ensure_ascii=False,
            )
        )


ws_manager = WsManager()
