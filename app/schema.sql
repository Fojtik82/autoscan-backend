CREATE TABLE IF NOT EXISTS listings_fresh (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,              -- "sauto" | "tipcars" | "bazos"
  url TEXT NOT NULL UNIQUE,
  scraped_at TEXT NOT NULL,          -- ISO8601 (UTC)
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  year INTEGER NOT NULL,
  mileage INTEGER NOT NULL,
  fuel TEXT,
  motor TEXT,
  transmission TEXT,
  drive TEXT,
  price_czk INTEGER NOT NULL,
  vat TEXT,
  vin TEXT,
  location TEXT
);

CREATE INDEX IF NOT EXISTS idx_search
ON listings_fresh(brand, model, year, mileage, fuel, motor, price_czk);

CREATE INDEX IF NOT EXISTS idx_time ON listings_fresh(scraped_at);
