import os
import asyncio
import math
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import statistics
import unicodedata

DB_PATH = os.getenv("DB_PATH", "vehicles_ai.db")

app = FastAPI(title="AutoScan Backend API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
# PŘIPOJENÍ K DATABÁZI
# -----------------------------------------------------------

async def _get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# -----------------------------------------------------------
# Pomocné: fold bez diakritiky + lowercase
# -----------------------------------------------------------

def _fold(s: Optional[str]) -> str:
    if not s:
        return ""
    x = unicodedata.normalize("NFD", s)
    x = "".join(ch for ch in x if unicodedata.category(ch) != "Mn")
    return x.lower().strip()


# -----------------------------------------------------------
# Pomocné: fuel → norm label / LIKE patterny
# -----------------------------------------------------------

def _fuel_norm(user_fuel: str) -> Optional[str]:
    f = (user_fuel or "").strip().lower()
    if not f:
        return None
    if any(k in f for k in ["naft", "diesel", "dízel", "dizel"]):
        return "diesel"
    if any(k in f for k in ["benz", "petrol", "gasoline", "ba"]):
        return "petrol"
    if "hybr" in f:
        return "hybrid"
    if any(k in f for k in ["ev", "elekt", "electr"]):
        return "elect."
    if "lpg" in f or "plyn" in f:
        return "lpg"
    if "cng" in f:
        return "cng"
    return None

def _fuel_patterns(user_fuel: str) -> List[str]:
    f = (user_fuel or "").strip().lower()
    if not f:
        return []
    # Diesel / Nafta
    if f in {"nafta", "diesel", "d", "de", "dizel", "dízel"}:
        return ["%naft%", "%dies%"]
    # Benzín / BA / petrol
    if f in {"benzin", "benzín", "ba", "petrol", "gasoline", "benz"}:
        return ["%benz%"]
    # LPG / plyn
    if f in {"lpg", "autoplyn", "plyn"}:
        return ["%lpg%", "%autoplyn%", "%plyn%"]
    # CNG
    if f in {"cng"}:
        return ["%cng%"]
    # Hybrid
    if f in {"hybrid", "hev", "phev", "plugin-hybrid", "plug-in hybrid", "plug-in"}:
        return ["%hybr%"]
    # Elektro
    if f in {"ev", "electro", "electric", "elektro"}:
        return ["%elekt%"]
    # fallback
    return [f"%{f}%"]


# -----------------------------------------------------------
# FUNKCE NAČTENÍ ZÁZNAMŮ – rozšířená (fold LIKE + fuel_norm)
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
    fresh_hours: int = 999999,
) -> List[Dict[str, Any]]:
    """
    Dotazuje view 'listings_fresh' (nebo tabulku s inzeráty).
    - Značka/model: fold + LIKE přes brand_fold/model_fold/model_base_fold (fallback na LOWER(brand/model)).
    - Palivo: preferuje fuel_norm (= diesel/petrol/elect./...), jinak LIKE patterny.
    - Motor: tolerantní LIKE (bez mezer i s mezerami).
    """
    db = await _get_db()

    where: List[str] = []
    params: List[Any] = []

    # --- brand/model: fold + LIKE ---
    brand_pat = f"%{_fold(brand)}%"
    model_pat = f"%{_fold(model)}%"

    # COALESCE zajistí, že když view nemá *_fold, spadne to na LOWER(brand/model)
    where.append("COALESCE(brand_fold, LOWER(brand)) LIKE ?")
    params.append(brand_pat)

    where.append("("
                 "COALESCE(model_fold, LOWER(model)) LIKE ?"
                 " OR COALESCE(model_base_fold, COALESCE(model_fold, LOWER(model))) LIKE ?"
                 ")")
    params.extend([model_pat, model_pat])

    # --- rok ± okno ---
    where.append("(year BETWEEN ? AND ?)")
    params.extend([year - window_year, year + window_year])

    # --- nájezd ± okno ---
    where.append("(mileage BETWEEN ? AND ?)")
    params.extend([max(0, mileage - window_km), mileage + window_km])

    # --- palivo ---
    if fuel:
        norm = _fuel_norm(fuel)
        if norm:
            # preferuj fuel_norm, fallback na LOWER(fuel) LIKE
            where.append("("
                         "COALESCE(fuel_norm, '') = ?"
                         " OR LOWER(fuel) LIKE ?"
                         ")")
            params.extend([norm, f"%{norm}%"])
        else:
            pats = _fuel_patterns(fuel)
            if pats:
                where.append("(" + " OR ".join(["LOWER(fuel) LIKE ?"] * len(pats)) + ")")
                params.extend(pats)

    # --- motor (např. 'g4fa', '2.0 tdi') ---
    if motor:
        m = motor.strip().lower()
        if m:
            where.append(
                "("
                "LOWER(REPLACE(COALESCE(motor,''), ' ', '')) LIKE ?"
                " OR LOWER(COALESCE(motor,'')) LIKE ?"
                ")"
            )
            params.append(f"%{m.replace(' ', '')}%")
            params.append(f"%{m}%")

    sql = f"""
        SELECT
            source, url, brand, model, year, mileage,
            fuel, motor, transmission, drive, price_czk, scraped_at
        FROM listings_fresh
        WHERE {" AND ".join(where)}
        ORDER BY ABS(year - ?) ASC, ABS(mileage - ?) ASC, price_czk ASC
        LIMIT ?
    """

    params.extend([year, mileage, limit])

    rows = await db.execute_fetchall(sql, tuple(params))
    return [dict(r) for r in rows]


