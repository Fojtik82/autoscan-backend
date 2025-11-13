#!/usr/bin/env bash
set -e

# 1) Stáhnout DB, pokud chybí
if [ ! -f vehicles_ai.db ]; then
  if [ -n "$DB_URL" ]; then
    echo ">> Downloading DB from $DB_URL"
    curl -L "$DB_URL" -o vehicles_ai.db
  elif [ -f vehicles_ai.zip ]; then
    echo ">> Extracting vehicles_ai.zip"
    python - <<'PY'
import zipfile
z = zipfile.ZipFile('vehicles_ai.zip')
z.extract('vehicles_ai.db')
print('DB extracted')
PY
  else
    echo "ERROR: vehicles_ai.db not found and DB_URL not set (nor vehicles_ai.zip present)."
    exit 1
  fi
fi

# 2) Vytvořit/obnovit VIEW listings_fresh podle dostupných sloupců
python - <<'PY'
import sqlite3

DB = "vehicles_ai.db"
TABLE = "vehicles_clean"

con = sqlite3.connect(DB)
cur = con.cursor()

cols = {row[1] for row in cur.execute(f"PRAGMA table_info({TABLE})").fetchall()}

def pick(*options, default="NULL"):
    for name in options:
        if name in cols:
            return name
    return default

PRICE_SRC    = pick("price_czk", "price", default="NULL")
BRAND_SRC    = pick("brand", default="''")
MODEL_SRC    = pick("model", default="''")
YEAR_SRC     = pick("year", default="NULL")
MILEAGE_SRC  = pick("mileage", default="NULL")
FUEL_SRC     = pick("fuel", default="''")
MOTOR_SRC    = pick("motor", default="''")
TRANS_SRC    = pick("transmission", default="''")
DRIVE_SRC    = pick("drive", default="''")
KW_SRC       = pick("kw", default="NULL")

BRAND_FOLD   = pick("brand_fold", default=BRAND_SRC)
MODEL_FOLD   = pick("model_fold", default=MODEL_SRC)
MODEL_BASE_F = pick("model_base_fold", default="''")
FUEL_NORM    = pick("fuel_norm", default=FUEL_SRC)
MOTOR_FOLD   = pick("motor_fold", default=MOTOR_SRC)
DRIVE_NORM   = pick("drive_norm", default=DRIVE_SRC)
TRANS_NORM   = pick("transmission_norm", default=TRANS_SRC)
EQ_FOLD      = pick("equipment_fold", default="''")

sql = f"""
DROP VIEW IF EXISTS listings_fresh;

CREATE VIEW listings_fresh AS
SELECT
  'seed'                              AS source,
  NULL                                AS url,

  {BRAND_SRC}                         AS brand,
  {MODEL_SRC}                         AS model,
  CAST({YEAR_SRC}    AS INTEGER)      AS year,
  CAST({MILEAGE_SRC} AS INTEGER)      AS mileage,
  {FUEL_SRC}                          AS fuel,
  {MOTOR_SRC}                         AS motor,
  {TRANS_SRC}                         AS transmission,
  {DRIVE_SRC}                         AS drive,
  CAST({PRICE_SRC}  AS INTEGER)       AS price_czk,
  'n/a'                               AS scraped_at,

  LOWER({BRAND_FOLD})                 AS brand_fold,
  LOWER({MODEL_FOLD})                 AS model_fold,
  LOWER({MODEL_BASE_F})               AS model_base_fold,
  LOWER({FUEL_NORM})                  AS fuel_norm,
  LOWER({MOTOR_FOLD})                 AS motor_fold,
  LOWER({DRIVE_NORM})                 AS drive_norm,
  LOWER({TRANS_NORM})                 AS transmission_norm,
  LOWER({EQ_FOLD})                    AS equipment_fold,

  {KW_SRC}                            AS kw
FROM {TABLE};
"""
cur.executescript(sql)
con.commit()
con.close()
print("OK: listings_fresh view ready.")
PY

# 3) Spustit API
exec uvicorn app.api_server:app --host 0.0.0.0 --port $PORT
