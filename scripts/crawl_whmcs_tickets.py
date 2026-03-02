#!/usr/bin/env python3
"""
Crawl WHMCS conversation list and detail pages, save to source/sample_conversations.json.

Flow:
1. Login to WHMCS (if WHMCS_EMAIL + WHMCS_PASSWORD set)
2. Crawl list: supporttickets.php?filter=1, follow pagination (page=2, page=3...)
3. Collect all conversation detail URLs
4. Visit each detail page, extract subject, description, status, etc.
5. Save to source/sample_conversations.json

Usage:
  python scripts/crawl_whmcs_tickets.py
  python scripts/crawl_whmcs_tickets.py --no-headless   # Show browser
  python scripts/crawl_whmcs_tickets.py --dry-run       # Only crawl list, no details

Env:
  WHMCS_EMAIL     - Login email (required for client area)
  WHMCS_PASSWORD  - Login password
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawlers.whmcs import WHMCSConfig, crawl_whmcs_tickets


def main():
    parser = argparse.ArgumentParser(description="Crawl WHMCS sample conversations to sample_conversations.json")
    parser.add_argument(
        "--base-url",
        default="https://greencloudvps.com/billing/greenvps",
        help="WHMCS base URL",
    )
    parser.add_argument(
        "--list-path",
        default="supporttickets.php?filter=1",
        help="Ticket list path (with query params)",
    )
    parser.add_argument(
        "--login-path",
        default="login.php",
        help="Login page path (e.g. login.php)",
    )
    parser.add_argument(
        "--output",
        default="source/sample_conversations.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only crawl list, print URLs (no detail crawl, no save)",
    )
    args = parser.parse_args()

    config = WHMCSConfig(
        base_url=args.base_url.rstrip("/"),
        list_path=args.list_path,
        login_path=args.login_path,
        username=os.environ.get("WHMCS_USERNAME", os.environ.get("WHMCS_EMAIL")),
        password=os.environ.get("WHMCS_PASSWORD"),
        totp_code=os.environ.get("WHMCS_TOTP"),
        headless=not args.no_headless,
    )

    if not config.username or not config.password:
        print("Warning: WHMCS_USERNAME and WHMCS_PASSWORD not set. Login may fail.")
        print("  export WHMCS_USERNAME=your@email.com")
        print("  export WHMCS_PASSWORD=yourpassword")
        print("  export WHMCS_TOTP=123456  # 2FA code if required")

    print("Crawling WHMCS tickets...")
    tickets = crawl_whmcs_tickets(config)

    if args.dry_run:
        print(f"Dry run: would save {len(tickets)} tickets")
        for t in tickets[:5]:
            print(f"  - {t.get('external_id')}: {t.get('subject', '')[:50]}")
        if len(tickets) > 5:
            print(f"  ... and {len(tickets) - 5} more")
        return

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "source": "whmcs",
        "crawled_from": config.base_url,
        "conversations": [
            {
                "external_id": t["external_id"],
                "subject": t["subject"],
                "description": t.get("description", ""),
                "status": t.get("status", "Open"),
                "priority": t.get("priority"),
                "client_id": t.get("client_id"),
                "email": t.get("email"),
                "name": t.get("name"),
                "detail_url": t.get("detail_url"),
            }
            for t in tickets
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(tickets)} sample conversations to {out_path}")


if __name__ == "__main__":
    main()
