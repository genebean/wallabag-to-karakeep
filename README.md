# wallabag-to-karakeep

Migrates bookmarks from a [Wallabag](https://wallabag.it/) MariaDB/MySQL
database directly into [Karakeep](https://karakeep.app/) via its REST API.

## What gets migrated

| Wallabag field | Karakeep field |
|---|---|
| `url` | `url` |
| `title` | `title` |
| `is_archived` | `archived` |
| `is_starred` | `favourited` |
| `created_at` | `createdAt` |
| tags | tags |

Article content is **not** migrated — Karakeep will re-crawl each URL when
it processes the import queue.

## Requirements

Python 3.11+ and two packages:

```bash
pip install pymysql requests
```

## Setup

### 1. Get a Karakeep API key

In Karakeep: **Settings → API Keys → New API Key**

### 2. Configure via environment variables (recommended)

```bash
export WB_DB_HOST=127.0.0.1
export WB_DB_PORT=3306
export WB_DB_NAME=wallabag
export WB_DB_USER=wallabag
export WB_DB_PASSWORD=yourpassword

export KK_URL=http://localhost:3000
export KK_API_KEY=your-karakeep-api-key
```

Or pass everything as CLI arguments (see `--help`).

## Usage

```bash
# Dry run first — shows what will be imported without writing anything
python3 migrate.py --dry-run

# Run the migration
python3 migrate.py

# Tag every imported bookmark (handy for tracking the import)
python3 migrate.py --tag imported-from-wallabag

# Slower rate if Karakeep is struggling (default is 0.2s between calls)
python3 migrate.py --delay 0.5
```

## Notes

- Entries are imported oldest-first so your Karakeep timeline reflects the
  original Wallabag order.
- `crawlPriority: low` is set on every bookmark so the re-crawl queue doesn't
  overwhelm the server during import.
- Tag creation is idempotent — existing tags in Karakeep won't be duplicated.
- If an entry fails to import it's logged and the script continues.
