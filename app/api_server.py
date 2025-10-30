import os
import math
import statistics
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite

# -----------------------------------------------------------
# Konfigurace
# -----------------------------------------------------------

DB_PATH = os.getenv("DB_PATH", "vehicles_ai.db")

app = FastAPI(title="AutoScan Backend API", version="1.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------
# Palivo - aliasy
# -----------------------------------------------------------

FUEL_ALIASES = {
    "benzin": ["benzin", "benzín", "petrol", "gasoline", "ba", "fsi", "tsi", "mpi", "tce"],
    "nafta": ["nafta", "diesel", "d", "tdi", "dci", "hdi", "cdti", "d-4d", "multijet"],
    "elektro": ["elektro", "electric", "ev", "bev"],
    "hybrid": ["hybrid", "hev"],
    "plug-in hybrid": ["plug-in hybrid", "phev", "plugin"],
    "mild hybrid": ["mild hybrid", "mhev"],
    "cng": ["cng", "zemní plyn", "compressed natural gas"],
    "lpg": ["lpg", "autoplyn", "propan", "butan"],
}

def expand_fuel(fuel: Optional[str]) -> List[str]:
    if not fuel:
        return []
    f = fuel.lower().strip()
    for k, vals in FUEL_ALIASES.items():
        if f == k or any(v in f or f in v for v in vals):
            return vals
    return [f]

# -----------------------------------------------------------
# MODELY
# -----------------------------------------------------------

class PriceEstimateRequest(BaseModel):
    brand: str
    model: str
    year: int
    mileage: int
    fuel: Optional[str] = None
    motor: Optional[str] = None
    window_km: int = 200000
    window_year: int = 1
    fresh_hours: int = 999999
    limit: int = 120

# -----------------------------------------------------------
# DB
# -----------------------------------------------------------

async def _get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db

# -----------------------------------------------------------
# Query
# -----------------------------------------------------------

async def _query_rows(
    *,
    brand: str,
    model: str,
    year: int,
    mileage: int,
    window_km: int,
    window_year: int,
    fuel: Optional[str] = None,
    motor: Optional[str] = None,
    limit: int = 120,
) -> List[Dict[str, Any]]:

    db = await _get_db()
    where = []
    params: List[Any] = []

    where.append("LOWER(brand) LIKE LOWER(?)")
    params.append(f"%{brand}%")
    where.append("LOWER(model) LIKE LOWER(?)")
    params.append(f"%{model}%")
    where.append("(year BETWEEN ? AND ?)")
    params.extend([year - window_year, year + window_year])
    where.append("(mileage BETWEEN ? AND ?)")
    params.extend([max(0, mileage - window_km), mileage + window_km])

    # Palivo s aliasy
    if fuel:
        tokens = expand_fuel(fuel)
        conds = []
        for t in tokens:
            conds.append("LOWER(fuel) LIKE ?")
            params.append(f"%{t}%")
            conds.append("LOWER(fuel_norm) LIKE ?")
            params.append(f"%{t}%")
        where.append("(" + " OR ".join(conds) + ")")

    if motor:
        m = motor.lower().strip()
        where.append("(LOWER(motor) LIKE ? OR LOWER(motor_fold) LIKE ?)")
        params.extend([f"%{m}%", f"%{m}%"])

    sql = f"""
        SELECT brand, model, year, mileage, fuel, motor, transmission, drive, price_czk
        FROM listings_fresh
        WHERE {" AND ".join(where)}
        ORDER BY ABS(year - ?) ASC, ABS(mileage - ?) ASC
        LIMIT ?
    """
    params.extend([year, mileage, limit])
    rows = await db.execute_fetchall(sql, tuple(params))
    await db.close()
    return [dict(r) for r in rows]

# -----------------------------------------------------------
# Endpointy
# -----------------------------------------------------------

@app.get("/comps")
async def get_comps(
    brand: str, model: str, year: int, mileage: int,
    window_km: int = 200000, window_year: int = 1,
    fuel: Optional[str] = None, motor: Optional[str] = None, limit: int = 120,
):
    data = await _query_rows(
        brand=brand, model=model, year=year, mileage=mileage,
        window_km=window_km, window_year=window_year, fuel=fuel, motor=motor, limit=limit,
    )
    return {"found": len(data), "items": data[:limit]}

@app.post("/price/estimate")
async def price_estimate(req: PriceEstimateRequest):
    rows = await _query_rows(
        brand=req.brand, model=req.model, year=req.year, mileage=req.mileage,
        window_km=req.window_km, window_year=req.window_year,
        fuel=req.fuel, motor=req.motor, limit=req.limit,
    )

    prices = [r["price_czk"] for r in rows if r["price_czk"]]
    if not prices:
        return {"found": 0, "message": "No prices"}
    return {
        "found": len(prices),
        "median": round(statistics.median(prices)),
        "mean": round(statistics.mean(prices)),
        "min": min(prices),
        "max": max(prices),
    }

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/")
async def root():
    return {"service": "autoscan_backend", "status": "running"}
