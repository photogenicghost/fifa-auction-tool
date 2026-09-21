"""Auction load test. Runs only against an explicitly named disposable target."""
import argparse
import asyncio
import getpass
import io
import json
import math
import os
import re
import ssl
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx
from openpyxl import Workbook
from websockets.asyncio.client import connect


class Metrics:
    def __init__(self):
        self.samples = defaultdict(list)
        self.errors = defaultdict(int)
        self.accepted = 0
        self.rejected = 0
        self.ws_active = 0
        self.ws_peak = 0
        self.notifications = 0
        self.stage = 'setup'
        self.stages = {}
        self.reconnects_requested = 0
        self.reconnects_completed = 0
        self.reconnect_ms = []

    def bucket(self, stage=None):
        return self.stages.setdefault(stage or self.stage, {'samples': defaultdict(list), 'errors': defaultdict(int)})

    def error(self, name, stage=None):
        self.errors[name] += 1
        self.bucket(stage)['errors'][name] += 1
        if self.errors[name] <= 3:
            print(f'ERROR [{stage or self.stage}]: {name}', flush=True)

    async def request(self, client, method, path, **kwargs):
        start = time.perf_counter()
        stage = self.stage
        try:
            response = await client.request(method, path, **kwargs)
            elapsed = (time.perf_counter() - start) * 1000
            self.samples[path].append(elapsed)
            self.bucket(stage)['samples'][path].append(elapsed)
            if response.status_code >= 500:
                self.error(path + ':server', stage)
            elif response.status_code >= 400 and path != '/bid':
                self.error(path + ':http', stage)
            return response
        except Exception as error:
            self.error(path + ':' + type(error).__name__, stage)
            return None

    @staticmethod
    def percentiles(samples):
        endpoints = {}
        for path, values in samples.items():
            values = sorted(values)
            endpoints[path] = {'requests': len(values), 'p50_ms': round(values[math.ceil(len(values)*.5)-1], 1),
                               'p95_ms': round(values[math.ceil(len(values)*.95)-1], 1), 'max_ms': round(values[-1], 1)}
        return endpoints

    def report(self):
        return {'endpoints': self.percentiles(self.samples), 'errors': dict(self.errors), 'accepted_bids': self.accepted,
                'expected_bid_rejections': self.rejected, 'peak_live_connections': self.ws_peak,
                'update_notifications': self.notifications,
                'planned_reconnects': self.reconnects_requested,
                'completed_reconnects': self.reconnects_completed,
                'reconnect_latency': self.percentiles({'reconnect': self.reconnect_ms}) if self.reconnect_ms else {},
                'stages': {name: {'endpoints': self.percentiles(bucket['samples']), 'errors': dict(bucket['errors'])}
                           for name, bucket in self.stages.items()}}


def fixture(count, quantity=1):
    # Test fixture for the application's workbook parser, not real participant data.
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Total Points'
    sheet.append(['NAME', 'Email', 'Total Points'])
    for index in range(count):
        sheet.append([f'Load Rep {index+1:03}', f'load{index+1:03}@example.com', 1000000])
    sheet = workbook.create_sheet('Auction Prize List')
    sheet.append(['Product Description', 'SKU'])
    for _ in range(quantity):
        sheet.append(['LOAD TEST ONLY', 'LOAD-TEST-ONLY'])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def field(html, name):
    match = re.search(r'name="' + name + r'" value="([^"]+)"', html)
    if not match:
        raise RuntimeError('Missing confirmation field: ' + name)
    return match[1]


async def run(args, password):
    metrics = Metrics()
    started = time.monotonic()
    try:
        await exercise(args, password, metrics)
    except Exception as error:
        metrics.error('runner:' + type(error).__name__)
        print(f'Test failed: {type(error).__name__}: {error}', flush=True)
    report = metrics.report()
    thresholds = []
    for stage, bucket in report['stages'].items():
        if stage == 'setup':
            continue
        for endpoint in ('/bid', '/api/auction', '/api/display'):
            timing = bucket['endpoints'].get(endpoint)
            if timing and timing['p95_ms'] > args.max_p95_ms:
                thresholds.append(f'{stage} participants {endpoint}: p95 {timing["p95_ms"]} ms')
    report.update(target=args.url, users=args.users, seconds_per_stage=args.stage_seconds,
                  elapsed_seconds=round(time.monotonic()-started, 1),
                  latency_failures=thresholds, max_p95_ms=args.max_p95_ms,
                  passed=not metrics.errors and not thresholds,
                  note='Local/client-inclusive timing; not proof of hosted or device capacity.')
    Path(args.report).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['passed'] else 1


