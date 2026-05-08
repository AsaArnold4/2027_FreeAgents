"""
Build a clean player→agency lookup from the uploaded NFL agency CSV.

Replaces the partial athleteagent_reps.csv (which only had VaynerSports clients)
with a full-coverage version derived from the user-supplied scrape.

Strategy:
- Strict name normalization: lowercase, strip periods/commas/apostrophes,
  drop "Jr"/"Sr"/"II"/"III"/"IV" suffixes, collapse whitespace.
- Match on normalized name only — no fuzzy matching (avoids false positives
  like attaching the wrong "Calen Bullock" to the wrong agent).
- Conflict resolution: when a player appears under multiple agencies in the
  source, keep the row with the higher AAV (proxy for most recent / committed
  representation).
- Display name: properly capitalized first + last name (Title Case), since
  the source CSV is mixed case.

Input:  data/nfl_players_by_agency.csv (uploaded)
Output: data/agency_reps.csv  — name, slug, norm_key, agency_name, aav_aa
"""
from __future__ import annotations
import re
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """Strict normalization for joining. Returns lowercase, punctuation-free, suffix-free."""
    if not isinstance(name, str):
        return ""
    s = name.lower()
    s = s.replace("'", "").replace("'", "").replace("`", "")
    s = s.replace(".", "").replace(",", "")
    s = SUFFIX_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[-]", "", s)  # treat hyphens as joins (gardner-johnson → gardnerjohnson)
    return s


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^\w\s'-]", "", s)
    s = s.replace("'", "").replace(".", "")
    s = re.sub(r"\s+", "-", s.strip())
    return s


def display_case(name: str) -> str:
    """Title-case for display. Preserves common particles (de, of, the) lowercase, but
    in NFL data this is rare — so straight title case is fine."""
    if not isinstance(name, str):
        return name
    # Special-case: keep apostrophes' following letter capitalized ("De'Von")
    parts = re.split(r"(\s+|-)", name.strip())
    out = []
    for p in parts:
        if not p or p.isspace() or p == "-":
            out.append(p)
            continue
        # Handle apostrophes: "DE'VON" → "De'Von"
        if "'" in p:
            sub = p.split("'")
            sub = [s.capitalize() for s in sub]
            out.append("'".join(sub))
        else:
            out.append(p.capitalize())
    return "".join(out)


def build():
    src = DATA_DIR / "nfl_players_by_agency.csv"
    fa = pd.read_csv(DATA_DIR / "fa_2027_clean.csv")
    df = pd.read_csv(src)

    df = df[df["league"] == "NFL"].copy()
    df["norm_key"] = df["player_name"].apply(normalize_name)
    df["aav_aa"] = pd.to_numeric(df["avg_annual_pay"], errors="coerce").fillna(0.0)

    # Conflict resolution: keep highest-AAV row per normalized name
    df = df.sort_values("aav_aa", ascending=False)
    df = df.drop_duplicates(subset=["norm_key"], keep="first")

    # Build clean output
    out = pd.DataFrame({
        "name": df["player_name"].apply(display_case),
        "slug": df["player_name"].apply(slugify),
        "norm_key": df["norm_key"],
        "agency_name": df["agency_name"],
        "aav_aa": df["aav_aa"],
    })

    out_path = DATA_DIR / "agency_reps.csv"
    out.to_csv(out_path, index=False)

    # Diagnostics: how does this overlap with the FA list?
    fa["norm_key"] = fa["name"].apply(normalize_name)
    matched = fa[fa["norm_key"].isin(set(out["norm_key"]))]
    print(f"[reps] {len(out)} unique players in agency data → {out_path}")
    print(f"[reps] {len(matched)}/{len(fa)} FAs matched ({100*len(matched)/len(fa):.1f}%)")
    print(f"[reps] {out['agency_name'].nunique()} unique agencies")

    # Show top agencies by FA count for context
    fa_with_agency = fa.merge(out[["norm_key", "agency_name"]], on="norm_key", how="inner")
    top = fa_with_agency["agency_name"].value_counts().head(15)
    print(f"\n[reps] Top 15 agencies by 2027 FA count:")
    for agency, count in top.items():
        print(f"  {count:3d}  {agency}")


if __name__ == "__main__":
    build()
