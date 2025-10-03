# app/api_server.py
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import unicodedata

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

# Init DB
@app.on_event("startup")
async def startup():
    await init_db()

def _auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# ---------- helpers: fold / norm ----------
def _fold(s: str) -> str:
    """lowercase + remove diacritics/spaces collapse (pro LIKE %...%)"""
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s

def _norm_fuel(s: str) -> str:
    x = _fold(s)
    if not x:
        return ""
    if "naft" in x or "diesel" in x: return "diesel"
    if "benz" in x or "petrol" in x or "gasol" in x: return "petrol"
    if x.startswith("elect"): return "elect."
    return x

# ---------- MODELS ----------
class Comp(BaseModel):
    source: str
    url: str | None = None
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
    # stejné filtrování jako /comps, jen COUNT(*)
    bf = f"%{_fold(brand)}%"
    mf = f"%{_fold(model)}%"
    y0, y1 = year - window_year, year + window_year
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchone(
            """
            SELECT COUNT(*)
            FROM vehicles_clean
            WHERE brand_fold LIKE ?
              AND (model_fold LIKE ? OR model_base_fold LIKE ?)
              AND year BETWEEN ? AND ?
              AND ABS(mileage - ?) <= ?
            """,
            (bf, mf, mf, y0, y1, mileage, window_km),
        )
    return {"count": row[0], "args": [bf, mf, y0, y1, mileage, window_km], "DB_PATH": DB_PATH}

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
    fresh_hours: int = FRESH_HOURS_DEFAULT,  # ignorováno (scraped_at je 'n/a'), ponechán pro kompatibilitu
    limit: int = 120,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Vrátí podobné inzeráty z *vehicles_clean* s využitím indexovaných sloupců:
    brand_fold / model_fold / model_base_fold / fuel_norm / motor_fold.
    """
    _auth(x_api_key)

    bf = f"%{_fold(brand)}%"
    mf = f"%{_fold(model)}%"
    fn = _norm_fuel(fuel)
    mot_like = f"%{_fold(motor)}%" if motor else ""

    sql = """
    SELECT
      'seed' AS source,
      'local://vehicle/' || CAST(id AS TEXT) AS url,
      brand,                      -- původní text
      model,                      -- původní text
      year,
      mileage,
      fuel,
      motor,
      transmission,
      drive,
      CAST(price AS INTEGER) AS price_czk,
      'n/a' AS scraped_at
    FROM vehicles_clean
    WHERE brand_fold LIKE ?
      AND (model_fold LIKE ? OR model_base_fold LIKE ?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
      AND (? = '' OR fuel_norm = ?)
      AND (? = '' OR motor_fold LIKE ?)
    ORDER BY ABS(mileage - ?), ABS(year - ?)
    LIMIT ?
    """

    args = [
        bf,
        mf, mf,
        year - window_year, year + window_year,
        mileage, window_km,
        fn, fn,               # fuel
        mot_like, mot_like,   # motor LIKE
        mileage, year,
        limit,
    ]

    out: list[dict] = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(sql, args)
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