async def exercise(args, password, metrics):
    clients = []
    tasks = []
    stop = asyncio.Event()
    sockets = {}
    reconnecting = {}
    forced = set()
    latest = {}
    refresh_events = {}
    tls = ssl.create_default_context()
    stages = args.stages or sorted(set(min(args.users, n) for n in [10, 40, 70, 100]) | {args.users})
    ws_url = args.url.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws'
    async with httpx.AsyncClient(base_url=args.url, timeout=30, follow_redirects=True, verify=tls, trust_env=False) as admin:
        async def admin_request(method, path, **kwargs):
            response = await metrics.request(admin, method, path, **kwargs)
            if response is None or response.status_code != 200:
                raise RuntimeError(f'Admin request failed: {method} {path}')
            return response

        login = await admin_request('POST', '/admin/login', data={'password': password})
        if 'Auction Controls' not in login.text:
            raise RuntimeError('Admin login failed. Check the test service password.')
        if 'Admin: TEST' not in login.text:
            raise RuntimeError('Target must be in TEST mode. No changes made.')
        state = (await admin_request('GET', '/api/display')).json()
        if state['auction'] and state['auction']['status'] in ('READY', 'OPEN', 'ENDED'):
            raise RuntimeError('Close the existing TEST auction before running this test.')
        quantity = max(10, math.ceil(args.stage_seconds / 20) * len(stages) + len(stages) * 3)
        preview = await admin_request('POST', '/admin/import/preview', data={'action': 'replace'},
                                   files={'workbook': ('load-fixture.xlsx', fixture(args.users, quantity))})
        token = field(preview.text, 'token')
        result = await admin_request('POST', '/admin/import/confirm', data={'token': token})
        if 'Import complete' not in result.text:
            raise RuntimeError('Fixture import failed: inspect the test admin page.')
        page = (await admin_request('GET', '/admin')).text
        prize = re.search(r'<option value="(\d+)">LOAD TEST ONLY', page)
        if not prize:
            raise RuntimeError('Test prize not found.')
        auction_id = None
        async def start_round():
            nonlocal auction_id
            await admin_request('POST', '/admin/load', data={'prize_id': prize[1]})
            loaded = (await admin_request('GET', '/api/display')).json()['auction']
            if not loaded or loaded['status'] != 'READY':
                raise RuntimeError('Could not load test auction.')
            auction_id = loaded['id']
            await admin_request('POST', '/admin/start', data={'auction_id': auction_id})
            opened = (await admin_request('GET', '/api/display')).json()['auction']
            if opened['id'] != auction_id or opened['status'] != 'OPEN':
                raise RuntimeError('Could not start test auction.')
        expected_total=0
        expected_wins=0

        async def finish_round():
            nonlocal expected_total,expected_wins
            current=(await admin_request('GET', '/api/display')).json()
            remaining=max(0,(current['auction']['ends_at'] or 0)-current['server_now'])
            await asyncio.sleep(remaining+.1)
            preview=await admin_request('POST', '/admin/close/preview')
            confirmation={'auction_id':field(preview.text,'auction_id'),'bid_id':field(preview.text,'bid_id')}
            await admin_request('POST', '/admin/close',data=confirmation)
            first=(await admin_request('GET', '/api/display')).json()
            await admin_request('POST', '/admin/close',data=confirmation)
            second=(await admin_request('GET', '/api/display')).json()
            if first['auction']!=second['auction'] or first['winner']!=second['winner'] or not first['winner'] or first['auction']['status']!='CLOSED':
                metrics.error('award:invalid_result')
            if first['winner']:
                expected_total+=first['winner']['winning_bid'];expected_wins+=1

        async def updates(client):
            while not stop.is_set():
                ws = None
                try:
                    async with connect(ws_url, open_timeout=20, proxy=None) as ws:
                        sockets[client] = ws
                        metrics.ws_active += 1
                        metrics.ws_peak = max(metrics.ws_peak, metrics.ws_active)
                        if client in reconnecting:
                            metrics.reconnect_ms.append((time.monotonic()-reconnecting.pop(client))*1000)
                            metrics.reconnects_completed += 1
                        refresh_events[client].set()
                        try:
                            async for message in ws:
                                metrics.notifications += 1
                                # Drain notifications promptly, like the browser.
                                refresh_events[client].set()
                        finally:
                            metrics.ws_active -= 1
                            sockets.pop(client, None)
                        if not stop.is_set() and ws not in forced:metrics.error('websocket:closed')
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if not stop.is_set():metrics.error('websocket:' + type(error).__name__)
                planned = ws in forced
                forced.discard(ws)
                await asyncio.sleep(.2 if planned else 3)

        async def refresh(client):
            event = refresh_events[client]
            while not stop.is_set():
                await event.wait()
                event.clear()
                response = await metrics.request(client, 'GET', '/api/auction')
                if response is not None and response.status_code == 200:
                    latest[client] = response.json()

        async def presence(client):
            while not stop.is_set():
                await metrics.request(client, 'POST', '/heartbeat')
                await asyncio.sleep(10)

        async def poll(client):
            while not stop.is_set():
                await asyncio.sleep(args.poll_seconds if client in sockets else 1)
                refresh_events[client].set()

        async def add_user(index):
            client = httpx.AsyncClient(base_url=args.url, timeout=20, follow_redirects=True, verify=tls, trust_env=False)
            clients.append(client)
            refresh_events[client] = asyncio.Event()
            response = await metrics.request(client, 'POST', '/login', data={'email': f'load{index+1:03}@example.com'})
            if response is None or 'bid-form' not in response.text:
                metrics.error('login:invalid_session')
                return
            tasks.extend([asyncio.create_task(updates(client)), asyncio.create_task(presence(client)),
                          asyncio.create_task(poll(client)), asyncio.create_task(refresh(client))])

        async def bid(client, amount):
            response = await metrics.request(client, 'POST', '/bid', data={'amount': amount, 'auction_id': auction_id},
                                             headers={'Accept': 'application/json'})
            if response is None:return
            try:
                data = response.json()
                if response.status_code == 200 and data.get('ok'):metrics.accepted += 1
                elif response.status_code == 400 and 'Another bid arrived first' in data.get('message', ''):metrics.rejected += 1
                elif response.status_code < 500:
                    metrics.error('bid:unexpected_response')
                    if metrics.errors['bid:unexpected_response'] <= 3:
                        print(f'Unexpected bid response: {data.get("message")}', flush=True)
            except ValueError:
                if response.status_code < 500:metrics.error('bid:invalid_json')

        try:
            previous = 0
            next_bid = 1
            for size in stages:
                metrics.stage = str(size)
                print(f'Connecting {size} reps...', flush=True)
                await asyncio.gather(*(add_user(i) for i in range(previous, size)))
                previous = size
                ready_deadline = time.monotonic() + 20
                while metrics.ws_active < size and time.monotonic() < ready_deadline:
                    await asyncio.sleep(.1)
                if metrics.ws_active != size:
                    raise RuntimeError(f'Only {metrics.ws_active}/{size} sockets connected')
                await start_round()
                until = time.monotonic() + args.stage_seconds
                reconnect_at = time.monotonic() + args.reconnect_every if args.reconnect_every else float('inf')
                progress_at = time.monotonic() + 60
                while time.monotonic() < until:
                    current=(await admin_request('GET', '/api/display')).json()
                    if current['auction']['ends_at']-current['server_now']<10:
                        await finish_round()
                        await start_round()
                    if time.monotonic() >= reconnect_at:
                        candidates = list(sockets.items())
                        offset = metrics.reconnects_requested % len(candidates)
                        candidates = candidates[offset:] + candidates[:offset]
                        for client, ws in candidates[:max(1, size//10)]:
                            forced.add(ws)
                            reconnecting[client] = time.monotonic()
                            metrics.reconnects_requested += 1
                            await ws.close()
                        reconnect_at = time.monotonic() + args.reconnect_every
                    before = time.monotonic()
                    await asyncio.gather(*(bid(client, next_bid+i) for i, client in enumerate(clients)))
                    next_bid += len(clients)
                    display = await metrics.request(admin, 'GET', '/api/display')
                    if display is not None:
                        high = display.json()['auction']['high'] or 0
                        if high != next_bid-1:metrics.error('display:wrong_high_bid')
                    convergence = time.monotonic()
                    def converged():
                        return all(latest.get(c, {}).get('auction') and
                                   latest[c]['auction']['id'] == auction_id and
                                   latest[c]['auction']['high'] == next_bid-1 for c in clients)
                    while not converged() and time.monotonic()-convergence < 5:
                        await asyncio.sleep(.05)
                    if not converged():metrics.error('updates:stale_participant_state')
                    if time.monotonic() >= progress_at:
                        print(f'{size} reps: {max(0, round(until-time.monotonic()))}s left; '
                              f'{metrics.ws_active} connected; {sum(metrics.errors.values())} errors', flush=True)
                        Path(args.report).with_suffix('.progress.json').write_text(json.dumps(metrics.report(), indent=2))
                        progress_at = time.monotonic()+60
                    await asyncio.sleep(max(0, 10-(time.monotonic()-before)))
                await finish_round()
                print(f'{size} reps stage complete; {metrics.ws_active} live connections', flush=True)
                Path(args.report).with_suffix('.progress.json').write_text(json.dumps(metrics.report(), indent=2))
            states = await asyncio.gather(*(metrics.request(c, 'GET', '/api/auction') for c in clients))
            states = [r.json() for r in states]
            if sum(1000000-s['balance'] for s in states) != expected_total or sum(len(s['wins']) for s in states) != expected_wins:
                metrics.error('award:wrong_deduction')
            if metrics.reconnects_requested != metrics.reconnects_completed:
                metrics.error('websocket:planned_reconnect_incomplete')
            exported = await admin_request('GET', '/admin/export/results')
            if not exported.content:metrics.error('export:empty')
        finally:
            stop.set()
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(c.aclose() for c in clients))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--confirm-test-host', required=True, help='Exact hostname of the disposable deployment')
    parser.add_argument('--users', type=int, default=100)
    parser.add_argument('--stage-seconds', type=int, default=180)
    parser.add_argument('--report', default='load-test-report.json')
    parser.add_argument('--stages', type=lambda s: [int(n) for n in s.split(',')])
    parser.add_argument('--reconnect-every', type=int, default=60)
    parser.add_argument('--max-p95-ms', type=float, default=2000)
    parser.add_argument('--poll-seconds', type=float, default=5)
    args = parser.parse_args()
    args.url = args.url.rstrip('/')
    host = urlparse(args.url).hostname
    if host == 'fifa-auction-tool.onrender.com' or host != args.confirm_test_host or not 1 <= args.users <= 150:
        parser.error('Use a separate disposable host, confirm its exact hostname, and choose 1–150 users.')
    if urlparse(args.url).scheme not in ('https', 'http') or args.stage_seconds < 1:
        parser.error('Invalid URL or duration.')
    if args.stages and (args.stages != sorted(set(args.stages)) or min(args.stages) < 1 or max(args.stages) != args.users):
        parser.error('Stages must increase, with the last equal to --users.')
    if args.reconnect_every < 0 or args.max_p95_ms <= 0 or args.poll_seconds <= 0:
        parser.error('Invalid reconnect interval or latency threshold.')
    print('This replaces TEST data on the named target with fake participants and prizes.')
    password = os.environ.get('LOAD_TEST_PASSWORD') or getpass.getpass('Test deployment admin password: ')
    raise SystemExit(asyncio.run(run(args, password)))


if __name__ == '__main__':main()
