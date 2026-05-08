# 2027 NFL Free Agent Tracker

A dashboard for free-agent prep: live valuation, representation, and team-fit context for every meaningful upcoming 2027 NFL free agent (876 players).

## What it does

- Lists every 2027 NFL free agent with prior contract data, age, and snap share
- Identifies which agency represents each player (~80% coverage from public sources, no fabricated data)
- Projects 2027 contract value as a **range** grounded in 2,800+ historical NFL contracts (inflation-adjusted, rookie-scale filtered)
- Suggests team fits based on which teams have expiring contracts at the same position
- Highlights VaynerSports clients with copper accents throughout the UI

## Quick start (local)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501

## Deploy to Streamlit Cloud (5 min, free)

1. Create a new public GitHub repo, e.g. `nfl-fa-2027`
2. Upload all files in this folder to the repo (drag-and-drop in GitHub web UI works fine)
3. Go to https://share.streamlit.io and sign in with GitHub
4. Click **New app** → select your repo, branch `main`, main file `app.py`
5. Click **Deploy** — wait ~2 minutes
6. You'll get a public URL like `https://nfl-fa-2027.streamlit.app`

The app reads pre-built CSVs at runtime — no scraping or computation during user requests, so cold-start is fast.

## Data architecture

```
data/
├── fa_2027_raw.csv               OTC export, untouched (1409 rows)
├── fa_2027_clean.csv             Cleaned + filtered to meaningful FAs (876 rows)
├── fa_2027_projected.csv         FAs + projection ranges + named comps (used by app)
├── nfl_players_by_agency.csv     Source: scraped agency client lists
├── agency_reps.csv               Cleaned player→agency lookup with normalized join key
└── NFL_Contracts.csv             51K historical NFL contracts (comp pool source)
```

## Refreshing the data

The app reads pre-built CSVs at runtime. To refresh:

```bash
# 1. Re-clean the OTC CSV (after replacing fa_2027_raw.csv with a fresh export)
python scripts/clean_fa_csv.py

# 2. Rebuild the agency lookup (after refreshing nfl_players_by_agency.csv)
python scripts/build_agency_reps.py

# 3. Recompute projections (uses NFL_Contracts.csv as the comp pool)
python scripts/project_value.py
```

If you want to re-scrape representation data from AthleteAgent.com:

```bash
python scripts/scrape_athleteagent.py
# Resumes from progress file if interrupted; ~5-15 min runtime
```

## Methodology

### Free agent list
OverTheCap's free agency tracker exports a CSV of every player whose contract expires by year. We filter to:
- UFA, Void, RFA tier (true free agents)
- ERFA only if snap share ≥ 30% (filters out practice squad / deep depth)

This produces 876 meaningful 2027 FAs from 1,409 raw rows.

### Representation data
The agency CSV is joined to the FA list via name normalization — lowercase, punctuation stripped, suffixes (Jr/Sr/II/III) removed, hyphens collapsed. Conflicts (a player listed under multiple agencies) are resolved by keeping the higher-AAV row, which proxies for most recent representation. Coverage is ~80% of the FA pool. The unmatched 20% are mostly stars whose agencies (CAA, Wasserman, Athletes First, Excel) have client lists that exceed the source's pagination cap. These display as "—" rather than guessed.

### 2027 projections
Range-based, derived from 2,800+ historical NFL contracts (OverTheCap, 2020-2025, multi-year, inflation-adjusted to current cap). For each player we:

1. Filter the comp pool to the same position group
2. Restrict to comps with inflation-adjusted APY in [0.5x, 2.5x] of the player's prior APY (with a $4M floor and $40M ceiling for edge cases)
3. Show the 25th–75th percentile of that band as the projection range
4. Surface the 4 closest comps by APY distance — these are real signed deals, not generated numbers

We exclude rookie-scale contracts (each player's first NFL deal) since those are CBA-fixed and not a market signal.

The agent gets a defensible range and the named comparables behind it. They bring scouting judgment to land on a final number.

### Team fits
For each FA, we surface 5 teams ranked by:
- Number of expiring contracts at the same position in 2027 (signal: positional need)
- APY of the highest-paid current incumbent (signal: willingness to spend)

This is intentionally simple. A more sophisticated v2 would layer in 2027 cap space projections.

## Trade-offs and what's next

**Honest limits:**
- Agency coverage is ~80% — the source CSV's 100-row-per-agency cap means the largest agencies (CAA, Athletes First, Wasserman) are missing their long tail.
- Projections don't account for player age directly — we rely on comp pool recency and APY-band proximity instead. A 27-year-old coming off a $7M deal gets compared to other players who signed deals in that band, which mixes ages.
- Team fits don't yet use 2027 cap space projections — this is the highest-value v2 add.
- Production stats (yards, EPA, snap share trend) are not yet integrated.

**What I'd build next (in priority order):**
1. Re-scrape with pagination to close agency coverage gap on the top FAs
2. 2027 cap space integration in team fits
3. Add stats overlay (recent season EPA, success rate, snap share trend) per player
4. Side-by-side player comparison
5. CSV export filtered to current view

## File tree

```
nfl_fa_app/
├── app.py                          Streamlit dashboard
├── requirements.txt                Python dependencies
├── README.md                       This file
├── data/
│   ├── fa_2027_raw.csv             OTC export (input)
│   ├── fa_2027_clean.csv           Filtered FAs
│   ├── fa_2027_projected.csv       FAs + projection columns (read by app)
│   ├── nfl_players_by_agency.csv   Source agency data
│   ├── agency_reps.csv             Cleaned player→agency lookup (read by app)
│   └── NFL_Contracts.csv           Historical contracts (comp pool source)
└── scripts/
    ├── clean_fa_csv.py             OTC CSV → clean dataset
    ├── build_agency_reps.py        Agency CSV → cleaned reps lookup
    ├── project_value.py            Compute projection ranges from contracts
    └── scrape_athleteagent.py      Refresh agency data (optional)
```
