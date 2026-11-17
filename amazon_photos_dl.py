#!/usr/bin/env python3
"""
Download every photo and video from Amazon Photos to a local folder.

READ-ONLY: the script only lists and downloads.
No deletion, no upload, no change to the account.

Usage:
  python3 amazon_photos_dl.py --list                       # count only, downloads nothing
  python3 amazon_photos_dl.py --out "/Volumes/MyDisk/AmazonPhotos"
  python3 amazon_photos_dl.py --tld de --out ...           # other store (amazon.de, .it, .es…)

Resume: rerun the same command; files already complete are skipped.
Requirements: pip3 install requests  +  a cookies.json file next to the script.
"""
import argparse, csv, hashlib, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

TLD = "fr"      # overridden by --tld
DRIVE = ""
BASE = {"asset": "ALL", "tempLink": "false", "resourceVersion": "V2", "ContentType": "JSON"}
PAGE = 200
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def cookie_names():
    """amazon.fr → ubid-acbfr/at-acbfr ; amazon.co.uk → …acbuk ; amazon.com → ubid-main/at-main"""
    if TLD == "com":
        return "ubid-main", "at-main"
    sfx = TLD.split(".")[-1]
    return f"ubid-acb{sfx}", f"at-acb{sfx}"


def session(cookie_file: Path) -> requests.Session:
    c = json.loads(cookie_file.read_text())
    for k in ("session-id", *cookie_names()):
        if not c.get(k):
            sys.exit(f"Missing cookie in {cookie_file}: {k}")
    s = requests.Session()
    s.headers.update({"user-agent": UA, "x-amzn-sessionid": c["session-id"]})
    for k, v in c.items():
        s.cookies.set(k, v, domain=f".amazon.{TLD}")
    return s


def get(s, url, params, tries=8, **kw):
    for i in range(tries):
        try:
            r = s.get(url, params=params, timeout=120, **kw)
            if r.status_code == 401:
                sys.exit(f"401: cookies expired. Log in again at https://www.amazon.{TLD}/photos and copy fresh ones.")
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.RequestException,) as e:
            wait = min(2 ** i, 120)
            print(f"  ! {e} → retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Giving up: {url}")


