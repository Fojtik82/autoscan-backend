import os, sqlite3, sys, json

DB = os.environ.get("DB_FILE", r"C:\autoscan_backend\vehicles_ai.db")
print(f"DB_FILE = {DB}")

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

# 0) Je v DB objekt 'listings_fresh'?
print("\n-- sqlite_master check --")
for row in c.execute("SELECT name, type, sql FROM sqlite_master WHERE name='listings_fresh'"):
    print(dict(row))

# 1) Kolik je tam celkem řádků?
try:
    cnt = c.execute("SELECT COUNT(*) FROM listings_fresh").fetchone()[0]
    print(f"\nlistings_fresh COUNT = {cnt}")
except Exception as e:
    print("\nCOUNT error:", e)

# 2) Zkusíme dotaz, který je 1:1 s API parametry (Škoda Octavia, 2020±3, 130k±60k)
brand = "Škoda"
model = "Octavia"
year = 2020
window_year = 3
mileage = 130000
window_km = 60000
fresh_hours = 999999
fuel = ""
motor = ""

sql = """
SELECT source,url,brand,model,year,mileage,fuel,motor,transmission,drive,price_czk,scraped_at
FROM listings_fresh
WHERE LOWER(brand) LIKE LOWER(?)
  AND LOWER(model) LIKE LOWER(?)
  AND year BETWEEN ? AND ?
  AND ABS(mileage - ?) <= ?
  AND (?='' OR LOWER(fuel)  LIKE LOWER(?))
  AND (?='' OR LOWER(motor) LIKE LOWER(?))
  AND (scraped_at = 'n/a' OR datetime(scraped_at) >= datetime('now', '-' || ? || ' hours'))
ORDER BY ABS(mileage - ?), ABS(year - ?)
LIMIT 20
"""
args = [
    f"%{brand}%",
    f"%{model}%",
    year - window_year, year + window_year,
    mileage, window_km,
    (fuel or ""), f"%{(fuel or '').lower()}%",
    (motor or ""), f"%{(motor or '').lower()}%",
    fresh_hours,
    mileage, year,
]

print("\n-- API SQL probe --")
print("ARGS:", args)
try:
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    print("MATCHED:", len(rows))
    for r in rows[:5]:
        print(json.dumps(r, ensure_ascii=False))
except Exception as e:
    print("SQL error:", e)

# 3) Rychlá kontrola přímo na vehicles_clean (stejná logika jako dřív)
print("\n-- vehicles_clean sanity --")
q2 = """
SELECT COUNT(*)
FROM vehicles_clean
WHERE lower(brand) LIKE '%škoda%'
  AND lower(model) LIKE '%octavia%'
  AND year BETWEEN ? AND ?
  AND ABS(mileage - ?) <= ?
"""
a2 = (year - window_year, year + window_year, mileage, window_km)
print("COUNT vehicles_clean =", c.execute(q2, a2).fetchone()[0])

con.close()
