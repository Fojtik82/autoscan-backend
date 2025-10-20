# app/api_server.py
import os
import statistics
from typing import Any, Dict, List, Optional

import aiosqlite
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# -------------------------------------------------------------------
# Nastavení DB
# -------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "vehicles_ai.db")  # na Renderu: /opt/render/project/src/vehicles_ai.db

app = FastAPI(title="AutoScan Backend API", version="1.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------------------------
# Modely requestů
# -------------------------------------------------------------------
class PriceEstimateRequest(BaseModel):
    brand: str
    model: str
    year: Optional[int] = None         # VOLITELNÉ
    mileage: Optional[int] = None      # VOLITELNÉ
    fuel: Optional[str] = None
    motor: Optional[str] = None
    window_km: int = 20000             # rozumný default
    window_year: int = 1
    fresh_hours: int = 999_999
    limit: int = 120


# -------------------------------------------------------------------
# DB helper
# -------------------------------------------------------------------
async def _get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# -------------------------------------------------------------------
# Dotaz do listings_fresh (s volitelným rokem a nájezdem)
# -------------------------------------------------------------------
async def _query_rows(
    *,
    brand: str,
    model: str,
    year: Optional[int],
    mileage: Optional[int],
    window_km: int,
    window_year: int,
    fuel: Optional[str] = None,
    motor: Optional[str] = None,
    limit: int = 120,
    fresh_hours: int = 999_999,
) -> List[Dict[str, Any]]:
    """
    Hledá v pohledu 'listings_fresh'.
    - brand & model: povinné, case-insensitive rovnost
    - year/mileage: VOLITELNÉ; pokud nejsou, filtr se neaplikuje
    - fuel/motor: volitelné, case-insensitive + LIKE
    """
    db = await _get_db()

    where: List[str] = []
    params: List[Any] = []

    # brand & model
    where.append("LOWER(brand) = LOWER(?)")
    params.append(brand.strip())
    where.append("LOWER(model) = LOWER(?)")
    params.append(model.strip())

    # year (jen pokud zadán)
    if year and year > 0:
        where.append("(year BETWEEN ? AND ?)")
        params.extend([year - window_year, year + window_year])

    # mileage (jen pokud zadán)
    if mileage and mileage > 0:
        low = max(0, mileage - window_km)
        high = mileage + window_km
        where.append("(mileage BETWEEN ? AND ?)")
        params.extend([low, high])

    # fuel (volitelně)
    if fuel:
        f = fuel.strip().lower()
        if f:
            where.append("(LOWER(fuel) = ? OR LOWER(fuel) LIKE ?)")
            params.extend([f, f"%{f}%"])

    # motor (volitelně)
    if motor:
        m = motor.strip().lower()
        if m:
            where.append("LOWER(motor) LIKE ?")
            params.append(f"%{m}%")

    # Sestavení ORDER BY podle toho, co je k dispozici
    order_parts: List[str] = []
    if year and year > 0:
        order_parts.append("ABS(year - ?) ASC")
    if mileage and mileage > 0:
        order_parts.append("ABS(mileage - ?) ASC")
    order_parts.append("price_czk ASC")
    order_by_sql = ", ".join(order_parts)

    sql = f"""
        SELECT
            source, url, brand, model, year, mileage,
            fuel, motor, transmission, drive, price_czk, scraped_at
        FROM listings_fresh
        WHERE {" AND ".join(where)}
        ORDER BY {order_by_sql}
        LIMIT ?
    """

    # Parametry pro ORDER BY
    if year and year > 0:
        params.append(year)
    if mileage and mileage > 0:
        params.append(mileage)
    params.append(limit)

    rows = await db.execute_fetchall(sql, tuple(params))
    return [dict(r) for r in rows]


# -------------------------------------------------------------------
# /comps – vrátí porovnatelné inzeráty
# -------------------------------------------------------------------
@app.get("/comps")
async def get_comparables(
    brand: str = Query(...),
    model: str = Query(...),
    year: Optional[int] = Query(None),
    mileage: Optional[int] = Query(None),
    window_km: int = Query(20000),
    window_year: int = Query(1),
    fuel: Optional[str] = Query(None),
    motor: Optional[str] = Query(None),
    limit: int = Query(120),
    fresh_hours: int = Query(999_999),
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


# -------------------------------------------------------------------
# /price/estimate – odhad ceny z nalezených inzerátů
# -------------------------------------------------------------------
@app.post("/price/estimate")
async def price_estimate(req: PriceEstimateRequest):
    if not req.brand.strip() or not req.model.strip():
        raise HTTPException(status_code=422, detail="brand and model are required")

    rows = await _query_rows(
        brand=req.brand,
        model=req.model,
        year=req.year if (req.year or 0) > 0 else None,
        mileage=req.mileage if (req.mileage or 0) > 0 else None,
        window_km=req.window_km,
        window_year=req.window_year,
        fuel=req.fuel,
        motor=req.motor,
        limit=req.limit,
        fresh_hours=req.fresh_hours,
    )

    if not rows:
        return {"found": 0, "message": "No comparable rows"}

    prices = [r["price_czk"] for r in rows if r.get("price_czk") not in (None, 0)]
    if not prices:
        return {"found": len(rows), "message": "No price data"}

    mean_price = statistics.mean(prices)
    median_price = statistics.median(prices)

    # IQR „pásmo“ kolem mediánu (konzervativní ±18 % / −6 %)
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


# -------------------------------------------------------------------
# Zdraví / debug
# -------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"ok": True, "service": "autoscan_backend"}

@app.get("/debug/db")
async def debug_db():
    return {"DB_PATH": DB_PATH}


# -------------------------------------------------------------------
# Lokální spuštění
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
