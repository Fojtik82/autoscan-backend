# app/api_server.py
import os
import math
import json
import unicodedata
from typing import Optional, List, Dict, Any

import aiosqlite
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def _norm(s: Optional[str]) -> str:
    """lower + odstranění diakritiky + ořez"""
    if not s:
        return ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

# Palivové aliasy (klíče i hodnoty jsou již 'norm()')
FUEL_ALIASES: Dict[str, List[str]] = {
    "ba":       ["ba", "benzin", "benzín", "petrol", "gasoline"],
    "benzin":   ["ba", "benzin", "benzín", "petrol", "gasoline"],
    "benzín":   ["ba", "benzin", "benzín", "petrol", "gasoline"],
    "nafta":    ["nafta", "diesel", "d"],
    "diesel":   ["nafta", "diesel", "d"],
    "lpg":      ["lpg", "autoplyn"],
    "cng":      ["cng", "zemni plyn", "zemní plyn"],
    "hybrid":   ["hybrid", "hev", "phev"],
    "elektro":  ["elektro", "ev", "electric"],
}

def _int_or_none(x: Optional[int]) -> Optional[int]:
    try:
        return int(x) if x is not None else None
    except Exception:
        return None

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
# Výchozí cesta k DB – na Renderu je projekt v /opt/render/project/src/
DEFAULT_DB = "/opt/render/project/src/vehicles_ai.db"
DB_PATH = os.getenv("DB_FILE") or (DEFAULT_DB if os.path.exists(DEFAULT_DB) else "./vehicles_ai.db")

ALLOWED_ORIGINS = [o.strip() for o in (os.getenv("ALLOWED_ORIGINS") or "*").split(",")]

app = FastAPI(title="AutoScan Comps API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# Pydantic model pro POST /price/estimate
# ---------------------------------------------------------
class EstimateReq(BaseModel):
    brand: str
    model: str
    year: int
    mileage: int
    fuel: Optional[str] = None
    motor: Optional[str] = None
    window_km: Optional[int] = 20000
    window_year: Optional[int] = 1
    fresh_hours: Optional[int] = 999999
    limit: Optional[int] = 120

# ---------------------------------------------------------
# Debug/health
# ---------------------------------------------------------
@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan_backend"}

@app.get("/debug/db")
async def dbg_db():
    return {"DB_PATH": DB_PATH}

@app.get("/debug/count")
async def dbg_count(
    brand: str = Query(...),
    model: str = Query(...),
    year: int = Query(...),
    mileage: int = Query(...),
    window_km: int = Query(20000),
    window_year: int = Query(1),
    fuel: Optional[str] = Query(None),
    motor: Optional[str] = Query(None),
    fresh_hours: int = Query(999999),
):
    rows = await _query_rows(
        brand=brand, model=model, year=year, mileage=mileage,
        window_km=window_km, window_year=window_year,
        fuel=fuel, motor=motor, fresh_hours=fresh_hours, limit=999999
    )
    return {"count": len(rows), "args": locals(), "DB_PATH": DB_PATH}

# ---------------------------------------------------------
# Core SELECT – používá /comps i /price/estimate
# ---------------------------------------------------------
async def _query_rows(
    *,
    brand: str,
    model: str,
    year: int,
    mileage: int,
    window_km: int = 20000,
    window_year: int = 1,
    fuel: Optional[str] = None,
    motor: Optional[str] = None,
    fresh_hours: int = 999999,
    limit: int = 120,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []

    # diakritika-insensitive match na značku/model (použijeme *_fold sloupce)
    brand_n = _norm(brand)
    model_n = _norm(model)
    where.append("brand_fold = ?")
    params.append(brand_n)
    where.append("model_fold = ?")
    params.append(model_n)

    # rok ± okno
    year = int(year)
    y_min, y_max = year - int(window_year), year + int(window_year)
    where.append("year BETWEEN ? AND ?")
    params += [y_min, y_max]

    # nájezd ± okno
    mileage = int(mileage)
    km_min = max(0, mileage - int(window_km))
    km_max = max(km_min, mileage + int(window_km))
    where.append("mileage BETWEEN ? AND ?")
    params += [km_min, km_max]

    # čerstvost (pokud máš timestamp; ve seed je 'n/a', tak podmínku vynecháváme)
    # V tvé DB je view 'listings_fresh' už očištěné – použijeme ho
    table = "listings_fresh"

    # FUEL aliasy (BA ~ benzin, Diesel ~ nafta)
    f_norm = _norm(fuel) if fuel else ""
    if f_norm:
        tokens = FUEL_ALIASES.get(f_norm, [f_norm])
        placeholders = ",".join(["?"] * len(tokens))
        where.append(f"fuel_norm IN ({placeholders})")
        params += tokens

    # MOTOR – podřetězcové LIKE bez diakritiky
    m_norm = _norm(motor) if motor else ""
    if m_norm:
        like = f"%{m_norm}%"
        where.append("(motor_fold LIKE ? OR lower(motor) LIKE ?)")
        params += [like, like]

    sql = f"""
        SELECT source, url, brand, model, year, mileage, fuel, motor,
               transmission, drive, price_czk, scraped_at
        FROM {table}
        WHERE {" AND ".join(where)}
        ORDER BY year DESC, ABS(mileage - ?) ASC
        LIMIT ?
    """
    params += [mileage, int(limit)]

    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

# ---------------------------------------------------------
# GET /comps – vrací porovnatelné inzeráty
# ---------------------------------------------------------
@app.get("/comps")
async def comps(
    brand: str = Query(..., description="Značka"),
    model: str = Query(..., description="Model"),
    year: int = Query(...),
    mileage: int = Query(...),
    window_km: int = Query(20000),
    window_year: int = Query(1),
    fuel: Optional[str] = Query(None),
    motor: Optional[str] = Query(None),
    fresh_hours: int = Query(999999),
    limit: int = Query(120),
):
    rows = await _query_rows(
        brand=brand, model=model, year=year, mileage=mileage,
        window_km=window_km, window_year=window_year,
        fuel=fuel, motor=motor, fresh_hours=fresh_hours, limit=limit
    )
    return rows

# ---------------------------------------------------------
# POST /price/estimate – spočítá cenu (median + IQR)
# ---------------------------------------------------------
@app.post("/price/estimate")
async def price_estimate(req: EstimateReq = Body(...)):
    rows = await _query_rows(
        brand=req.brand,
        model=req.model,
        year=req.year,
        mileage=req.mileage,
        window_km=_int_or_none(req.window_km) or 20000,
        window_year=_int_or_none(req.window_year) or 1,
        fuel=req.fuel,
        motor=req.motor,
        fresh_hours=_int_or_none(req.fresh_hours) or 999999,
        limit=_int_or_none(req.limit) or 120,
    )
    prices = [int(r["price_czk"]) for r in rows if r.get("price_czk") is not None]
    if not prices:
        return {"found": 0, "message": "No comparable rows"}

    prices.sort()
    n = len(prices)

    def _pct(p: float) -> float:
        # lineární interpolace percentilu
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(prices[int(k)])
        return prices[f] + (prices[c] - prices[f]) * (k - f)

    q1 = _pct(0.25)
    q2 = _pct(0.50)  # median
    q3 = _pct(0.75)

    return {
        "price_czk": round(q2),
        "low_czk": round(q1),
        "high_czk": round(q3),
        "count": n,
        "found": n,
        "mean": round(sum(prices) / n),
        "median": round(q2),
        "min": min(prices),
        "max": max(prices),
    }
