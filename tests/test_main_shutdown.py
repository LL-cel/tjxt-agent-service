import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHUTDOWN_DRIVER = r"""
import asyncio
import runpy
import selectors
import signal
import sys
from urllib.request import urlopen

import uvicorn
from web import app

mode = sys.argv[1]
cleanup = set()
resources = {}
app.router.on_shutdown.append(lambda: cleanup.add('application'))

async def background_task():
    try:
        await asyncio.Event().wait()
    finally:
        cleanup.add('task')

async def async_resource():
    try:
        yield
    finally:
        cleanup.add('generator')

class TestServer(uvicorn.Server):
    async def serve(self, sockets=None):
        await super().serve(sockets)
        if mode == 'error':
            raise RuntimeError('unexpected server failure')

def make_server(config):
    config.host = '127.0.0.1'
    config.port = 0
    server = TestServer(config)
    resources['loop'] = asyncio.get_running_loop()
    assert isinstance(resources['loop']._selector, selectors.SelectSelector)

    async def stop_when_ready():
        resources['task'] = asyncio.create_task(background_task())
        resources['generator'] = async_resource()
        await anext(resources['generator'])
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]

        def check_http():
            with urlopen(f'http://127.0.0.1:{port}/openapi.json', timeout=5) as response:
                assert response.status == 200

        await asyncio.to_thread(check_http)
        if mode == 'sigint':
            signal.raise_signal(signal.SIGINT)
        else:
            server.should_exit = True

    resources['stopper'] = asyncio.create_task(stop_when_ready())
    return server

uvicorn.Server = make_server
try:
    runpy.run_path('main.py', run_name='__main__')
finally:
    loop = resources['loop']
    assert isinstance(loop, asyncio.SelectorEventLoop)
    assert loop.is_closed(), 'event loop was not closed'
    assert resources['stopper'].done()
    assert resources['stopper'].exception() is None
    assert cleanup == {'application', 'task', 'generator'}, cleanup
    print('shutdown cleanup verified')
"""


class MainShutdownTests(unittest.TestCase):
    def run_shutdown(self, mode):
        result = subprocess.run(
            [sys.executable, '-B', '-W', 'error::ResourceWarning', '-c', SHUTDOWN_DRIVER, mode],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertIn('shutdown cleanup verified', result.stdout, result.stderr)
        self.assertIn('Application shutdown complete.', result.stderr)
        self.assertNotIn('Task was destroyed', result.stderr)
        self.assertNotIn('ResourceWarning', result.stderr)
        return result

    def test_normal_shutdown_cleans_resources(self):
        result = self.run_shutdown('normal')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_ctrl_c_exits_without_traceback(self):
        result = self.run_shutdown('sigint')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_unexpected_failure_is_not_suppressed(self):
        result = self.run_shutdown('error')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('RuntimeError: unexpected server failure', result.stderr)


if __name__ == '__main__':
    unittest.main()