def search(s, filters):
    """List every node matching the filter (paginated)."""
    out, offset = [], 0
    while True:
        p = BASE | {"limit": PAGE, "offset": offset, "filters": filters,
                    "lowResThumbnail": "true", "searchContext": "customer",
                    "sort": "['createdDate ASC']"}
        j = get(s, f"{DRIVE}/search", p).json()
        time.sleep(0.5)  # be gentle with the API (avoids 429s)
        data = j.get("data", [])
        out += data
        offset += PAGE
        if not data or offset >= j.get("count", 0):
            return out, j.get("count", 0)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def iso_end(d):
    return (d - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.999Z")


def list_all(s):
    """Query one upload month at a time to stay clear of Amazon's pagination limits."""
    nodes, start = {}, datetime(2005, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    while start < now:
        end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
        f = f'type:(PHOTOS OR VIDEOS) AND createdDate:["{iso(start)}" TO "{iso_end(end)}"]'
        items, count = search(s, f)
        if count:
            print(f"  {start:%Y-%m}: {count} items")
            if len(items) < count:
                print(f"  ⚠ {start:%Y-%m}: only {len(items)}/{count} listed", file=sys.stderr)
        for n in items:
            nodes[n["id"]] = n
        start = end
    # safety nets: items dated out of range (before 2005 or in the future)
    for label, rng in (("before 2005", f'[* TO "{iso_end(datetime(2005,1,1,tzinfo=timezone.utc))}"]'),
                       ("after today", f'["{iso(start)}" TO *]')):
        items, count = search(s, f"type:(PHOTOS OR VIDEOS) AND createdDate:{rng}")
        if count:
            print(f"  {label}: {count} items")
        for n in items:
            nodes[n["id"]] = n
    _, total = search_count(s, "type:(PHOTOS OR VIDEOS)")
    print(f"  (Amazon search reports {total} items in total)")
    return list(nodes.values())


def search_count(s, filters):
    p = BASE | {"limit": 1, "offset": 0, "filters": filters, "searchContext": "customer"}
    j = get(s, f"{DRIVE}/search", p).json()
    return None, j.get("count", 0)


def target_path(out: Path, n) -> Path:
    cp = n.get("contentProperties", {}) or {}
    date = cp.get("contentDate") or n.get("createdDate") or ""
    year = date[:4] if date[:4].isdigit() else "unknown"
    name = n.get("name") or n["id"]
    return out / year / f"{n['id'][:8]}_{name}"   # id prefix = no filename collisions


def download(s, owner, n, out: Path):
    dest = target_path(out, n)
    size = (n.get("contentProperties") or {}).get("size")
    md5 = (n.get("contentProperties") or {}).get("md5")
    if dest.exists() and (size is None or dest.stat().st_size == size):
        return "skip", dest, 0, 0.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    url = f"{DRIVE}/nodes/{n['id']}/contentRedirection"
    for attempt in range(5):
        try:
            t = time.time()
            h = hashlib.md5()
            with get(s, url, {"querySuffix": "?download=true", "ownerId": owner}, stream=True) as r, open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk); h.update(chunk)
            if md5 and h.hexdigest() != md5:
                raise IOError("MD5 mismatch")
            tmp.rename(dest)
            return "ok", dest, dest.stat().st_size, time.time() - t
        except Exception as e:
            err = e
            time.sleep(2 ** attempt)
    tmp.unlink(missing_ok=True)
    return f"FAILED: {err}", dest, 0, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="AmazonPhotos", help="destination folder (default AmazonPhotos)")
    ap.add_argument("--tld", default="fr", help="Amazon store holding your photos: fr, de, it, es, co.uk, com… (default fr)")
    ap.add_argument("--cookies", default=str(Path(__file__).with_name("cookies.json")))
    ap.add_argument("--workers", type=int, default=4, help="parallel downloads (default 4)")
    ap.add_argument("--list", action="store_true", help="inventory only, download nothing")
    ap.add_argument("--verbose", action="store_true", help="also print files already present")
    ap.add_argument("--refresh", action="store_true", help="rebuild the inventory (to pick up new photos)")
    a = ap.parse_args()

    global TLD, DRIVE
    TLD = a.tld.strip(".").lower()
    DRIVE = f"https://www.amazon.{TLD}/drive/v1"

    s = session(Path(a.cookies))
    owner = get(s, f"{DRIVE}/nodes", BASE | {"filters": "isRoot:true"}).json()["data"][0]["ownerId"]
    out = Path(a.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
    inv = out / "_inventory.json"
    if not inv.exists() and (old := out / "_inventaire.json").exists():
        old.rename(inv)   # legacy name
    if inv.exists() and not a.refresh and not a.list:
        nodes = json.loads(inv.read_text())
        print(f"Connected to amazon.{TLD}. Inventory loaded from cache ({len(nodes)} files). Use --refresh to rebuild it.")
    else:
        print(f"Connected to amazon.{TLD}. Building inventory…")
        nodes = list_all(s)
        if not a.list:
            inv.write_text(json.dumps(nodes))
    photos = sum(n.get("kind") == "FILE" and "image" in (n.get("contentProperties", {}).get("contentType") or "") for n in nodes)
    print(f"\nTotal: {len(nodes)} files (≈{photos} photos, {len(nodes) - photos} videos/other)")
    if a.list:
        return

    report = out / "_report.csv"          # per-file details
    logf = out / "_history.log"         # one line per run
    if logf.exists() and (h := logf.read_text().strip().splitlines()):
        print(f"Last run: {h[-1]}")

    t0 = datetime.now()
    stats = {"ok": 0, "skip": 0, "failed": 0}
    ex = ThreadPoolExecutor(a.workers)
    try:
        with open(report, "w", newline="") as fr:
            w = csv.writer(fr); w.writerow(["id", "status", "file"])
            futs = {ex.submit(download, s, owner, n, out): n for n in nodes}
            total_bytes, t_start = 0, time.time()
            for i, fu in enumerate(as_completed(futs), 1):
                st, dest, nb, dt = fu.result()
                stats[st if st in stats else "failed"] += 1
                total_bytes += nb
                w.writerow([futs[fu]["id"], st, dest])
                if st != "skip" or a.verbose:
                    speed = f"{nb / dt / 1e6:5.1f} MB/s" if dt else "         "
                    print(f"  [{i}/{len(nodes)}] {st:<4} {nb / 1e6:7.1f} MB  {speed}  {dest.parent.name}/{dest.name}", flush=True)
                if i % 50 == 0 or i == len(nodes):
                    el = time.time() - t_start
                    done_now = stats["ok"] + stats["failed"]
                    rate = done_now / el * 60 if el else 0
                    left = len(nodes) - i
                    eta = f"{left / rate / 60:.1f} h" if rate else "?"
                    print(f"  ── {i}/{len(nodes)} | ok={stats['ok']} skip={stats['skip']} failed={stats['failed']} | "
                          f"{total_bytes / 1e9:.2f} GB, {total_bytes / el / 1e6:.1f} MB/s, {rate:.0f} files/min | ETA ≈ {eta}", flush=True)
                    fr.flush()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)   # Ctrl+C: stop right away
        present = stats["ok"] + stats["skip"]
        state = "COMPLETE" if present == len(nodes) else "to resume"
        with open(logf, "a") as fl:
            fl.write(f"{t0:%Y-%m-%d %H:%M}: {stats['ok']} files downloaded, "
                     f"{present}/{len(nodes)} present, {stats['failed']} failed → {state}\n")
    print(f"\nDone. History: {logf}  |  Details: {report}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Rerun the same command to resume.")
