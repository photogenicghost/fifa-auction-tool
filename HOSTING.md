# Render deployment

This project is prepared for a single paid Python web service with a 1 GB persistent disk. Review the live price in Render before creating it.

1. Upload `main.py`, `requirements.txt`, `render.yaml`, `.gitignore`, `.env.example`, `README.md`, and this file to a private GitHub repository. Do not upload `.env`, the runtime folders, databases, logs, or backups.
2. Sign in at https://dashboard.render.com and create a Blueprint from that repository.
3. Render reads `render.yaml`. Set `ADMIN_PASSWORD` to the password from your local `.env`, or choose a new one. Render generates a separate session secret.
4. Review the paid service and disk price and deploy.
5. Open the service's HTTPS URL and `/admin`, and verify password login.
6. The hosted database starts empty in TEST mode. Import your Excel workbook there. The local database is preserved on your computer and is not automatically transferred. If existing auction history must carry over, migrate it before opening the hosted auction.
7. Run a practice auction from an iPad on team Wi-Fi. Verify bidding, live display updates, winner award, balance deduction, and survival of a service restart. Switch to LIVE and import the live workbook only after this passes.
8. Share the service HTTPS URL after validation. Stop using the old temporary tunnel address.

Keep one service instance and one Uvicorn worker: live update connections are held in memory, and SQLite lives on the attached disk. Database and backups use `/var/data` and survive restarts and deploys. Avoid deploying while an auction is open because an attached disk causes a short restart gap.
