# Auction load test

## Separate hosting target

Create a new Render Python web service from the same GitHub repository. Name it `fifa-auction-load-test`. Review and accept the cost before creating this additional paid service.

Use the same compute plan as your auction service, one instance, and:

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --timeout-keep-alive 30`
- Health check: `/healthz`
- A new 1 GB disk mounted at `/var/data` (never reuse the real auction disk).
- `DATA_DIR=/var/data`
- `COOKIE_SECURE=true`
- A separate `ADMIN_PASSWORD` and random `SESSION_SECRET` of at least 32 characters.
- `PYTHON_VERSION=3.12.12`

Upload the latest main.py before creating the service. Leave the service in TEST mode. The runner imports fake data automatically and replaces any existing TEST data on this disposable target. It refuses the known real auction hostname.

## Run from this computer

The prepared `.runtime` environment already contains the test dependencies. Else install `requirements-load-test.txt` into your test environment.

```powershell
.\.runtime\Scripts\python.exe .\load_test.py --url https://YOUR-TEST-HOST.onrender.com --confirm-test-host YOUR-TEST-HOST.onrender.com
```

Enter the test service password at the hidden prompt. Do not put passwords into command lines or GitHub.

The default run holds 10, 40, 70 and 100 participants for three minutes each. Each rep maintains a live connection, refreshes state after notifications, polls every five seconds while connected, and sends a heartbeat every 10 seconds. Every 10 seconds all connected reps submit competing bids. This is intentionally more aggressive than typical bidding. Admin display state is checked after each burst. The runner loads and starts 30-second auctions, waits for each deadline, and confirms each award twice to check that it is applied once. It loads the next test unit when another round is needed, then verifies cumulative wins and balance deductions. Deadline waits can extend the configured stage duration.

Report: `load-test-report.json`. It contains request percentiles, errors, expected competing-bid rejections, live connections and notifications. It excludes passwords and cookies.

## Interpret results

Investigate timeouts, HTTP/server errors, lost connections, wrong high bids, or award mismatches. A practical target is a p95 below 2 seconds for bids and state refreshes, with no unexpected errors. This target is a planning criterion, not a guarantee. Inspect Render CPU and memory during the run. Client timings include network transit; notifications are counted but end-to-end display propagation is not separately measured. Real iPad practice remains necessary.

If the runner aborts, check the disposable admin page; it may leave an open TEST auction. Close it before rerunning. Delete the extra service and disk after testing to stop ongoing hosting charges. Do not change the production service based solely on a short local smoke test.


## September 21 capacity checks

Use `python run_isolated_load.py --stages 70,100 --stage-seconds 1200 --prefix capacity-sustained` for 20 minutes at each level on a disposable local server. The existing database is never used as the load target. Application and test dependencies must be installed in the Python environment running the script.

The runner now instruments admin requests, reports endpoint latency separately for each participant count, coalesces refreshes like the browser, reconnects 10% of participants once per minute, checks that every participant sees the final high bid after each burst, and fails on unexpected errors or bid/state p95 above 2 seconds. The local wrapper also checks database integrity, stock, winning bids, server logs, and the original database hash. Resource samples report server CPU cores used, memory, and event-loop lag. Windows test servers shut down through a temporary stop file before inspecting their databases.

For a disposable hosted target, use `python load_test.py --url https://TEST-HOST --confirm-test-host TEST-HOST --users 100 --stages 70,100 --stage-seconds 1200 --report hosted-capacity.json`. Never substitute the real auction URL: the fixture replaces TEST data. Configure the same CPU, RAM, disk, Python version, single worker, and 30-second keep-alive as production. A local pass alone is not production approval.

Regression checks: `python websocket_diagnostic.py`, `python bid_batch_check.py`, `python timer_check.py`, `python image_check.py`, and `python restart_check.py`. `diagnostic_check.py` and `migration_check.py` additionally inspect the existing local database.
