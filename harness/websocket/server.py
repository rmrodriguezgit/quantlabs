import asyncio
import json

import websockets

from orchestrator.engine import HarnessEngine


engine = HarnessEngine()


async def handler(ws):
    async for raw in ws:
        data = json.loads(raw)
        await ws.send(json.dumps(engine.chat(data.get('session_id','default'), data['message'], data.get('agent','planner'), 'websocket', 'guest')))


async def main():
    async with websockets.serve(handler,'0.0.0.0',8765):
        await asyncio.Future()


if __name__=='__main__':
    asyncio.run(main())
