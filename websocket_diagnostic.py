"""Regression checks for nonblocking, bounded WebSocket notifications."""
import ast
import asyncio
from pathlib import Path

tree = ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8-sig'))
hub_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Hub')
namespace = {'asyncio': asyncio}
exec(compile(ast.Module(body=[hub_class], type_ignores=[]), 'main.py', 'exec'), namespace)


class DisconnectedSocket:
    async def accept(self):
        pass

    async def send_text(self, message):
        await asyncio.sleep(0)
        raise ConnectionError('Simulated disconnected client')


async def main():
    hub = namespace['Hub']()
    broken = DisconnectedSocket()
    event = await hub.add(broken)
    sender = asyncio.create_task(hub.send_updates(broken, event))
    # Neither a failed sender nor duplicate cleanup may fail a mutation.
    await asyncio.gather(*(hub.push() for _ in range(100)))
    result = await asyncio.gather(sender, return_exceptions=True)
    assert isinstance(result[0], ConnectionError)
    hub.remove(broken)
    hub.remove(broken)
    await hub.push()
    assert not hub.clients

    class SlowSocket(DisconnectedSocket):
        async def send_text(self, message):
            await asyncio.Event().wait()

    class HealthySocket(DisconnectedSocket):
        def __init__(self):
            self.delivered = asyncio.Event()
            self.messages = 0

        async def send_text(self, message):
            assert message == 'update'
            self.messages += 1
            self.delivered.set()

    slow, healthy = SlowSocket(), HealthySocket()
    slow_sender = asyncio.create_task(hub.send_updates(slow, await hub.add(slow)))
    healthy_sender = asyncio.create_task(hub.send_updates(healthy, await hub.add(healthy)))
    await asyncio.wait_for(asyncio.gather(*(hub.push() for _ in range(100))), .5)
    await asyncio.wait_for(healthy.delivered.wait(), .5)
    assert healthy.messages == 1, 'A burst must not create 100 queued refreshes'
    result = await asyncio.gather(slow_sender, return_exceptions=True)
    assert isinstance(result[0], TimeoutError), 'Slow sends must time out'
    healthy.delivered.clear()
    await hub.push()
    await asyncio.wait_for(healthy.delivered.wait(), .5)
    healthy_sender.cancel()
    await asyncio.gather(healthy_sender, return_exceptions=True)
    hub.remove(slow)
    hub.remove(healthy)
    assert not hub.clients
    print('PASS: disconnected cleanup, duplicate removal, slow-client isolation, send timeout, burst coalescing, later updates')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
