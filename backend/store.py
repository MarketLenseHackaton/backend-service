"""
store.py — the only file that reads data files or talks to the database.

Data files (loaded once, cached):
    load_places()        data/places.csv
    load_sources()       data/sources.csv
    load_discover()      data/discover.json
    load_nafdac_notices() data/nafdac_notices.json

SQLite (marketlens.db in the repo root):
    capacity_overrides   the latest row per place replaces its capacity
    claims               each NEW claim adds 1 to registered
                         (seeded claims are businesses already counted in the CSV)
    feedback             trial feedback from the Discover page

current_places() = CSV + SQLite changes, rebuilt on every request (no stale cache).
"""

import json
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent      # repo root
DATA_DIR = ROOT / "data"
DB_PATH = ROOT / "marketlens.db"

PLACE_INT_FIELDS = ("population", "capacity", "registered")
PLACE_FLOAT_FIELDS = ("demand", "protein_deficiency", "crc_index", "meat_cost")

# Businesses already in the CSV's "registered" count for the full places,
# so the regulator page has real-looking claims to check. Empty NAFDAC numbers
# here are what GET /api/regulator/overview reports as missing_nafdac.
SEED_CLAIMS = [
    ("LAG-FAM-SUP", "Demo seller Lagos 1", "A7-0001"),
    ("LAG-FAM-SUP", "Demo seller Lagos 2", "A7-0002"),
    ("LAG-FAM-SUP", "Demo seller Lagos 3", ""),
    ("LAG-FAM-SUP", "Demo seller Lagos 4", ""),
    ("ABJ-STU-CAN", "Demo seller Abuja 1", "A7-0101"),
    ("ABJ-STU-CAN", "Demo seller Abuja 2", ""),
    ("ABJ-PRO-SUP", "Demo seller Abuja 3", ""),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- data files

def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and value != value)  # NaN


def _clean_place(row: dict) -> dict:
    place = {k: (None if _is_missing(v) else v) for k, v in row.items()}
    for field in PLACE_INT_FIELDS:
        if place.get(field) is not None:
            place[field] = int(place[field])
    for field in PLACE_FLOAT_FIELDS:
        if place.get(field) is not None:
            place[field] = float(place[field])
    for field in ("id", "name", "state", "segment", "source_ids"):
        place[field] = "" if place.get(field) is None else str(place[field])
    return place


def _clean_source(row: dict) -> dict:
    source = {k: (None if _is_missing(v) else v) for k, v in row.items()}
    if source.get("year") is not None:
        source["year"] = int(source["year"])
    for field in ("id", "title", "publisher", "level", "used_for"):
        source[field] = "" if source.get(field) is None else str(source[field])
    return source


@lru_cache
def load_places() -> tuple[dict, ...]:
    df = pd.read_csv(DATA_DIR / "places.csv")
    return tuple(_clean_place(r) for r in df.to_dict("records"))


@lru_cache
def load_sources() -> tuple[dict, ...]:
    df = pd.read_csv(DATA_DIR / "sources.csv")
    return tuple(_clean_source(r) for r in df.to_dict("records"))


@lru_cache
def load_discover() -> dict:
    with open(DATA_DIR / "discover.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_nafdac_notices() -> tuple[dict, ...]:
    with open(DATA_DIR / "nafdac_notices.json", encoding="utf-8") as f:
        return tuple(json.load(f))


def get_sources(source_ids: str) -> list[dict]:
    wanted = [s.strip() for s in (source_ids or "").split(";") if s.strip()]
    by_id = {s["id"]: s for s in load_sources()}
    return [dict(by_id[i]) for i in wanted if i in by_id]


def discover_content(place_id: str) -> dict:
    """Recipes and extra activities for one place (falls back to 'default')."""
    data = load_discover()
    default = data.get("default", {})
    own = data.get("places", {}).get(place_id, {})
    return {
        "recipes": own.get("recipes") or default.get("recipes", []),
        "activities": own.get("activities") or default.get("activities", []),
    }


# ---------------------------------------------------------------- SQLite

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tables and seed them if they are empty. Runs at start-up."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS capacity_overrides (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id    TEXT NOT NULL,
                capacity    INTEGER NOT NULL,
                changed_by  TEXT NOT NULL,
                changed_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id       TEXT NOT NULL,
                business_name  TEXT NOT NULL,
                nafdac_no      TEXT NOT NULL DEFAULT '',
                seeded         INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id    TEXT NOT NULL,
                liked       TEXT NOT NULL,
                can_cook    TEXT NOT NULL,
                would_buy   TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            """
        )
        has_claims = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        if not has_claims:
            _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO claims (place_id, business_name, nafdac_no, seeded, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        [(pid, name, nafdac, _now()) for pid, name, nafdac in SEED_CLAIMS],
    )


def reset_db() -> None:
    """Clear every change and re-seed. Used by POST /api/admin/reset."""
    with _connect() as conn:
        conn.execute("DELETE FROM capacity_overrides")
        conn.execute("DELETE FROM claims")
        conn.execute("DELETE FROM feedback")
        _seed(conn)


# ---------------------------------------------------------------- places = CSV + SQLite

def current_places() -> list[dict]:
    """Every place with SQLite changes applied. Rebuilt on every request."""
    with _connect() as conn:
        overrides = {
            row["place_id"]: row["capacity"]
            for row in conn.execute(
                "SELECT place_id, capacity FROM capacity_overrides ORDER BY id"
            )
        }  # later rows overwrite earlier ones, so the latest change wins
        new_claims = {
            row["place_id"]: row["n"]
            for row in conn.execute(
                "SELECT place_id, COUNT(*) AS n FROM claims WHERE seeded = 0 GROUP BY place_id"
            )
        }

    places = []
    for base in load_places():
        place = dict(base)
        if place["id"] in overrides:
            place["capacity"] = overrides[place["id"]]
        place["registered"] = place["registered"] + new_claims.get(place["id"], 0)
        places.append(place)
    return places


def get_place(place_id: str) -> dict | None:
    return next((p for p in current_places() if p["id"] == place_id), None)


# ---------------------------------------------------------------- writes

def add_claim(place_id: str, business_name: str, nafdac_no: str) -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO claims (place_id, business_name, nafdac_no, seeded, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (place_id, business_name, nafdac_no, _now()),
        )
        row = conn.execute("SELECT * FROM claims WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def add_feedback(place_id: str, liked: str, can_cook: str, would_buy: str) -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO feedback (place_id, liked, can_cook, would_buy, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (place_id, liked, can_cook, would_buy, _now()),
        )
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def set_capacity(place_id: str, capacity: int, changed_by: str = "regulator") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO capacity_overrides (place_id, capacity, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?)",
            (place_id, capacity, changed_by, _now()),
        )


# ---------------------------------------------------------------- reads for the API

def feedback_summary(place_id: str) -> dict:
    """tasters = responses; each *_pct = % of tasters who answered 'yes'."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT liked, can_cook, would_buy FROM feedback WHERE place_id = ?",
            (place_id,),
        ).fetchall()

    tasters = len(rows)
    if tasters == 0:
        return {"tasters": 0, "liked_pct": None, "can_cook_pct": None, "would_buy_pct": None}

    def pct(field: str) -> int:
        return round(100 * sum(1 for r in rows if r[field] == "yes") / tasters)

    return {
        "tasters": tasters,
        "liked_pct": pct("liked"),
        "can_cook_pct": pct("can_cook"),
        "would_buy_pct": pct("would_buy"),
    }


def count_missing_nafdac() -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM claims WHERE TRIM(nafdac_no) = ''"
        ).fetchone()[0]