# -----------------------------------------------------------
# /comps – výpis srovnatelných vozů
# -----------------------------------------------------------

@app.get("/comps")
async def get_comparables(
    brand: str = Query(...),
    model: str = Query(...),
    year: int = Query(...),
    mileage: int = Query(...),
    window_km: int = Query(200000),
    window_year: int = Query(1),
    fuel: Optional[str] = Query(None),
    motor: Optional[str] = Query(None),
    limit: int = Query(120),
    fresh_hours: int = Query(999999),
):
    rows = await _query_rows(
        brand=brand,
        model=model,
        year=year,
        mileage=mileage,
        window_km=window_km,
        window_year=window_year,
        fuel=fuel,
        motor=motor,
        limit=limit,
        fresh_hours=fresh_hours,
    )
    return rows


# -----------------------------------------------------------
# /price/estimate – odhad ceny
# -----------------------------------------------------------

@app.post("/price/estimate")
async def price_estimate(req: PriceEstimateRequest):
    rows = await _query_rows(
        brand=req.brand,
        model=req.model,
        year=req.year,
        mileage=req.mileage,
        window_km=req.window_km,
        window_year=req.window_year,
        fuel=req.fuel,
        motor=req.motor,
        limit=req.limit,
        fresh_hours=req.fresh_hours,
    )

    if not rows:
        return {"found": 0, "message": "No comparable rows"}

    prices = [r["price_czk"] for r in rows if r["price_czk"]]

    if not prices:
        return {"found": len(rows), "message": "No price data"}

    # výpočty
    mean_price = statistics.mean(prices)
    median_price = statistics.median(prices)
    low = max(min(prices), median_price * 0.94)
    high = min(max(prices), median_price * 1.18)

    return {
        "price_czk": round(median_price),
        "low_czk": round(low),
        "high_czk": round(high),
        "count": len(prices),
        "found": len(prices),
        "mean": round(mean_price),
        "median": round(median_price),
        "min": min(prices),
        "max": max(prices),
    }


# -----------------------------------------------------------
# /health – kontrola
# -----------------------------------------------------------

@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan_backend"}


# -----------------------------------------------------------
# /debug/db – ověření cesty k DB
# -----------------------------------------------------------

@app.get("/debug/db")
async def debug_db():
    return {"DB_PATH": DB_PATH}


# -----------------------------------------------------------
# SPUŠTĚNÍ (lokálně)
# -----------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
