# FIFA Auction Tool Phase 2

1. Open PowerShell in this folder.
2. Run `powershell -ExecutionPolicy Bypass -File .\start.ps1` using the prepared `.runtime` environment.
3. The admin password is the `ADMIN_PASSWORD` value in `.env`. Edit it and restart the server to change it. Keep this file private.
4. `SESSION_SECRET` must be at least 32 characters. Changing it signs everyone out.
5. `COOKIE_SECURE=true` is configured for the public HTTPS link. For local HTTP login only, set it to `false` and restart.
6. To create a new temporary public link, run `.\cloudflared.exe tunnel --url http://127.0.0.1:8000 --protocol http2 --no-autoupdate`. The URL prints in the terminal.

The app must be started before using the local address or tunnel. Check `http://127.0.0.1:8000/healthz` for `{"ok":true}`. Keep this computer awake and connected. Restarting the tunnel creates a new URL. Server and tunnel logs are in this folder. This is temporary sharing, not permanent hosting.

The application starts in TEST mode. The Admin page imports the Total Points and Auction Prize List sheets from the workbook. Replace clears only the selected mode. Append updates names and prize images for matching emails/SKUs while preserving existing balances and stock. New entries use workbook points and quantities. Use balance editing for corrections. Participants log in using their registered email on the honor system.

The Link to Image column accepts public HTTP/HTTPS image URLs (including WebP, query strings, and URLs without file extensions), Excel hyperlinks, literal HYPERLINK/IMAGE formulas, and base64 raster image links. Images appear in Prizes, Auction, and Display. Links must point to an image rather than a product page; private or embedding-blocked images can remain unavailable. Missing SKUs receive a stable identifier based on the product description so those prizes are imported too. Keep those descriptions consistent across append imports. Each workbook prize row still counts as one unit. Restart the app after installing this update, then use Append to add missing prizes and update existing image links without resetting balances or stock.

Image import regression checks: `.\.runtime\Scripts\python.exe image_check.py` (uses a temporary database).


Participant sessions are invalidated when a roster is replaced, TEST data is reset, or Test/Live mode changes. Participants must sign in again. Append imports and normal server restarts preserve valid sessions. Installing the session fix also requires existing participants to sign in once again.

Run isolated regression checks with `.\.runtime\Scripts\python.exe diagnostic_check.py`. These use temporary databases and a temporary local server, and write `diagnostic-results.json` and `diagnostic-load-report.json`. Test dependencies are in `requirements-load-test.txt`.


Each auction uses two separate admin actions:

1. Choose a prize from the dropdown and click **Load Item**. The prize appears on participant and display screens, but bidding stays disabled.
2. Click **Start 30-Second Auction** to open bidding and start the countdown.
3. At zero, the server rejects further bids. To stop sooner, click **End Bidding Early** and confirm. This stops bidding immediately and keeps the highest bid. Click **Review and Award**, then confirm to deduct points and one prize unit. No-bid auctions do not consume stock.
4. Use **Cancel Auction** to discard a loaded or started auction without deducting points or stock. Award or cancel before loading another item.

Refreshing or reconnecting cannot restart the timer. The server checks bids against the saved deadline when processing them; allow for network delays near zero. Countdown displays use server time rather than the device's wall clock.

On upgrade, any old open auction without a saved deadline stops accepting bids and can be reviewed/awarded or cancelled. Deploy between auctions. Existing history is preserved; the deadline column is added automatically at startup.

Timer regression checks: `.\.runtime\Scripts\python.exe timer_check.py`.


Capacity update: notifications are bounded and coalesced so disconnected or slow clients cannot fail an accepted bid or award. Simultaneous bids are checked in arrival order in small transaction batches; each bid still checks the saved deadline and no success response is sent before commit. The server retains an idle SQLite connection while running and indexes high-bid lookups. Participants and display screens refresh through WebSockets, with five-second connected polling and one-second fallback while disconnected; the countdown still ticks locally every 100 ms. Use a single worker with `--timeout-keep-alive 30` for hosted startup. See LOAD_TEST.md for regression and sustained load checks.
