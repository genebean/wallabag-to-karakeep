#!/usr/bin/env python3
"""
Wallabag → Karakeep migration script.

Reads entries directly from a Wallabag MariaDB/MySQL database and imports
them into Karakeep via its REST API. Preserves title, URL, archived status,
favourited status, creation date, and tags.

Article content is NOT migrated — Karakeep will re-crawl each URL.

Requirements:
    pip install pymysql requests

Usage:
    Set environment variables (recommended):
        export WB_DB_HOST=127.0.0.1
        export WB_DB_PORT=3306
        export WB_DB_NAME=wallabag
        export WB_DB_USER=wallabag
        export WB_DB_PASSWORD=yourpassword
        export KK_URL=http://localhost:3000
        export KK_API_KEY=your-karakeep-api-key
        python3 migrate.py

    Or pass everything as arguments:
        python3 migrate.py \\
            --db-host 127.0.0.1 --db-port 3306 \\
            --db-name wallabag --db-user wallabag --db-password yourpassword \\
            --kk-url http://localhost:3000 --kk-api-key your-karakeep-api-key

    Dry run (no writes to Karakeep):
        python3 migrate.py --dry-run

    Add a tag to every imported bookmark (useful for tracking the import):
        python3 migrate.py --tag imported-from-wallabag
"""

import argparse
import os
import sys
import time

import pymysql
import requests


# ---------------------------------------------------------------------------
# CLI / config
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate Wallabag bookmarks to Karakeep"
    )

    # Wallabag DB
    p.add_argument("--db-host", default=os.getenv("WB_DB_HOST", "127.0.0.1"))
    p.add_argument("--db-port", type=int, default=int(os.getenv("WB_DB_PORT", "3306")))
    p.add_argument("--db-name", default=os.getenv("WB_DB_NAME", "wallabag"))
    p.add_argument("--db-user", default=os.getenv("WB_DB_USER", "wallabag"))
    p.add_argument("--db-password", default=os.getenv("WB_DB_PASSWORD", ""))

    # Karakeep
    p.add_argument("--kk-url", default=os.getenv("KK_URL", "http://localhost:3000"))
    p.add_argument("--kk-api-key", default=os.getenv("KK_API_KEY", ""))

    # Options
    p.add_argument(
        "--tag",
        dest="extra_tags",
        action="append",
        default=[],
        metavar="TAG",
        help="Extra tag to add to every bookmark (repeatable)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.2,
        metavar="SECONDS",
        help="Delay between API calls to avoid overwhelming Karakeep (default: 0.2s)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be imported without writing anything",
    )

    return p.parse_args()


# ---------------------------------------------------------------------------
# Wallabag DB
# ---------------------------------------------------------------------------

QUERY = """
    SELECT
        e.id,
        e.title,
        e.url,
        e.is_archived,
        e.is_starred,
        e.created_at,
        GROUP_CONCAT(t.label SEPARATOR '|||') AS tags
    FROM wallabag_entry e
    LEFT JOIN wallabag_entry_tag et ON e.id = et.entry_id
    LEFT JOIN wallabag_tag t ON et.tag_id = t.id
    GROUP BY e.id
    ORDER BY e.created_at ASC
"""


def fetch_entries(args: argparse.Namespace) -> list[dict]:
    conn = pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        db=args.db_name,
        user=args.db_user,
        password=args.db_password,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            return cur.fetchall()


# ---------------------------------------------------------------------------
# Karakeep API
# ---------------------------------------------------------------------------

def kk_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def create_bookmark(base_url: str, api_key: str, entry: dict) -> str | None:
    """Create a bookmark and return its Karakeep ID."""
    payload = {
        "type": "link",
        "url": entry["url"],
        "title": entry["title"],
        "archived": bool(entry["is_archived"]),
        "favourited": bool(entry["is_starred"]),
        "createdAt": entry["created_at"].isoformat() if entry["created_at"] else None,
        "crawlPriority": "low",
    }
    # Drop None values
    payload = {k: v for k, v in payload.items() if v is not None}

    resp = requests.post(
        f"{base_url}/api/v1/bookmarks",
        json=payload,
        headers=kk_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def ensure_tag(base_url: str, api_key: str, name: str, tag_cache: dict) -> str:
    """Return the Karakeep tag ID for a tag name, creating it if needed."""
    if name in tag_cache:
        return tag_cache[name]

    resp = requests.post(
        f"{base_url}/api/v1/tags",
        json={"name": name},
        headers=kk_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    tag_id = resp.json()["id"]
    tag_cache[name] = tag_id
    return tag_id


def attach_tags(
    base_url: str, api_key: str, bookmark_id: str, tag_ids: list[str]
) -> None:
    if not tag_ids:
        return
    resp = requests.post(
        f"{base_url}/api/v1/bookmarks/{bookmark_id}/tags",
        json={"tags": [{"tagId": tid} for tid in tag_ids]},
        headers=kk_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    if not args.db_password:
        sys.exit("Error: database password is required (--db-password or WB_DB_PASSWORD)")
    if not args.dry_run and not args.kk_api_key:
        sys.exit("Error: Karakeep API key is required (--kk-api-key or KK_API_KEY)")

    print("Connecting to Wallabag database…")
    entries = fetch_entries(args)
    print(f"Found {len(entries)} entries to migrate.")

    if args.dry_run:
        print("\n--- DRY RUN --- (no writes will happen)\n")

    tag_cache: dict[str, str] = {}
    ok = 0
    failed = 0

    for i, entry in enumerate(entries, 1):
        tags = [t for t in (entry["tags"] or "").split("|||") if t]
        tags += args.extra_tags

        print(
            f"[{i}/{len(entries)}] {entry['title'] or entry['url']!r}"
            f"  tags={tags or '—'}"
            f"  archived={bool(entry['is_archived'])}"
        )

        if args.dry_run:
            continue

        try:
            bookmark_id = create_bookmark(args.kk_url, args.kk_api_key, entry)

            if tags:
                tag_ids = [
                    ensure_tag(args.kk_url, args.kk_api_key, t, tag_cache)
                    for t in tags
                ]
                attach_tags(args.kk_url, args.kk_api_key, bookmark_id, tag_ids)

            ok += 1
        except requests.HTTPError as exc:
            print(f"  ERROR: {exc} — {exc.response.text[:200]}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed += 1

        time.sleep(args.delay)

    if not args.dry_run:
        print(f"\nDone. {ok} imported, {failed} failed.")


if __name__ == "__main__":
    main()
