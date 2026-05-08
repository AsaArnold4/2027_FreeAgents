"""
Scrape AthleteAgent.com agency-clients pages to build a player→agency lookup.

Why this approach:
- AthleteAgent's per-player /representation page is paywalled (returns "Dummy Agency")
- The /agencies/{id}/clients page is FREE and lists every client publicly
- We invert: scrape every agency's NFL client list, build name→agency map

Usage (typical first run):
    python scripts/scrape_athleteagent.py
    # ~5-15 minutes depending on agency count and network

Re-run scenarios:
    python scripts/scrape_athleteagent.py                  # resumes from cache
    python scripts/scrape_athleteagent.py --refresh-index  # re-pulls agency list
    python scripts/scrape_athleteagent.py --restart        # starts from scratch
    python scripts/scrape_athleteagent.py --max 20         # debug: only first 20

Output:
    data/athleteagent_index.csv      - agency_id, agency_name
    data/athleteagent_reps.csv       - name, slug, agency_id, agency_name, league, aav_aa
    data/athleteagent_progress.json  - resume state (which agency_ids are done)

This script runs once locally then commits the CSVs.
At runtime the Streamlit app reads the prebuilt CSVs.
"""
import re
import csv
import json
import time
import argparse
import requests
from pathlib import Path
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDEX_URL = "https://www.athleteagent.com/agencies"
CLIENTS_URL = "https://www.athleteagent.com/agencies/{id}/clients"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

# Polite scraping: ~1.6 req/s ceiling
SLEEP_BETWEEN = 0.6
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds, doubled each retry


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^\w\s'-]", "", s)
    s = s.replace("'", "").replace(".", "")
    s = re.sub(r"\s+", "-", s.strip())
    return s


def fetch_with_retry(url: str, label: str) -> str | None:
    """GET with up to MAX_RETRIES attempts on transient errors."""
    backoff = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 502, 503, 504):
                print(f"[{label}] HTTP {r.status_code} on attempt {attempt}, sleeping {backoff:.1f}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            print(f"[{label}] HTTP {r.status_code}, giving up")
            return None
        except requests.RequestException as e:
            print(f"[{label}] {type(e).__name__} on attempt {attempt}: {e}")
            time.sleep(backoff)
            backoff *= 2
    print(f"[{label}] exhausted retries")
    return None


def scrape_agency_index() -> dict[int, str]:
    """Scrape /agencies. Returns {agency_id: agency_name}."""
    print(f"[index] GET {INDEX_URL}")
    html = fetch_with_retry(INDEX_URL, "index")
    if not html:
        raise SystemExit("Failed to fetch agencies index — aborting.")
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.select("a[href*='/agencies/']"):
        m = re.search(r"/agencies/(\d+)/clients", a.get("href", ""))
        if m:
            agency_id = int(m.group(1))
            name = a.get_text(strip=True)
            if name:
                out[agency_id] = name
    print(f"[index] Found {len(out)} agencies")
    return out


def scrape_agency_clients(agency_id: int, agency_name: str) -> list[dict]:
    """Scrape one agency's /clients page. Returns list of NFL client dicts (may be empty)."""
    url = CLIENTS_URL.format(id=agency_id)
    html = fetch_with_retry(url, f"agency {agency_id}")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    clients = []
    # Table columns: NAME | SPORT | LEAGUE | AVG ANNUAL PAY
    for tr in soup.select("table tr"):
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[0].find("a")
        if not link:
            continue
        name = link.get_text(strip=True)
        league = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        aav_raw = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        if league != "NFL":
            continue
        try:
            aav = float(aav_raw.replace("$", "").replace(",", "")) if aav_raw else 0.0
        except ValueError:
            aav = 0.0
        clients.append({
            "name": name,
            "slug": slugify(name),
            "agency_id": agency_id,
            "agency_name": agency_name,
            "league": league,
            "aav_aa": aav,
        })
    return clients


def is_likely_nfl_agency(name: str) -> bool:
    """Heuristic skip-list. Conservative — only skip clearly-not-NFL names."""
    name_l = name.lower()
    skip_keywords = [
        "cricket", "fútbol", "futbol", " soccer", "ipl", "tennis", "golf",
        "hockey", "esports", "gaming", "korea", "japan", "brasil", "brazil",
        "argentina", "mexico", "colombia", "chile", "pharoahs", "f1 ",
    ]
    return not any(kw in name_l for kw in skip_keywords)


def load_progress(progress_path: Path) -> dict:
    """Resume state: {'done_ids': [ints], 'reps': [rows]}"""
    if not progress_path.exists():
        return {"done_ids": [], "reps": []}
    try:
        with progress_path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"[resume] {progress_path} unreadable, starting fresh")
        return {"done_ids": [], "reps": []}


