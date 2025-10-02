import aiosqlite
from .config import DB_PATH

INIT_NOTE = """-- Nic neni destruktivni. Jen zajistime, ze existuje 'listings_fresh' (VIEW),
-- pokud v DB existuje 'vehicles_clean'. Na VIEW NEdelame indexy, SQLite by to nechtelo.
"""

CREATE_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS listings_fresh AS
SELECT
  'seed' AS source,
  -- Pokud vehicles_clean nema 'url', vyrobime fallback. (mnoho dumpu ji nema)
  COALESCE(url, 'local://vehicle/' || id) AS url,
  LOWER(brand)      AS brand,
  LOWER(model)      AS model,
  CAST(year    AS INTEGER) AS year,
  CAST(mileage AS INTEGER) AS mileage,
  fuel,
  motor,
  transmission,
  drive,
  -- nekdy je sloupec 'price' (v Kč), jindy 'price_czk'; zkus obe varianty:
  CAST(COALESCE(price_czk, price) AS INTEGER) AS price_czk,
  -- scraped_at u seed dat neni -> dame 'n/a', abychom v API mohli fresh filtr ignorovat
  COALESCE(scraped_at, 'n/a') AS scraped_at
FROM vehicles_clean;
"""

async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    async with db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?;", (name,)) as cur:
        row = await cur.fetchone()
        return row is not None

async def init_db():
    # Pokud v DB existuje vehicles_clean a neexistuje listings_fresh, zalozime pohled.
    async with aiosqlite.connect(DB_PATH) as db:
        if await _table_exists(db, "vehicles_clean"):
            if not await _table_exists(db, "listings_fresh"):
                await db.executescript(CREATE_VIEW_SQL)
                await db.commit()
