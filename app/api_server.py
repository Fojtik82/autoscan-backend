from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite

from .db import init_db
from .config import ALLOWED_ORIGINS, API_KEY, FRESH_HOURS_DEFAULT, DB_PATH
from .estimators import estimate_from_rows

app = FastAPI(title="AutoScan Comps API", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    await init_db()

def _auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# ------------ MODELS ------------
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

# ------------ ROUTES ------------
@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan-backend"}

@app.get("/debug/db")
async def dbg_db():
    return {"DB_PATH": DB_PATH}

@app.get("/debug/count")
async def dbg_count(
    brand: str,
    model: str,
    year: int,
    mileage: int,
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
        year - window_year, year + window_year,
        mileage, window_km
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchone(sql, args)
        cnt = row[0] if row else 0
    return {"count": cnt, "args": args, "DB_PATH": DB_PATH}

@app.get("/comps", response_model=List[Comp])
async def comps(
    brand: str = Query(..., description="např. 'Škoda' (LIKE)"),
    model: str = Query(..., description="např. 'Octavia' (LIKE)"),
    year: int = Query(..., description="cílový rok"),
    mileage: int = Query(..., description="cílový nájezd km"),
    fuel: str = Query("", description="volitelné (LIKE)"),
    motor: str = Query("", description="volitelné (LIKE)"),
    window_km: int = 20000,
    window_year: int = 1,
    fresh_hours: int = FRESH_HOURS_DEFAULT,
    limit: int = 120,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Vrátí srovnatelné vozy z `listings_fresh` (VIEW nad `vehicles_clean`, založí se při startu).
    - Brand/Model = LIKE (volné hledání)
    - Rok v intervalu [year ± window_year]
    - Nájezd v intervalu [mileage ± window_km]
    - Pokud `scraped_at='n/a'`, filtr čerstvosti se neaplikuje.
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
        year - window_year, year + window_year,
        mileage, window_km,
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
    Vrátí robustní odhad ceny (median + IQR) z /comps (nebo z poslaných `rows`).
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

    est = estimate_from_rows(rows, body.year, body.mileage, (body.motor or ""))
    if not est:
        return {"found": 0, "message": "No comparable rows"}
    est["found"] = est["count"]
    return est
