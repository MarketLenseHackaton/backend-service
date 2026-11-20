"""
Pydantic models for MarketLens.
Field names match the Project Spec Sections 3 and 4 exactly. Do not rename.

Note: values with a fixed set of options (weights 1-3, lens, feedback answers,
capacity >= 1) are plain types here on purpose. main.py checks them and returns
the spec's error codes (bad_weights, bad_lens, bad_value, bad_capacity).
If Pydantic checked them, FastAPI would return its own 422 shape instead.
"""

from typing import Literal, Optional

from pydantic import BaseModel

AreaType = Literal["big_city", "city", "rural"]
Channel = Literal["open_market", "supermarket", "canteen", "restaurant", "village_shop"]
Label = Literal["measured", "estimated", "assumed"]
Level = Literal["low", "medium", "high"]
Status = Literal["open", "nearly_full", "full"]


# ---------- 1. Health ----------

class HealthResponse(BaseModel):
    status: str


# ---------- 2. Products ----------

class Product(BaseModel):
    id: str
    name: str
    uses: list[str]


# ---------- Sources (used by 5 and 11) ----------

class Source(BaseModel):
    id: str
    title: str
    publisher: str
    url: Optional[str] = None
    year: Optional[int] = None
    level: str          # e.g. national, zone, state, "state / LGA", local, global
    used_for: str


# ---------- Place record (Section 3) ----------

class Place(BaseModel):
    id: str
    name: str
    state: str
    area_type: AreaType
    segment: str
    channel: Channel
    population: int
    demand: float
    protein_deficiency: float
    crc_index: Optional[float] = None
    meat_cost: float
    capacity: int
    registered: int
    awareness_gap: Level
    label_demand: Label
    label_health: Label
    label_meat_cost: Label
    source_ids: str
    market_type: Optional[str] = None   # extra column in the AI team's places.csv


class PlaceOut(Place):
    """GET /api/places: Place + derived fields."""
    is_rural: bool
    slots_left: int
    status: Status


class PlaceDetail(PlaceOut):
    """GET /api/places/{id}: PlaceOut + its sources and notes."""
    sources: list[Source]
    notes: str


# ---------- 3. POST /api/rank ----------

class Weights(BaseModel):
    demand: int
    health: int
    affordability: int


class RankRequest(BaseModel):
    product_id: str
    weights: Weights
    lens: str
    fair_share: bool = False


class Scores(BaseModel):
    demand: float
    health: float
    affordability: float
    total: float
    low: float
    high: float


class Confidence(BaseModel):
    pct: float
    label: Level


class CapacityInfo(BaseModel):
    registered: int
    capacity: int
    slots_left: int
    status: Status


class Explanation(BaseModel):
    demand: str
    health: str
    affordability: str
    uncertain: str
    test_first: str


class CheckLabels(BaseModel):
    demand: Label
    health: Label
    affordability: Label


class RankedPlace(BaseModel):
    rank: int
    id: str
    name: str
    area_type: AreaType
    is_rural: bool
    segment: str
    channel: Channel
    scores: Scores
    confidence: Confidence
    capacity: CapacityInfo
    explanation: Explanation
    labels: CheckLabels
    fair_share_added: bool


class FullPlace(BaseModel):
    id: str
    name: str
    segment: str
    registered: int
    capacity: int


class RankEquity(BaseModel):
    rural_in_top5: int
    target: int
    balanced: bool


class RankMeta(BaseModel):
    n_simulations: int
    seed: int
    data_as_of: Optional[str] = None
    lens: str


class RankResponse(BaseModel):
    ranked: list[RankedPlace]
    full: list[FullPlace]
    equity: RankEquity
    meta: RankMeta


# ---------- 6. GET /api/discover/{id} ----------

class Activity(BaseModel):
    title: str
    description: str
    effort: Level


class Recipe(BaseModel):
    dish: str
    steps: str


class FeedbackSummary(BaseModel):
    tasters: int
    liked_pct: Optional[float] = None
    can_cook_pct: Optional[float] = None
    would_buy_pct: Optional[float] = None


class DiscoverResponse(BaseModel):
    place_id: str
    awareness_gap: Level
    lens: str
    message: str
    activities: list[Activity]
    recipes: list[Recipe]
    feedback_summary: FeedbackSummary


# ---------- 7. POST /api/feedback ----------

class FeedbackRequest(BaseModel):
    place_id: str
    liked: str       # yes / okay / no
    can_cook: str    # yes / not_sure / no
    would_buy: str   # yes / maybe / no


class FeedbackResponse(BaseModel):
    id: int
    place_id: str
    liked: str
    can_cook: str
    would_buy: str
    created_at: str


# ---------- 8. POST /api/claims ----------

class ClaimRequest(BaseModel):
    place_id: str
    business_name: str
    nafdac_no: str = ""

# Response for claims is CapacityInfo (the updated capacity object).


# ---------- 9. GET /api/regulator/overview ----------

class Counts(BaseModel):
    businesses: int
    places: int
    open: int
    nearly_full: int
    full: int


class AreaShare(BaseModel):
    area: AreaType
    business_pct: float
    need_pct: float


class RegulatorEquity(BaseModel):
    score: float
    by_area: list[AreaShare]


class RegulatorPlace(BaseModel):
    id: str
    name: str
    area_type: AreaType
    health_need: str
    demand: float
    registered: int
    capacity: int
    suggested_capacity: int
    status: Status


class NafdacNotice(BaseModel):
    title: str
    date: str
    url: str


class RegulatorOverview(BaseModel):
    counts: Counts
    equity: RegulatorEquity
    places: list[RegulatorPlace]
    missing_nafdac: int
    nafdac_notices: list[NafdacNotice]


# ---------- 10. PATCH /api/regulator/places/{id}/capacity ----------

class CapacityUpdate(BaseModel):
    capacity: int

# Response is RegulatorPlace (the updated row from the overview).
