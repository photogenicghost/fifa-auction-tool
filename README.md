# FIFA Auction Tool Phase 2

1. Open PowerShell in this folder.
2. Run `powershell -ExecutionPolicy Bypass -File .\start.ps1` using the prepared `.runtime` environment.
3. The admin password is the `ADMIN_PASSWORD` value in `.env`. Edit it and restart the server to change it. Keep this file private.
4. `SESSION_SECRET` must be at least 32 characters. Changing it signs everyone out.
5. `COOKIE_SECURE=true` is configured for the public HTTPS link. For local HTTP login only, set it to `false` and restart.
6. To create a new temporary public link, run `.\cloudflared.exe tunnel --url http://127.0.0.1:8000 --protocol http2 --no-autoupdate`. The URL prints in the terminal.

The app and tunnel are currently running in the background. Keep this computer awake and connected. Restarting the tunnel creates a new URL. Server and tunnel logs are in this folder. This is temporary sharing, not permanent hosting.

The application starts in TEST mode. The Admin page imports the Total Points and Auction Prize List sheets from the workbook. Replace clears only the selected mode. Append updates matching participant emails and matching prize SKUs. Participants log in using their registered email on the honor system.
