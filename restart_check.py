"""Verify real-process restart persistence using a disposable database."""
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
from load_test import field, fixture

ROOT = Path(__file__).resolve().parent


def main():
    with tempfile.TemporaryDirectory(prefix='auction-restart-') as directory:
        env = os.environ.copy()
        env.update(DATA_DIR=directory, ADMIN_PASSWORD=secrets.token_hex(24),
                   SESSION_SECRET=secrets.token_hex(32), COOKIE_SECURE='false')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        stop_file = Path(directory)/'stop-server'
        resources = Path(directory)/'resources.json'
        with (ROOT / 'restart-server.log').open('w') as log:
            def start():
                stop_file.unlink(missing_ok=True)
                process = subprocess.Popen([sys.executable, 'capacity_server.py',
                    '--port', str(port), '--metrics', str(resources), '--stop-file', str(stop_file)],
                    cwd=ROOT, env=env, stdout=log, stderr=log)
                for _ in range(100):
                    try:
                        if httpx.get(url+'/healthz', timeout=1).status_code == 200:
                            return process
                    except httpx.RequestError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError('Restart test server failed to start')
                    time.sleep(.1)
                stop_file.touch()
                process.wait(timeout=10)
                raise RuntimeError('Restart test server not ready')

            def stop(process, crash=False):
                if crash:
                    os.kill(int(resources.with_suffix('.pid').read_text()), 15)
                else:
                    stop_file.touch()
                process.wait(timeout=15)

            process = start()
            try:
                with httpx.Client(base_url=url, follow_redirects=True) as admin, httpx.Client(base_url=url, follow_redirects=True) as rep:
                    admin.post('/admin/login', data={'password': env['ADMIN_PASSWORD']}).raise_for_status()
                    preview = admin.post('/admin/import/preview', data={'action': 'replace'},
                        files={'workbook': ('fixture.xlsx', fixture(2, 2))})
                    assert 'Import complete' in admin.post('/admin/import/confirm', data={'token': field(preview.text, 'token')}).text
                    rep.post('/login', data={'email': 'load001@example.com'})
                    admin.post('/admin/load', data={'prize_id': 1})
                    aid = admin.get('/api/display').json()['auction']['id']
                    admin.post('/admin/start', data={'auction_id': aid})
                    assert rep.post('/bid', data={'auction_id': aid, 'amount': 100}, headers={'Accept': 'application/json'}).json()['ok']
                    before = rep.get('/api/auction').json()
                    # Stop the process rather than just re-running initialization.
                    stop(process, crash=True)
                    process = start()
                    after = rep.get('/api/auction').json()
                    assert before['auction'] == after['auction']
                    assert after['balance'] == 1000000 and after['last_bid'] == 100
                    assert after['auction']['ends_at'] == before['auction']['ends_at']
                    assert 'Auction Controls' in admin.get('/admin').text
                    admin.post('/admin/end', data={'auction_id': aid})
                    assert rep.post('/bid', data={'auction_id': aid, 'amount': 101}, headers={'Accept': 'application/json'}).status_code == 400
                    preview = admin.post('/admin/close/preview')
                    data = {'auction_id': field(preview.text, 'auction_id'), 'bid_id': field(preview.text, 'bid_id')}
                    for _ in range(2):
                        admin.post('/admin/close', data=data).raise_for_status()
                    result = rep.get('/api/auction').json()
                    assert result['balance'] == 999900 and len(result['wins']) == 1
                    stop(process)
                    process = start()
                    persisted = rep.get('/api/auction').json()
                    assert persisted['balance'] == 999900 and len(persisted['wins']) == 1
            finally:
                stop(process)
        database = sqlite3.connect(Path(directory)/'auction.db')
        try:
            assert database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert database.execute('SELECT quantity FROM prizes').fetchone()[0] == 1
        finally:
            database.close()
    print('PASS: process restart preserves sessions, deadline, high bid, balance, award and stock; duplicate award remains safe')


if __name__ == '__main__':
    main()