def save_progress(progress_path: Path, state: dict):
    tmp = progress_path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f)
    tmp.replace(progress_path)


def write_reps_csv(reps_csv: Path, rows: list[dict]):
    """Write the final reps CSV. Dedupes by (slug, agency_id) so partial runs are clean."""
    seen = set()
    deduped = []
    for r in rows:
        key = (r["slug"], r["agency_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    with reps_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "slug", "agency_id", "agency_name", "league", "aav_aa"])
        w.writeheader()
        w.writerows(deduped)


def main(refresh_index: bool, restart: bool, max_agencies: int | None):
    DATA_DIR.mkdir(exist_ok=True)
    index_csv = DATA_DIR / "athleteagent_index.csv"
    reps_csv = DATA_DIR / "athleteagent_reps.csv"
    progress_path = DATA_DIR / "athleteagent_progress.json"

    # Step 1: agency index — auto-fetch if missing or refresh requested
    if refresh_index or not index_csv.exists():
        idx = scrape_agency_index()
        with index_csv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["agency_id", "agency_name"])
            for aid, aname in sorted(idx.items()):
                w.writerow([aid, aname])
        print(f"[index] Saved → {index_csv}")
    else:
        idx = {}
        with index_csv.open() as f:
            r = csv.DictReader(f)
            for row in r:
                idx[int(row["agency_id"])] = row["agency_name"]
        print(f"[index] Loaded {len(idx)} agencies from cache")

    # Step 2: candidate list (deterministic order so resume works)
    candidates = sorted(
        [(aid, name) for aid, name in idx.items() if is_likely_nfl_agency(name)]
    )
    if max_agencies:
        candidates = candidates[:max_agencies]

    # Step 3: load resume state
    if restart and progress_path.exists():
        progress_path.unlink()
        print("[restart] cleared progress file")
    state = load_progress(progress_path)
    done_ids = set(state["done_ids"])
    all_reps = state["reps"]
    if done_ids:
        print(f"[resume] {len(done_ids)} agencies already done, {len(all_reps)} rows cached")

    todo = [(aid, name) for aid, name in candidates if aid not in done_ids]
    print(f"[scrape] {len(todo)} agencies to hit (of {len(candidates)} candidates)")

    # Step 4: scrape loop with checkpointing every 25 agencies
    started = time.time()
    for i, (aid, aname) in enumerate(todo, 1):
        clients = scrape_agency_clients(aid, aname)
        marker = "✓" if clients else "·"
        eta = ""
        if i > 5:
            elapsed = time.time() - started
            rate = i / elapsed
            remaining = (len(todo) - i) / rate
            eta = f" eta {remaining/60:.1f}m"
        print(f"[{i}/{len(todo)}] {marker} {aname} (id={aid}): {len(clients)} NFL{eta}")
        all_reps.extend(clients)
        done_ids.add(aid)

        if i % 25 == 0 or i == len(todo):
            save_progress(progress_path, {"done_ids": sorted(done_ids), "reps": all_reps})
            write_reps_csv(reps_csv, all_reps)
            print(f"  [checkpoint] {len(all_reps)} rows saved")

        time.sleep(SLEEP_BETWEEN)

    # Final write (handles the empty-todo case too)
    write_reps_csv(reps_csv, all_reps)
    nfl_agencies = len({r["agency_id"] for r in all_reps})
    print(f"\n[done] {len(all_reps)} player-agency rows → {reps_csv}")
    print(f"[done] {nfl_agencies} agencies with NFL clients")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--refresh-index", action="store_true",
                   help="Re-scrape /agencies index (default: use cached if present)")
    p.add_argument("--restart", action="store_true",
                   help="Ignore progress file and start scrape from scratch")
    p.add_argument("--max", type=int, default=None,
                   help="Max agencies to scrape (for debugging)")
    args = p.parse_args()
    main(args.refresh_index, args.restart, args.max)
