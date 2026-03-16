import argparse
import json
import re
import sqlite3
import difflib
import smtplib
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
DB_PATH = SCRIPT_DIR / "tracker.db"


def parse_args():
    parser = argparse.ArgumentParser(description="Website change tracker")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--test-email", action="store_true", help="Send a test email and exit")
    parser.add_argument("--data-dir", type=str, help="Directory for persistent data files (tracker.db)")
    return parser.parse_args()


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Error: Config file not found at {CONFIG_PATH}")
        print("Create a config.json with your email settings and sites to track.")
        sys.exit(1)
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in config file: {e}")
        sys.exit(1)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS snapshots (
            site_name  TEXT PRIMARY KEY,
            content    TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name     TEXT NOT NULL,
            detected_at   TEXT NOT NULL,
            similarity    REAL,
            lines_added   INTEGER,
            lines_removed INTEGER,
            diff_text     TEXT,
            notified      INTEGER DEFAULT 0
        )"""
    )
    conn.commit()
    return conn


def load_snapshot(conn, site_name):
    row = conn.execute(
        "SELECT content FROM snapshots WHERE site_name = ?", (site_name,)
    ).fetchone()
    return row[0] if row else None


def save_snapshot(conn, site_name, content):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO snapshots (site_name, content, updated_at) VALUES (?, ?, ?)",
        (site_name, content, now),
    )
    conn.commit()


def log_change(conn, site_name, diff_info, detection_time, notified):
    conn.execute(
        """INSERT INTO changes (site_name, detected_at, similarity, lines_added, lines_removed, diff_text, notified)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            site_name,
            detection_time,
            diff_info["similarity"],
            diff_info["lines_added"],
            diff_info["lines_removed"],
            diff_info["diff_text"],
            1 if notified else 0,
        ),
    )
    conn.commit()


def fetch_shopify_products(url):
    """Fetch product list from a Shopify collection using the JSON API.
    Returns a stable text representation of product titles and prices."""
    # Convert collection URL to JSON API endpoint
    base_url = url.split("?")[0]
    json_url = f"{base_url}/products.json?limit=250"

    response = requests.get(
        json_url,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()
    data = response.json()

    lines = []
    for p in data["products"]:
        title = p["title"]
        price = p["variants"][0]["price"] if p.get("variants") else "N/A"
        lines.append(f"{title} - ${price}")
    return "\n".join(sorted(lines))


def fetch_text(url, selector=None):
    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    if selector:
        element = soup.select_one(selector)
        if element is None:
            print(f"  Warning: Selector '{selector}' matched nothing, using full page")
        else:
            soup = element

    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def compute_diff(old_text, new_text, site_name):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{site_name} (previous)",
            tofile=f"{site_name} (current)",
            lineterm="",
            n=3,
        )
    )

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    ratio = matcher.ratio()

    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    return {
        "diff_text": "\n".join(diff),
        "similarity": round(ratio * 100, 1),
        "lines_added": added,
        "lines_removed": removed,
    }


def send_notification(email_cfg, site, diff_info, detection_time):
    subject = f"[Website Change] {site['name']} - {detection_time}"

    body = (
        f"Change detected on: {site['name']}\n"
        f"URL: {site['url']}\n"
        f"Time: {detection_time}\n"
        f"Similarity to previous version: {diff_info['similarity']}%\n"
        f"\n"
        f"{'=' * 50}\n"
        f"  CHANGE SUMMARY\n"
        f"{'=' * 50}\n"
        f"Lines added:   {diff_info['lines_added']}\n"
        f"Lines removed: {diff_info['lines_removed']}\n"
        f"\n"
        f"{'=' * 50}\n"
        f"  DETAILED DIFF\n"
        f"{'=' * 50}\n"
        f"\n"
        f"{diff_info['diff_text']}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_cfg["from_addr"]
    msg["To"] = email_cfg["to_addr"]
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls()
        server.login(email_cfg["username"], email_cfg["password"])
        server.sendmail(email_cfg["from_addr"], email_cfg["to_addr"], msg.as_string())

    print(f"  Email sent to {email_cfg['to_addr']}")


def check_sites(config):
    email_cfg = config["email"]
    conn = init_db()

    try:
        for site in config["sites"]:
            name = site["name"]
            url = site["url"]
            selector = site.get("selector")
            print(f"Checking: {name} ({url})")

            try:
                if site.get("type") == "shopify":
                    current_text = fetch_shopify_products(url)
                else:
                    current_text = fetch_text(url, selector)
            except requests.RequestException as e:
                print(f"  Error fetching {url}: {e}")
                continue

            previous_text = load_snapshot(conn, name)

            if previous_text is None:
                save_snapshot(conn, name, current_text)
                print(f"  Initial snapshot saved (first run)")
                continue

            if current_text == previous_text:
                print(f"  No changes detected")
                continue

            detection_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"  Change detected at {detection_time}")

            diff_info = compute_diff(previous_text, current_text, name)

            notified = False
            try:
                send_notification(email_cfg, site, diff_info, detection_time)
                notified = True
            except Exception as e:
                print(f"  Error sending email: {e}")
                print(f"  Snapshot NOT updated (will retry next run)")

            log_change(conn, name, diff_info, detection_time, notified)

            if notified:
                save_snapshot(conn, name, current_text)
                print(f"  Snapshot updated")
    finally:
        conn.close()


def test_email(config):
    email_cfg = config["email"]
    site = config["sites"][0]
    detection_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    diff_info = {
        "diff_text": (
            f"--- {site['name']} (previous)\n"
            f"+++ {site['name']} (current)\n"
            "@@ -1,3 +1,3 @@\n"
            " Some unchanged content\n"
            "-Old line that was removed\n"
            "+New line that was added\n"
            " More unchanged content"
        ),
        "similarity": 85.0,
        "lines_added": 1,
        "lines_removed": 1,
    }

    print(f"Sending test email to {email_cfg['to_addr']}...")
    send_notification(email_cfg, site, diff_info, detection_time)
    print("Test email sent successfully!")


def main():
    global DB_PATH
    args = parse_args()

    if args.data_dir:
        data_dir = Path(args.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        DB_PATH = data_dir / "tracker.db"

    config = load_config()

    if args.test_email:
        test_email(config)
        return

    if args.once:
        print("Running single check...")
        check_sites(config)
        print("Done.")
    else:
        interval = config.get("check_interval_seconds", 3600)
        print(f"Starting continuous monitoring (interval: {interval}s)")
        print("Press Ctrl+C to stop.\n")
        try:
            while True:
                check_sites(config)
                print(f"\nNext check in {interval} seconds...\n")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
