"""Disposable load-test server with CPU, memory and event-loop sampling."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import ctypes
import json
import os
from pathlib import Path
import time

import uvicorn
from main import app


def memory_mb():
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                    'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = ctypes.c_void_p
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(process()), ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / 1024**2, 1)
        return None
    try:
        fields = Path('/proc/self/status').read_text().splitlines()
        return round(int(next(s for s in fields if s.startswith('VmRSS:')).split()[1])/1024, 1)
    except (OSError, StopIteration):
        return None


async def monitor(path, stop_file, server):
    path.with_suffix('.pid').write_text(str(os.getpid()))
    started = previous = time.monotonic()
    cpu = time.process_time()
    records = []
    max_lag = 0
    while True:
        tick = time.monotonic()
        await asyncio.sleep(.1)
        if stop_file.exists():
            server.should_exit = True
        now = time.monotonic()
        max_lag = max(max_lag, max(0, now-tick-.1)*1000)
        if now-previous >= 5:
            current_cpu = time.process_time()
            records.append({'seconds': round(now-started, 1),
                            'cpu_cores': round((current_cpu-cpu)/(now-previous), 3),
                            'rss_mb': memory_mb(), 'max_loop_lag_ms': round(max_lag, 1)})
            path.write_text(json.dumps(records, indent=2))
            previous, cpu, max_lag = now, current_cpu, 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--metrics', type=Path, required=True)
    parser.add_argument('--stop-file', type=Path, required=True)
    args = parser.parse_args()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            task = asyncio.create_task(monitor(args.metrics, args.stop_file, server))
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                         workers=1, timeout_keep_alive=30))
    server.run()


if __name__ == '__main__':
    main()
