# app/api_server.py
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import unicodedata

from .db import init_db, DB_PATH
from .config import ALLOWED_ORIGINS, API_KEY

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

# ---------- helpers (diakritika → fold) ----------
def fold(s: str) -> str:
    if s is None:
        return ""
    s = s.strip().lower()
    # odříznutí diakritiky (NFD bez Mn)
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def norm_fuel(s: str) -> str:
    x = fold(s)
    if not x:
        return ""
    if "naft" in x or "diesel" in x:
        return "diesel"
    if "benz" in x or "petrol" in x or "gasol" in x:
        return "petrol"
    if x.startswith("elect"):
        return "elect."
    return x

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
    # stejné defaulty jako ve Flutteru
    window_year: int = 1
    window_km: int = 20000
    limit: int = 1000

# ---------- ROUTES ----------
@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan-backend"}

@app.get("/debug/db")
async def dbg_db():
    return {"DB_PATH": DB_PATH}

@app.get("/debug/count")
async def dbg_count(
    brand: str = Query(...),
    model: str = Query(...),
    year: int = Query(...),
    mileage: int = Query(...),
    window_year: int = 1,
    window_km: int = 20000,
):
    bf = fold(brand)
    mf = fold(model)

    sql = """
    SELECT COUNT(*) AS cnt
    FROM vehicles_clean
    WHERE brand_fold = ?
      AND (model_fold = ? OR model_base_fold = ?)
      AND year BETWEEN ? AND ?
      AND mileage BETWEEN ? AND ?
    """
    args = [
        bf,
        mf, mf,
        year - window_year, year + window_year,
        max(0, mileage - window_km), mileage + window_km,
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, args) as cur:
            row = await cur.fetchone()
            return {"count": row[0], "args": args, "DB_PATH": DB_PATH}

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
    limit: int = 120,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Vyhledávání stejné logiky jako Flutter LocalDbService.querySimilarCars:
    - přesná značka+model (po 'fold' normalizaci)
    - rok v okně ±1 (lze změnit parametrem)
    - nájezd v okně ±20k (lze změnit parametrem)
    - fuel LIKE (nad fuel_norm), motor LIKE (nad motor_fold)
    """
    _auth(x_api_key)

    bf = fold(brand)
    mf = fold(model)
    fueln = norm_fuel(fuel)
    motorf = fold(motor)

    sql = """
    SELECT
      'seed' AS source,
      COALESCE(url, 'local://vehicle/' || id) AS url,
      brand, model, year, mileage, fuel, motor, transmission, drive,
      CAST(price AS INTEGER) AS price_czk,
      'n/a' AS scraped_at
    FROM vehicles_clean
    WHERE brand_fold = ?
      AND (model_fold = ? OR model_base_fold = ?)
      AND year BETWEEN ? AND ?
      AND mileage BETWEEN ? AND ?
      AND (? = '' OR fuel_norm LIKE ?)
      AND (? = '' OR motor_fold LIKE ?)
    ORDER BY ABS(mileage - ?), ABS(year - ?)
    LIMIT ?
    """
    args = [
        bf,
        mf, mf,
        year - window_year, year + window_year,
        max(0, mileage - window_km), mileage + window_km,
        fueln, f"%{fueln}%",
        motorf, f"%{motorf}%",
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
async def price_estimate(body: EstimateReq):
    """
    Vrací jednoduchý průměr/medián z /comps (stejně jako lokální výpočet ve Flutteru).
    """
    rows = await comps(
        brand=body.brand,
        model=body.model,
        year=body.year,
        mileage=body.mileage,
        fuel=body.fuel or "",
        motor=body.motor or "",
        window_km=body.window_km,
        window_year=body.window_year,
        limit=body.limit,
        x_api_key=None,
    )
    if not rows:
        return {"found": 0, "message": "No comparable rows"}

    prices = [r["price_czk"] for r in rows if isinstance(r.get("price_czk"), int)]
    prices.sort()
    n = len(prices)
    if n == 0:
        return {"found": 0, "message": "No comparable rows"}

    # medián + jednoduše min/max
    median = prices[n // 2] if n % 2 == 1 else (prices[n // 2 - 1] + prices[n // 2]) // 2
    return {
        "price_czk": median,
        "low_czk": prices[0],
        "high_czk": prices[-1],
        "count": n,
        "found": n,
    }
