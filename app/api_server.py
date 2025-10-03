# app/api_server.py
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite

from .db import init_db, DB_PATH
from .config import ALLOWED_ORIGINS, API_KEY, FRESH_HOURS_DEFAULT
from .estimators import estimate_from_rows

# ----------------- Helpers: fold/normalize (bez diakritiky) -----------------
def fold_input(s: Optional[str]) -> str:
    if not s:
        return ""
    x = s.strip().lower()
    return (
        x.replace("á", "a").replace("ä", "a").replace("â", "a")
         .replace("č", "c")
         .replace("ď", "d")
         .replace("é", "e").replace("ě", "e").replace("ë", "e")
         .replace("í", "i").replace("ï", "i")
         .replace("ľ", "l").replace("ĺ", "l")
         .replace("ň", "n")
         .replace("ó", "o").replace("ô", "o")
         .replace("ř", "r")
         .replace("š", "s")
         .replace("ť", "t")
         .replace("ú", "u").replace("ů", "u").replace("ü", "u")
         .replace("ý", "y")
         .replace("ž", "z")
    )

def norm_fuel(s: Optional[str]) -> Optional[str]:
    if not s or not s.strip():
        return None
    x = fold_input(s)
    if "naft" in x or "diesel" in x:
        return "diesel"
    if "benz" in x or "petrol" in x or "gasoline" in x:
        return "petrol"
    if x.startswith("elect"):
        return "elect."
    return x

# ----------------- FastAPI -----------------
app = FastAPI(title="AutoScan Comps API", version="1.1")

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

# ---------- MODELS ----------
class Comp(BaseModel):
    source: str
    url: str
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
    fresh_hours: int = FRESH_HOURS_DEFAULT,  # zachován parametr (ignorujeme v vehicles_clean)
    limit: int = 120,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Hledá přímo v `vehicles_clean` na *normalizovaných* sloupcích:
      - brand_fold, model_fold / model_base_fold (LIKE, bez diakritiky, case-insensitive)
      - fuel_norm (rovnost)
      - motor_fold (LIKE)
    Rok a nájezd přes okna (±).
    """
    _auth(x_api_key)

    brand_f = fold_input(brand)
    model_f = fold_input(model)
    fuel_n = norm_fuel(fuel)
    motor_f = fold_input(motor)

    # Pozn.: `vehicles_clean` nemá scraped_at ani price_czk => price_czk = price, scraped_at='n/a'
    sql = """
    SELECT
      'seed'                         AS source,
      COALESCE(url, 'local://vehicle/' || id) AS url,
      brand                          AS brand,           -- vracíme původní hodnotu (s diakritikou)
      model                          AS model,
      CAST(year AS INTEGER)          AS year,
      CAST(mileage AS INTEGER)       AS mileage,
      fuel,
      motor,
      transmission,
      drive,
      CAST(price AS INTEGER)         AS price_czk,
      'n/a'                          AS scraped_at
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
        f"%{brand_f}%",
        f"%{model_f}%", f"%{model_f}%",
        year - window_year, year + window_year,
        mileage, window_km,
        (fuel_n or ""), (fuel_n or ""),
        (motor_f or ""), f"%{motor_f}%",
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

@app.get("/debug/db")
async def dbg_db():
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
    brand_f = fold_input(brand)
    model_f = fold_input(model)

    sql = """
    SELECT COUNT(*)
    FROM vehicles_clean
    WHERE brand_fold LIKE ?
      AND (model_fold LIKE ? OR model_base_fold LIKE ?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
    """
    args = [
        f"%{brand_f}%",
        f"%{model_f}%", f"%{model_f}%",
        year - window_year, year + window_year,
        mileage, window_km,
    ]

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, args) as cur:
            row = await cur.fetchone()
            count = row[0] if row else 0
    return {"count": count, "args": args, "DB_PATH": DB_PATH}

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
        )

    est = estimate_from_rows(rows, body.year, body.mileage, body.motor or "")
    if not est:
        return {"found": 0, "message": "No comparable rows"}

    est["found"] = est["count"]
    return est
