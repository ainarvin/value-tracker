# Value Tracker

Margin-of-safety, risk-to-reward and dividend dashboard for a long-term watchlist.

- **On this PC (live):** double-click `start_tracker.bat`, or run `python value_tracker.py`, then open http://127.0.0.1:8060
- **Anywhere, PC off (snapshot):** `https://<your-github-username>.github.io/value-tracker/`. GitHub refreshes it about every 30 minutes on weekdays.

The page shows where its data came from ("Live from this PC" or "Snapshot") and warns you if a weekday snapshot is more than 2 hours old.

## Changing stocks or assumptions
Edit `valuation.json` (on the PC, or on github.com, which works from your phone). Each entry has its own growth rates, discount rate, fair P/E and bad case. A change pushed to GitHub rebuilds the snapshot within a couple of minutes.

## One-time GitHub setup
1. On github.com click **New repository**, name it `value-tracker`, choose **Public**, and don't add a README. Free Pages needs a public repo.
2. In this folder, run:
   ```
   git init -b main
   git add .
   git commit -m "Value tracker"
   git remote add origin https://github.com/<your-username>/value-tracker.git
   git push -u origin main
   ```
3. On the repo go to **Settings → Pages → Build and deployment**, set Source to **Deploy from a branch**, Branch to `main`, Folder to `/docs`, and **Save**.
4. Go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, and **Save**.
5. Go to **Actions → Refresh stock snapshot → Run workflow** to run it once now. After that it runs on its own.

## How the refresh works
`.github/workflows/update.yml` runs every 30 min Mon–Fri (00:00–22:00 UTC, which covers Bursa, Europe and US hours) and once on Saturday. It runs `python value_tracker.py --export docs` and commits the new `docs/data.json`. If Yahoo blocks a run, that run fails without overwriting anything, so the last good snapshot stays up and the next run normally recovers. GitHub may start runs 5–30 min late.

Not investment advice. Intrinsic values come from the assumptions in `valuation.json`.
