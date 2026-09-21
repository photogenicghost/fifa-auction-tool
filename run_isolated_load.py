"""Run the existing load test against a disposable local server."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing

import httpx

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stages', default='70,100')
    parser.add_argument('--stage-seconds', type=int, default=1200)
    parser.add_argument('--prefix', default='capacity')
    args = parser.parse_args()
    stages = [int(n) for n in args.stages.split(',')]
    original = hashlib.sha256((ROOT / 'auction.db').read_bytes()).hexdigest() if (ROOT / 'auction.db').exists() else None
    source_hash = hashlib.sha256((ROOT / 'main.py').read_bytes()).hexdigest()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix='auction-load-') as directory:
        env = os.environ.copy()
        env.update(DATA_DIR=directory, ADMIN_PASSWORD=secrets.token_hex(24),
                   SESSION_SECRET=secrets.token_hex(32), COOKIE_SECURE='false')
        env['LOAD_TEST_PASSWORD'] = env['ADMIN_PASSWORD']
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        stop_file = Path(directory)/'stop-server'
        with (ROOT / f'{args.prefix}-server.log').open('w') as log:
            server = subprocess.Popen(
                [sys.executable, 'capacity_server.py', '--port', str(port),
                 '--metrics', f'{args.prefix}-resources.json', '--stop-file', str(stop_file)], cwd=ROOT, env=env,
                stdout=log, stderr=log)
            try:
                for attempt in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Test server exited; inspect server log.')
                    try:
                        if httpx.get(url + '/healthz', timeout=1).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('Test server did not become ready.')
                result = subprocess.run(
                    [sys.executable, 'load_test.py', '--url', url,
                     '--confirm-test-host', '127.0.0.1', '--users', str(max(stages)),
                     '--stages', args.stages, '--stage-seconds', str(args.stage_seconds),
                     '--report', f'{args.prefix}-report.json'],
                    cwd=ROOT, env=env, timeout=(args.stage_seconds+90)*len(stages)+180)
                health = httpx.get(url + '/healthz', timeout=5).status_code
            finally:
                stop_file.touch()
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pid = int((ROOT / f'{args.prefix}-resources.pid').read_text())
                    os.kill(pid, 15)
                    server.wait(timeout=10)
        with closing(sqlite3.connect(Path(directory, 'auction.db').as_uri()+'?mode=ro', uri=True)) as database:
            integrity = database.execute('PRAGMA integrity_check').fetchone()[0]
            negative = database.execute('SELECT COUNT(*) FROM users WHERE balance<0').fetchone()[0]
            stock = database.execute('SELECT quantity FROM prizes').fetchone()[0]
            awards = database.execute('SELECT COUNT(*) FROM winners').fetchone()[0]
            initial_stock = max(10, ((args.stage_seconds+19)//20)*len(stages)+len(stages)*3)
            stock_ok = stock == initial_stock-awards
            invalid_awards = database.execute('''SELECT COUNT(*) FROM winners w JOIN auctions a ON a.id=w.auction_id
                WHERE w.winning_bid != (SELECT MAX(amount) FROM bids b WHERE b.auction_id=a.id)
                OR a.winner != (SELECT user_id FROM bids b WHERE b.auction_id=a.id ORDER BY amount DESC,stamp_ns,id LIMIT 1)''').fetchone()[0]
        log_text = (ROOT / f'{args.prefix}-server.log').read_text()
        server_errors = log_text.count('ERROR:')
    summary = dict(exit_code=result.returncode, elapsed_seconds=round(time.time()-started, 1),
                   final_health_status=health, server_stopped=server.poll() is not None,
                   database_integrity=integrity, negative_balances=negative,
                   stock_correct=stock_ok, awards=awards, invalid_awards=invalid_awards,
                   server_errors=server_errors,
                   source_sha256=source_hash,
                   original_database_unchanged=original == (hashlib.sha256((ROOT / 'auction.db').read_bytes()).hexdigest() if (ROOT / 'auction.db').exists() else None))
    summary['passed'] = (result.returncode == 0 and health == 200 and integrity == 'ok'
                         and not negative and stock_ok and not invalid_awards and not server_errors
                         and summary['original_database_unchanged'])
    (ROOT / f'{args.prefix}-summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
