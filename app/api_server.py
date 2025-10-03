# app/api_server.py
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import unicodedata

from .db import init_db, DB_PATH
from .config import ALLOWED_ORIGINS, API_KEY, FRESH_HOURS_DEFAULT
from .estimators import estimate_from_rows


# ----------------- FastAPI app -----------------
app = FastAPI(title="AutoScan Comps API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await init_db()


def _auth(x_api_key: Optional[str]) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ----------------- helpers -----------------
def _fold(s: str) -> str:
    """
    Diakritika pryč + lowercase (aby to sedělo na *_fold / *_norm sloupce).
    """
    if not s:
        return ""
    # NFD -> odstranění kombinačních znaků (diakritiky)
    n = unicodedata.normalize("NFD", s)
    n = "".join(ch for ch in n if unicodedata.category(ch) != "Mn").lower()
    # drobné doladění
    return (
        n.replace("ř", "r")
         .replace("ů", "u")
         .replace("ť", "t")
         .replace("ď", "d")
         .replace("ě", "e")
    )


# ----------------- models -----------------
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


# ----------------- routes -----------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "service": "autoscan-backend"}


# --- debug pomocníci (beze změny) ---
@app.get("/debug/db")
async def debug_db() -> Dict[str, Any]:
    return {"DB_PATH": DB_PATH}


@app.get("/debug/count")
async def debug_count(
    brand: str,
    model: str,
    year: int,
    mileage: int,
    window_km: int = 20000,
    window_year: int = 1,
) -> Dict[str, Any]:
    bf = f"%{_fold(brand)}%"
    mf = f"%{_fold(model)}%"

    sql = """
    SELECT COUNT(*) AS cnt
    FROM vehicles_clean
    WHERE brand_fold LIKE ?
      AND (model_fold LIKE ? OR model_base_fold LIKE ?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
    """
    args = [bf, mf, mf, year - window_year, year + window_year, mileage, window_km]

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, args) as cur:
            row = await cur.fetchone()
            count = row[0] if row else 0

    # jen informativní návrat
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
    fresh_hours: int = FRESH_HOURS_DEFAULT,  # ignorujeme, scraped_at='n/a'
    limit: int = 120,
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Vrátí podobné inzeráty. NOVĚ: čteme přímo z vehicles_clean a
    generujeme URL ze sloupce id (protože ve vehicles_clean není 'url').
    """
    _auth(x_api_key)

    # připrav foldované vstupy pro *_fold / *_norm
    bf = f"%{_fold(brand)}%"
    mf = f"%{_fold(model)}%"
    ff = f"%{_fold(fuel)}%" if fuel else ""
    mfld = f"%{_fold(motor)}%" if motor else ""

    sql = """
    SELECT
      'seed' AS source,
      'local://vehicle/' || CAST(id AS TEXT) AS url,
      brand, model, year, mileage, fuel, motor, transmission, drive,
      CAST(price AS INTEGER) AS price_czk,
      'n/a' AS scraped_at
    FROM vehicles_clean
    WHERE brand_fold LIKE ?
      AND (model_fold LIKE ? OR model_base_fold LIKE ?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
      AND (? = '' OR fuel_norm LIKE ?)
      AND (? = '' OR motor_fold LIKE ?)
    ORDER BY ABS(mileage - ?), ABS(year - ?)
    LIMIT ?
    """
    args = [
        bf,
        mf, mf,
        year - window_year, year + window_year,
        mileage, window_km,
        (ff or ""), ff or "%",     # když fuel prázdný, podmínka se vypne (?='')
        (mfld or ""), mfld or "%", # dtto pro motor
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
    Spočítá odhad (vážený medián + IQR) z /comps nebo z poslaných `rows`.
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
