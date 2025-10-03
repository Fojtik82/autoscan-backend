# app/api_server.py
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite

from .db import init_db, DB_PATH
from .config import ALLOWED_ORIGINS, API_KEY, FRESH_HOURS_DEFAULT
from .estimators import estimate_from_rows


# --- FastAPI ---
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
async def startup() -> None:
    await init_db()


def _auth(x_api_key: Optional[str]) -> None:
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
    rows: Optional[list[dict]] = None
    fresh_hours: int = FRESH_HOURS_DEFAULT
    window_km: int = 20000
    window_year: int = 1
    limit: int = 120


# ---------- ROUTES ----------
@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan-backend"}


@app.get("/debug/db")
async def dbg_db():
    # rychlá kontrola, jaký soubor SQLite se používá
    return {"DB_PATH": DB_PATH}


@app.get("/debug/count")
async def dbg_count(
    brand: str,
    model: str,
    year: int,
    mileage: int,
    window_km: int = 60000,
    window_year: int = 3,
):
    """
    Vrátí jen COUNT(*) pro stejný filtr jako /comps – užitečné pro ladění.
    """
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

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, args)
            row = await cur.fetchone()         # <-- správný způsob v aiosqlite
            count = row[0] if row is not None else 0
        return {"count": count, "args": args, "DB_PATH": DB_PATH}
    except Exception as e:
        # ať je při ladění hned vidět přesná chyba
        raise HTTPException(status_code=500, detail=f"dbg_count error: {e}")


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
    Vrátí podobné inzeráty z listings_fresh (může to být i VIEW nad vehicles_clean).
    Brand/model matchují přes LIKE (volnější chování).
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
        cur = await db.execute(sql, args)
        rows = await cur.fetchall()
        for r in rows:
            out.append(dict(r))
    return out


@app.post("/price/estimate")
async def price_estimate(
    body: EstimateReq,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Spočítá vážený medián + IQR z /comps (nebo z poslaných `rows`).
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

    est = estimate_from_rows(rows, body.year, body.mileage, body.motor or "")
    if not est:
        return {"found": 0, "message": "No comparable rows"}

    est["found"] = est["count"]
    return est
