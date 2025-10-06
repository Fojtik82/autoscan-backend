# app/api_server.py
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import math
from statistics import median

from .db import init_db, DB_PATH
from .config import ALLOWED_ORIGINS, API_KEY, FRESH_HOURS_DEFAULT

# --- FastAPI instance ---
app = FastAPI(title="AutoScan Comps API", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Init DB při startu
@app.on_event("startup")
async def startup():
    await init_db()


def _auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------- MODELS ----------
class Comp(BaseModel):
    source: str
    url: Optional[str] = None
    brand: str
    model: str
    year: int
    mileage: int
    fuel: Optional[str] = None
    motor: Optional[str] = None
    transmission: Optional[str] = None
    drive: Optional[str] = None
    price_czk: int
    scraped_at: str


class EstimateReq(BaseModel):
    brand: str
    model: str
    year: int
    mileage: int
    fuel: Optional[str] = ""
    motor: Optional[str] = ""
    # volitelné – pokud pošleš rovnou řádky, použijí se
    rows: Optional[list[dict]] = None
    fresh_hours: int = FRESH_HOURS_DEFAULT
    window_km: int = 20000
    window_year: int = 1
    limit: int = 120


# ---------- HELPERS ----------
def _summarize_prices(prices: list[int | float]) -> dict:
    """Vrátí count, median, mean, min, max (zaokrouhlené na celé CZK)."""
    if not prices:
        return {}
    prices_sorted = sorted(float(p) for p in prices)
    n = len(prices_sorted)
    med = median(prices_sorted)
    mean = sum(prices_sorted) / n
    return {
        "count": n,
        "median": int(round(med)),
        "mean": int(round(mean)),
        "min": int(round(prices_sorted[0])),
        "max": int(round(prices_sorted[-1])),
    }


# ---------- ROUTES ----------
@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan-backend"}


@app.get("/debug/db")
async def dbg_db():
    # ukáže, jakou DB skutečně používá server
    return {"DB_PATH": DB_PATH}


@app.get("/debug/count")
async def dbg_count(
    brand: str = Query(...),
    model: str = Query(...),
    year: int = Query(...),
    mileage: int = Query(...),
    window_km: int = 20000,
    window_year: int = 1,
):
    sql = """
    SELECT COUNT(*)
    FROM listings_fresh
    WHERE LOWER(brand) LIKE LOWER(?)
      AND LOWER(model) LIKE LOWER(?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
    """
    args = [
        f"%{brand}%",
        f"%{model}%",
        year - window_year,
        year + window_year,
        mileage,
        window_km,
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            row = await cur.fetchone()
            count = row[0] if row else 0
    return {"count": count, "args": args, "DB_PATH": DB_PATH}


@app.get("/comps", response_model=List[Comp])
async def comps(
    brand: str,
    model: str,
    year: int,
    mileage: int,
    fuel: str = "",
    motor: str = "",
    window_km: int = 20000,
    window_year: int = 1,
    fresh_hours: int = FRESH_HOURS_DEFAULT,
    limit: int = 120,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Vrátí podobné inzeráty z `listings_fresh` (VIEW nad vehicles_clean).
    Brand/model matchují přes LIKE.
    Pokud scraped_at='n/a', ignoruje se čerstvost.
    """
    _auth(x_api_key)

    sql = """
    SELECT source,url,brand,model,year,mileage,fuel,motor,transmission,drive,price_czk,scraped_at
    FROM listings_fresh
    WHERE LOWER(brand) LIKE LOWER(?)
      AND LOWER(model) LIKE LOWER(?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
      AND (?='' OR LOWER(fuel)  LIKE LOWER(?))
      AND (?='' OR LOWER(motor) LIKE LOWER(?))
      AND (scraped_at = 'n/a' OR datetime(scraped_at) >= datetime('now', '-' || ? || ' hours'))
    ORDER BY ABS(mileage - ?), ABS(year - ?)
    LIMIT ?
    """

    args = [
        f"%{brand}%",
        f"%{model}%",
        year - window_year,
        year + window_year,
        mileage,
        window_km,
        (fuel or ""), f"%{(fuel or '').lower()}%",
        (motor or ""), f"%{(motor or '').lower()}%",
        fresh_hours,
        mileage, year,
        limit,
    ]

    out: list[dict] = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            async for r in cur:
                out.append(dict(r))
    return out


@app.post("/price/estimate")
async def price_estimate(
    body: EstimateReq,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Spočítá souhrn (count, median, mean, min, max) z /comps
    nebo z `rows` pokud je klient pošle přímo.
    """
    _auth(x_api_key)

    rows = body.rows or []
    if not rows:
        rows = await comps(
            brand=body.brand,
            model=body.model,
            year=body.year,
            mileage=body.mileage,
            fuel=body.fuel or "",
            motor=body.motor or "",
            window_km=body.window_km,
            window_year=body.window_year,
            fresh_hours=body.fresh_hours,
            limit=body.limit,
            x_api_key=x_api_key,
        )

    # vytáhnout ceny
    prices = []
    for r in rows:
        p = r.get("price_czk")
        if isinstance(p, (int, float)) and p > 0:
            prices.append(p)

    if not prices:
        return {"found": 0, "message": "No comparable rows"}

    summary = _summarize_prices(prices)
    # pro kompatibilitu s frontendem pojmenujeme klíče i „low/high/price“
    return {
        "price_czk": summary["median"],
        "low_czk": int(round(summary["mean"] * 0.92)),   # volitelný „rozptyl“
        "high_czk": int(round(summary["mean"] * 1.15)),  # volitelný „rozptyl“
        "count": summary["count"],
        "found": summary["count"],
        "mean": summary["mean"],
        "median": summary["median"],
        "min": summary["min"],
        "max": summary["max"],
    }
