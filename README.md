# Web App Tracker

A Python-based website change tracker that monitors pages for content changes and sends email notifications with a detailed diff of what changed.

## Features

- Monitor multiple websites simultaneously
- Optional CSS selectors to track specific sections of a page
- Email notifications with exact change time, summary, and unified diff
- SQLite database for storing snapshots and change history
- Continuous monitoring or single-check mode

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy the example config and fill in your details:

```bash
cp config.example.json config.json
```

Edit `config.json`:

| Field | Description |
|---|---|
| `check_interval_seconds` | How often to check (in seconds). `3600` = 1 hour, `60` = 1 minute |
| `email.smtp_server` | SMTP server. For Gmail: `smtp.gmail.com` |
| `email.smtp_port` | SMTP port. For Gmail with TLS: `587` |
| `email.username` | Your email login |
| `email.password` | Your email password. For Gmail, use an [App Password](https://myaccount.google.com/apppasswords) |
| `email.from_addr` | Sender address |
| `email.to_addr` | Recipient address |
| `sites` | Array of sites to monitor (see below) |

#### Site configuration

Each site in the `sites` array has:

```json
{
  "name": "Friendly Name",
  "url": "https://example.com/page",
  "selector": null
}
```

- **`name`** - A label used in email subjects and the database
- **`url`** - The page URL to monitor
- **`selector`** - CSS selector to monitor a specific part of the page. Use `null` to monitor the entire page

#### Selector examples

| Goal | Selector |
|---|---|
| Entire page | `null` |
| Main content area | `"#content"` or `".main-content"` |
| A specific div by ID | `"#product-description"` |
| First element matching a class | `".pricing-table"` |

Using a selector reduces false positives from headers, footers, ads, and other dynamic elements that change frequently.

## Usage

### Single check

Run once and exit. Good for cron jobs or Task Scheduler:

```bash
python tracker.py --once
```

### Continuous monitoring

Run in a loop, checking at the configured interval:

```bash
python tracker.py
```

### Custom data directory

Store `tracker.db` in a separate directory (recommended for server deployments to protect data from code deploys):

```bash
python tracker.py --data-dir /opt/hobbies/data/website-tracker
```

### Test email

Send a test notification to verify your email settings:

```bash
python tracker.py --test-email
```

## Example email

When a change is detected, you'll receive an email like this:

**Subject:** `[Website Change] Python Downloads - 2026-03-15 14:30:07`

```
Change detected on: Python Downloads
URL: https://www.python.org/downloads/
Time: 2026-03-15 14:30:07
Similarity to previous version: 94.2%

==================================================
  CHANGE SUMMARY
==================================================
Lines added:   3
Lines removed: 2

==================================================
  DETAILED DIFF
==================================================

--- Python Downloads (previous)
+++ Python Downloads (current)
@@ -1,5 +1,6 @@
 Active Python Releases
-Python 3.13.2     Jan. 4, 2025    Download
+Python 3.13.3     April 8, 2025   Download
+Python 3.13.2     Jan. 4, 2025    Download
 Python 3.12.9     Feb. 4, 2025    Download
-Looking for a specific release?
+Looking for a specific release? See the full list.
 Pre-releases
```

## How it works

1. Fetches each page and extracts visible text (strips HTML, scripts, styles)
2. Compares extracted text against the last saved snapshot in the SQLite database
3. If changed, computes a unified diff and sends an email with:
   - The exact detection time in the subject line
   - Similarity percentage
   - Lines added/removed count
   - Full diff showing exactly what changed
4. Saves the new snapshot after a successful notification
5. If the email fails, the snapshot is **not** updated so the change is retried next run

### First run

On the first run, the tracker saves a baseline snapshot for each site without sending any emails.

## Database

All data is stored in `tracker.db` (SQLite), auto-created on first run. By default it's created in the script directory, or in the `--data-dir` path if specified:

- **`snapshots`** - Latest content for each site
- **`changes`** - History of every detected change with diffs

Query change history:

```bash
sqlite3 tracker.db "SELECT site_name, detected_at, similarity, lines_added, lines_removed FROM changes ORDER BY detected_at DESC LIMIT 10;"
```

## Deployment

### systemd (Linux)

Create `/etc/systemd/system/web-app-tracker.service`:

```ini
[Unit]
Description=Web App Tracker
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/hobbies/services/web-app-tracker
ExecStart=/opt/hobbies/services/web-app-tracker/venv/bin/python -u tracker.py --data-dir /opt/hobbies/data/website-tracker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
systemctl daemon-reload
systemctl enable web-app-tracker
systemctl start web-app-tracker
```

View logs:

```bash
journalctl -u web-app-tracker -f
```

### Windows Task Scheduler

Schedule `python tracker.py --once` to run at your desired interval.
