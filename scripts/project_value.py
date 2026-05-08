"""
Projection engine v2: produce contract value ranges from real historical comps.

What changed from v1:
- Replaced the 88-row hand-curated comps_seed.csv with the full 51K-row historical
  contracts dataset.
- Use inflation-adjusted APYs so a 2021 deal is fairly comparable to a 2025 deal.
- Filter out rookie-scale contracts (a player's first NFL deal) since those are
  CBA-fixed and not a market signal.
- Match by position group + APY band proximity to the player's prior APY.
- Surface the 4 comps closest to the player's prior APY (most defensible).

Methodology:
- Comp pool: contracts from 2020-2025, multi-year (≥2yr), inflation-adjusted APY ≥$1M,
  excluding each player's first-ever recorded contract.
- For each FA, find comps at the same position group with inflation-adjusted APY
  in [0.5x, 2.5x] of their prior APY (with floor $4M, ceiling $40M for low-prior cases).
- 25th/75th percentile of that band defines the projection range.
- Median is the midpoint.
- Surfaced comps: 4 closest to prior APY, sorted by absolute APY distance.
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Map raw contracts CSV positions to FA list pos_groups
POS_MAP = {
    "QB": "QB", "RB": "RB", "FB": "RB",
    "WR": "WR", "TE": "TE",
    "LT": "OL", "RT": "OL", "LG": "OL", "RG": "OL", "C": "OL",
    "DE": "EDGE", "DT": "IDL",
    "LB": "LB", "CB": "CB", "S": "S",
    "K": "ST", "P": "ST", "LS": "ST",
}


def parse_money(v) -> float | None:
    if pd.isna(v):
        return None
    s = str(v).replace("$", "").replace(",", "").strip()
    if not s or s == "0":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def load_comp_pool() -> pd.DataFrame:
    """
    Load and filter the historical contracts CSV into a clean veteran comp pool.

    Filters:
    - Year 2020-2025 (recent enough to reflect current market, exclude future-year typos)
    - Years ≥ 2 (multi-year deals only — exclude one-year prove-it specials skewing low)
    - Inflation-adjusted APY ≥ $1M (exclude practice-squad / minimum salary deals)
    - Position has a valid pos_group mapping
    - Exclude each player's FIRST contract (likely rookie scale)
    - Dedupe on (Player, Year, Years)
    """
    src = DATA_DIR / "NFL_Contracts.csv"
    df = pd.read_csv(src, skiprows=[1], encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    # The CSV has 3 unnamed "Inflated" columns: Value, APY, Guaranteed
    df = df.rename(columns={"Inflated.1": "Inflated_APY"})

    df["APY_num"] = df["APY"].apply(parse_money)
    df["Inflated_APY_num"] = df["Inflated_APY"].apply(parse_money)
    df["pos_group"] = df["Position"].map(POS_MAP)

    # Identify each player's first contract (sort by year, take cumcount)
    df = df.sort_values(["Player", "Year"]).reset_index(drop=True)
    df["contract_num"] = df.groupby("Player").cumcount()

    pool = df[
        (df["contract_num"] >= 1)
        & (df["Year"].between(2020, 2025))
        & (df["Years"] >= 2)
        & (df["Inflated_APY_num"] >= 1_000_000)
        & (df["pos_group"].notna())
    ].drop_duplicates(subset=["Player", "Year", "Years"]).copy()

    return pool


def project_one(player: pd.Series, pool: pd.DataFrame) -> dict:
    """Build a projection for one player using the historical comp pool."""
    pos = player["pos_group"]
    prior_apy = player["prior_apy"] if pd.notna(player["prior_apy"]) else 0

    # APY band: 0.5x-2.5x of prior, with floor $4M and ceiling $40M
    # Floor handles low-prior players (rookie-scale ending) — they're not capped at $2.5M comps.
    # Ceiling prevents one outlier mega-deal from dominating the band for a $20M-prior player.
    band_low = max(prior_apy * 0.5, 1_000_000)  # min $1M to keep things sensible
    band_high = max(prior_apy * 2.5, 4_000_000)
    band_high = min(band_high, 40_000_000)

    pos_pool = pool[pool["pos_group"] == pos]
    band = pos_pool[
        pos_pool["Inflated_APY_num"].between(band_low, band_high)
    ]

    if len(band) < 4:
        # Fallback: drop the band, use all comps at this position
        band = pos_pool

    if len(band) < 3:
        return {
            "low": None, "high": None, "midpoint": None,
            "comp_count": 0, "comp_names": "[]",
            "method": "insufficient comps",
        }

    apys = band["Inflated_APY_num"]
    low = apys.quantile(0.25)
    mid = apys.median()
    high = apys.quantile(0.75)

    # Surface the 4 comps closest to the player's prior APY
    band_sorted = band.assign(
        _dist=(band["Inflated_APY_num"] - prior_apy).abs()
    ).nsmallest(4, "_dist")

    comp_names = [
        f"{row['Player']} ({int(row['Year'])}, {int(row['Years'])}yr, ${row['Inflated_APY_num']/1e6:.1f}M)"
        for _, row in band_sorted.iterrows()
    ]

    return {
        "low": round(low / 1e6, 1),
        "high": round(high / 1e6, 1),
        "midpoint": round(mid / 1e6, 1),
        "comp_count": len(band),
        "comp_names": str(comp_names),
        "method": f"{len(band)} {pos} comps in ${band_low/1e6:.1f}M-${band_high/1e6:.1f}M band",
    }


def project_all(fa_df: pd.DataFrame) -> pd.DataFrame:
    pool = load_comp_pool()
    print(f"[project] Comp pool: {len(pool)} historical contracts")
    print(f"[project] Per-position depth:")
    for pos, n in pool["pos_group"].value_counts().items():
        print(f"   {pos:5s} {n}")

    rows = [project_one(p, pool) for _, p in fa_df.iterrows()]
    proj = pd.DataFrame(rows)
    return pd.concat([fa_df.reset_index(drop=True), proj], axis=1)


if __name__ == "__main__":
    fa = pd.read_csv(DATA_DIR / "fa_2027_clean.csv")
    out = project_all(fa)
    out.to_csv(DATA_DIR / "fa_2027_projected.csv", index=False)
    n_proj = (out["low"].notna()).sum()
    print(f"\n[project] {n_proj}/{len(out)} FAs got projections")
    print(f"\n[project] Sample of top 10 projections:")
    print(out.nlargest(10, "prior_apy")[
        ["name", "pos_group", "team", "age", "prior_apy", "low", "high", "comp_count"]
    ].to_string())
