import os
from contextlib import asynccontextmanager

import marketlens_ai as ai
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from backend import store
from backend.errors import (
    ApiError,
    api_error_handler,
    bad_capacity,
    bad_lens,
    bad_value,
    bad_weights,
    error_response,
    nafdac_required,
    not_found,
    place_full,
)
from backend.schemas import (
    CapacityInfo,
    CapacityUpdate,
    ClaimRequest,
    DiscoverResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    PlaceDetail,
    PlaceOut,
    Product,
    RankRequest,
    RankResponse,
    RegulatorOverview,
    RegulatorPlace,
    Source,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()          # create SQLite tables + seed claims on first run
    yield


app = FastAPI(title="MarketLens API", lifespan=lifespan)

# Which websites may call this API. Local dev ports are always allowed.
# On Render, set CORS_ORIGINS to the live frontend URL(s), comma-separated,
# e.g. https://marketlens.vercel.app
LOCAL_ORIGINS = ["http://localhost:5173", "http://localhost:4173", "http://127.0.0.1:5173"]
EXTRA_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_ORIGINS + EXTRA_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VALID_LENSES = ("protein", "cancer", "both")
VALID_WEIGHTS = (1, 2, 3)
FEEDBACK_VALUES = {
    "liked": ("yes", "okay", "no"),
    "can_cook": ("yes", "not_sure", "no"),
    "would_buy": ("yes", "maybe", "no"),
}
DATA_AS_OF = "2026-05"   # NBS Selected Food Price Watch, May 2026 (source S01)

PRODUCTS = [
    {"id": "soy_chunk", "name": "Soy–cowpea protein chunk", "uses": ["stews", "jollof", "suya-style"]},
    {"id": "awara", "name": "Awara (soy cheese)", "uses": ["stews", "snacks", "suya-style"]},
    {"id": "soy_mince", "name": "Soy mince", "uses": ["jollof", "sauces", "stews"]},
]


# ---------- Error handlers: every error is {"error": {"code", "message"}} ----------

app.add_exception_handler(ApiError, api_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    message = f"{field}: {first.get('msg', 'invalid value')}" if field else "Invalid request"
    return error_response(400, "bad_value", message)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    return error_response(500, "server_error", str(exc))


# ---------- Helpers ----------

def with_derived(place: dict) -> dict:
    """Add is_rural, slots_left and status (status comes from the AI module)."""
    return {
        **place,
        "is_rural": place.get("area_type") == "rural",
        "slots_left": max(0, place["capacity"] - place["registered"]),
        "status": ai.status(place),
    }


def capacity_info(place: dict) -> dict:
    return {
        "registered": place["registered"],
        "capacity": place["capacity"],
        "slots_left": max(0, place["capacity"] - place["registered"]),
        "status": ai.status(place),
    }


def health_need(place: dict) -> str:
    """Which health need is bigger here: 'protein' or 'cancer'."""
    protein = place.get("protein_deficiency") or 0
    cancer = place.get("crc_index")
    return "cancer" if cancer is not None and cancer > protein else "protein"


def regulator_row(place: dict) -> dict:
    return {
        "id": place["id"],
        "name": f"{place['name']} · {place['segment']}",
        "area_type": place["area_type"],
        "health_need": health_need(place),
        "demand": place["demand"],
        "registered": place["registered"],
        "capacity": place["capacity"],
        "suggested_capacity": ai.suggest_capacity(place),
        "status": ai.status(place),
    }


def require_place(place_id: str) -> dict:
    place = store.get_place(place_id)
    if place is None:
        raise not_found(f"Place {place_id}")
    return place


def check_lens(lens: str) -> None:
    if lens not in VALID_LENSES:
        raise bad_lens(f"lens must be one of {', '.join(VALID_LENSES)}, got '{lens}'")


# ---------- 1. Health ----------

@app.get("/api/health", response_model=HealthResponse)
def health():
    return {"status": "ok"}


# ---------- 2. Products ----------

@app.get("/api/products", response_model=list[Product])
def products():
    return PRODUCTS


# ---------- 3. Rank ----------

@app.post("/api/rank", response_model=RankResponse)
def rank(req: RankRequest):
    weights = req.weights.model_dump()
    for name, value in weights.items():
        if value not in VALID_WEIGHTS:
            raise bad_weights(f"Weight '{name}' must be 1, 2 or 3, got {value}")
    check_lens(req.lens)

    places = store.current_places()
    result = ai.rank(places, weights, req.lens, fair_share=req.fair_share)
    if not result["meta"].get("data_as_of"):
        result["meta"]["data_as_of"] = DATA_AS_OF
    return result


# ---------- 4 / 5. Places ----------

@app.get("/api/places", response_model=list[PlaceOut])
def list_places():
    return [with_derived(p) for p in store.current_places()]


@app.get("/api/places/{place_id}", response_model=PlaceDetail)
def get_place(place_id: str):
    place = require_place(place_id)
    return {
        **with_derived(place),
        "sources": store.get_sources(place["source_ids"]),
        "notes": place.get("market_type") or "",
    }


# ---------- 6. Discover ----------

@app.get("/api/discover/{place_id}", response_model=DiscoverResponse)
def discover(place_id: str, lens: str = "both"):
    check_lens(lens)
    place = require_place(place_id)

    plan = ai.discover_plan(place, lens)
    extra = store.discover_content(place_id)
    return {
        **plan,
        "activities": plan["activities"] + extra["activities"],
        "recipes": extra["recipes"],
        "feedback_summary": store.feedback_summary(place_id),
    }


# ---------- 7. Feedback ----------

@app.post("/api/feedback", response_model=FeedbackResponse, status_code=201)
def feedback(req: FeedbackRequest):
    for field, allowed in FEEDBACK_VALUES.items():
        value = getattr(req, field)
        if value not in allowed:
            raise bad_value(f"{field} must be one of {', '.join(allowed)}, got '{value}'")
    require_place(req.place_id)

    return store.add_feedback(req.place_id, req.liked, req.can_cook, req.would_buy)


# ---------- 8. Claims ----------

@app.post("/api/claims", response_model=CapacityInfo, status_code=201)
def claim(req: ClaimRequest):
    place = require_place(req.place_id)
    if not req.nafdac_no.strip():
        raise nafdac_required("A NAFDAC registration number is required to claim a slot")
    if not req.business_name.strip():
        raise bad_value("business_name is required")
    if ai.status(place) == "full":
        raise place_full(f"{place['name']} · {place['segment']} has no slots left")

    store.add_claim(req.place_id, req.business_name.strip(), req.nafdac_no.strip())
    return capacity_info(require_place(req.place_id))


# ---------- 9. Regulator overview ----------

@app.get("/api/regulator/overview", response_model=RegulatorOverview)
def regulator_overview():
    places = store.current_places()
    statuses = [ai.status(p) for p in places]
    return {
        "counts": {
            "businesses": sum(p["registered"] for p in places),
            "places": len(places),
            "open": statuses.count("open"),
            "nearly_full": statuses.count("nearly_full"),
            "full": statuses.count("full"),
        },
        "equity": ai.equity_overview(places),
        "places": [regulator_row(p) for p in places],
        "missing_nafdac": store.count_missing_nafdac(),
        "nafdac_notices": list(store.load_nafdac_notices()),
    }


# ---------- 10. Change capacity ----------

@app.patch("/api/regulator/places/{place_id}/capacity", response_model=RegulatorPlace)
def change_capacity(place_id: str, req: CapacityUpdate):
    if req.capacity < 1:
        raise bad_capacity(f"Capacity must be at least 1, got {req.capacity}")
    require_place(place_id)

    store.set_capacity(place_id, req.capacity)
    return regulator_row(require_place(place_id))


# ---------- 11. Sources ----------

@app.get("/api/sources", response_model=list[Source])
def sources():
    return [dict(s) for s in store.load_sources()]


# ---------- Admin (not used by the frontend) ----------

@app.post("/api/admin/reset")
def admin_reset():
    store.reset_db()
    return {"status": "reset"}