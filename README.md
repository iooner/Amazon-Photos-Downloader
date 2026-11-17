# Amazon Photos Downloader

Read-only Python script to bulk-download your entire Amazon Photos library (originals, RAW included) with resume, MD5 verification and per-year folders.

## Why

I really like Amazon Photos: unlimited photo storage with Prime, decent apps, it just works. But getting your photos *back out* is a real pain. There is no "export everything" button, the web UI caps selections at a few thousand files, downloads come as ZIPs you have to babysit, and the desktop app couldn't even see my library (wrong region, no way to change it). With 76,000 files, doing it by hand was not an option.

So this script does it for you: it lists your whole library through the same API the website uses and downloads every original file, safely and resumably.

## Features

- **Read-only**: only lists and downloads. No delete, no upload, no change to your account.
- **Originals**: files come back byte-for-byte as uploaded (JPEG, HEIC, RAW, videos), checked by MD5.
- **Resumable**: stop with `Ctrl+C` anytime, rerun the same command; completed files are skipped.
- **Organized**: one folder per year (date taken, falls back to upload date).
- **Inventory cache**: the library listing is saved, so restarts begin downloading immediately.
- **Logs**: one-line-per-session history (`_history.log`) plus a per-file report (`_report.csv`).

## Requirements

- macOS or Linux with Python 3.9+
- An Amazon Photos account (tested on amazon.fr, other stores via `--tld`)

## Setup

The examples below use **amazon.fr**. Replace `fr` with your own store (`de`, `it`, `co.uk`, `com`…), see [Other Amazon stores](#other-amazon-stores).

1. Find which store holds your photos: open `https://www.amazon.<tld>/photos` and check that your library shows up. It is the store where you signed up, not necessarily your country.
2. Open DevTools → **Application** → **Cookies** → `https://www.amazon.<tld>` and copy the values of `session-id`, `ubid-acb<tld>` and `at-acb<tld>` (for example `ubid-acbfr`, `at-acbfr`; amazon.co.uk and amazon.com use different names, see the table below).
3. Copy `cookies.example.json` to `cookies.json`, rename the keys if your store isn't `fr`, and paste the values in.
4. Run with `--tld <tld>` if your store isn't `fr`.

> ⚠️ These cookies give full access to your Amazon account. Never share or commit `cookies.json` (it is git-ignored). Log out of Amazon when you are done to invalidate them.

## Usage

```bash
./run.sh --tld fr --list                                   # count only, downloads nothing
./run.sh --tld fr --out "/Volumes/MyDisk/AmazonPhotos"     # download everything
./run.sh --tld fr --out "/Volumes/MyDisk/AmazonPhotos" --workers 16
```

`run.sh` creates a local virtualenv on first run, installs `requests`, and keeps the Mac awake with `caffeinate`.

| Option | Description |
|---|---|
| `--out DIR` | Destination folder (default `AmazonPhotos`) |
| `--tld TLD` | Amazon store holding your photos: `fr`, `de`, `co.uk`, `com`… (default `fr`) |
| `--workers N` | Parallel downloads (default 4). Amazon throttles each connection, so 8–16 helps. |
| `--refresh` | Rebuild the inventory (to pick up new photos) |
| `--verbose` | Also print files already present |
| `--list` | Inventory only |

## How it works

### 1. Authentication
The script reuses your browser session: the three cookies (`session-id`, `ubid-acbfr`, `at-acbfr`) are sent with each request, along with the `x-amzn-sessionid` header the website uses. No password is ever handled. When the cookies expire, the API returns `401` and the script stops with a message telling you to copy fresh ones.

### 2. Account root
A first call to `GET /drive/v1/nodes?filters=isRoot:true` returns the root node of your drive, which holds your `ownerId`. That ID is required for downloads.

### 3. Inventory
Files are listed with `GET /drive/v1/search` and the filter `type:(PHOTOS OR VIDEOS)`. The API returns at most 200 items per page, and very large result sets are unreliable to paginate. The script therefore queries **one month at a time** by upload date (`createdDate:["2020-09-01T00:00:00.000Z" TO "2020-09-30T23:59:59.999Z"]`), from 2005 to today. Then it runs two catch-all queries for items dated before 2005 or in the future.

- A short pause between pages avoids rate limiting (`HTTP 429`).
- Results are deduplicated by node ID.
- At the end, the script prints Amazon's own total for comparison.
- The inventory is saved to `_inventory.json` in the output folder. Later runs reuse it instantly; `--refresh` rebuilds it.

### 4. Download
Each file is fetched with `GET /drive/v1/nodes/{id}/contentRedirection?querySuffix=?download=true&ownerId=…`. Amazon then redirects to its content servers (`content-eu.drive.amazonaws.com`), which serve the **original** file.

- Data is streamed to `name.part` while an MD5 hash is computed on the fly.
- The hash is compared with the MD5 Amazon stores for that file. Only if they match is the `.part` renamed to its final name, so a file with a final name is always complete.
- Several files download in parallel (`--workers`). Amazon throttles each connection to a few hundred KB/s, so parallelism is what gives speed.

### 5. Layout
```
AmazonPhotos/
├── 2006/
│   └── a1b2c3d4_IMG_0001.JPG
├── 2018/
│   └── 5HF2vtab_IMG_20180606_144703.jpg
├── unknown/              # no date available
├── _inventory.json      # cached library listing
├── _report.csv          # per-file status of the last run
└── _history.log       # one line per run
```
- Folders use the year the photo was **taken** (`contentDate` from EXIF), or the upload year as a fallback.
- Filenames are prefixed with the first 8 characters of Amazon's node ID, so two `IMG_0001.JPG` from different cameras never overwrite each other.

### 6. Resume and verification
On each run, a file is skipped if it already exists **with the exact size Amazon reports**. Leftover `.part` files are simply re-downloaded from scratch. To confirm a complete backup, run the script once more after it finishes: every file should be `skip` (already present) and `failed` should be `0`.

### 7. Error handling
- `429`, `5xx` and timeouts are retried with exponential backoff, up to 2 minutes between attempts.
- A failed file is retried 5 times, then recorded as a failure in the report and picked up on the next run.
- `Ctrl+C` stops cleanly and still writes the session line to `_history.log`.

### Example `_history.log`
```
2026-11-17 14:52: 414 files downloaded, 540/76034 present, 0 failed → to resume
2026-11-18 09:12: 75494 files downloaded, 76034/76034 present, 0 failed → COMPLETE
```

## Other Amazon stores

Use `--tld` with the store where your photos live (default `fr`):

```bash
./run.sh --tld de --out "/Volumes/MyDisk/AmazonPhotos"
```

Copy the matching cookies into `cookies.json`:

| Store | `--tld` | Cookies |
|---|---|---|
| amazon.fr / .de / .it / .es / .nl… | `fr`, `de`, `it`, `es`, `nl` | `session-id`, `ubid-acb<tld>`, `at-acb<tld>` |
| amazon.co.uk | `co.uk` | `session-id`, `ubid-acbuk`, `at-acbuk` |
| amazon.com | `com` | `session-id`, `ubid-main`, `at-main` |

Only amazon.fr has been tested. Your photos are tied to the store where you signed up, not to where you live: a Belgian account may well be on amazon.fr.

## Notes

- Uses Amazon's undocumented web API; it may break without notice.
- HTTP 429 / 502 / timeouts are retried automatically. If you see many 429s, lower `--workers`.
- Hidden photos and items shared with you through groups may not be returned by the search.

## Disclaimer

Not affiliated with Amazon. Use at your own risk, for your own data only.
