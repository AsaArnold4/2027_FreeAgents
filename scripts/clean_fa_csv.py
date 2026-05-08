"""
Clean the raw OTC 2027 free agents CSV into a canonical dataset.

Input: data/fa_2027_raw.csv (downloaded from OverTheCap free agency tracker)
Output: data/fa_2027_clean.csv

Steps:
- Parse money columns to numeric
- Map team names to standard abbreviations
- Normalize positions
- Filter out very-deep rookie ERFA tier players (no agent context, not relevant)
- Add a player slug for headshot/lookup matching
"""
import re
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

TEAM_ABBR = {
    "Bills": "BUF", "Dolphins": "MIA", "Patriots": "NE", "Jets": "NYJ",
    "Ravens": "BAL", "Bengals": "CIN", "Browns": "CLE", "Steelers": "PIT",
    "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Titans": "TEN",
    "Broncos": "DEN", "Chiefs": "KC", "Raiders": "LV", "Chargers": "LAC",
    "Cowboys": "DAL", "Giants": "NYG", "Eagles": "PHI", "Commanders": "WAS",
    "Bears": "CHI", "Lions": "DET", "Packers": "GB", "Vikings": "MIN",
    "Falcons": "ATL", "Panthers": "CAR", "Saints": "NO", "Buccaneers": "TB",
    "Cardinals": "ARI", "Rams": "LAR", "49ers": "SF", "Seahawks": "SEA",
}

# Position normalization (collapse OL variants for filtering, keep detail in column)
POS_GROUP = {
    "QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
    "LT": "OL", "RT": "OL", "T": "OL", "LG": "OL", "RG": "OL", "G": "OL", "C": "OL",
    "EDGE": "EDGE", "IDL": "IDL", "LB": "LB", "CB": "CB", "S": "S",
    "K": "ST", "P": "ST", "LS": "ST",
}


def parse_money(val) -> float:
    """Convert '$5,000,000 ' -> 5_000_000.0; blanks/0 -> 0.0"""
    if pd.isna(val):
        return 0.0
    s = str(val).replace("$", "").replace(",", "").strip()
    if not s or s == "0":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def slugify(name: str) -> str:
    """Convert player name to a URL-safe slug for matching."""
    s = name.lower()
    s = re.sub(r"[^\w\s'-]", "", s)
    s = s.replace("'", "").replace(".", "")
    s = re.sub(r"\s+", "-", s.strip())
    return s


def clean(min_apy: float = 1_000_000) -> pd.DataFrame:
    raw = pd.read_csv(DATA_DIR / "fa_2027_raw.csv")
    raw.columns = [c.strip() for c in raw.columns]

    df = pd.DataFrame({
        "name": raw["Player"].str.strip(),
        "pos_detail": raw["Pos."].str.strip(),
        "team_full": raw["2026 Team"].str.strip(),
        "fa_type": raw["Type"].str.strip(),
        "snap_pct": raw["Snaps"].str.rstrip("%").astype(float, errors="ignore"),
        "age": pd.to_numeric(raw["Age"], errors="coerce"),
        "prior_apy": raw["Current APY"].apply(parse_money),
        "prior_gtd": raw["Guarantees"].apply(parse_money),
    })

    df["team"] = df["team_full"].map(TEAM_ABBR).fillna(df["team_full"])
    df["pos_group"] = df["pos_detail"].map(POS_GROUP).fillna(df["pos_detail"])
    df["slug"] = df["name"].apply(slugify)

    # Filter: keep only meaningful FAs
    # - Drop ERFA with snaps under 5% (deep depth/practice squad)
    # - Drop anyone under $1M APY who's also under 30% snap share (no agent leverage)
    keep = (
        (df["fa_type"].isin(["UFA", "Void", "RFA"]))
        | ((df["fa_type"] == "ERFA") & (df["snap_pct"] >= 30))
    )
    df = df[keep].copy()

    # Sort by APY descending (most relevant first)
    df = df.sort_values("prior_apy", ascending=False).reset_index(drop=True)

    out_path = DATA_DIR / "fa_2027_clean.csv"
    df.to_csv(out_path, index=False)
    print(f"[clean] Wrote {len(df)} rows → {out_path}")
    print(f"[clean] FA types: {df['fa_type'].value_counts().to_dict()}")
    print(f"[clean] Positions: {df['pos_group'].value_counts().to_dict()}")
    return df


if __name__ == "__main__":
    clean()
