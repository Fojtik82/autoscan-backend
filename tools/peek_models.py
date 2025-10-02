# tools/peek_models.py
import sqlite3, sys

DB = r"C:\autoscan_backend\vehicles_ai.db"

brand = None
if len(sys.argv) > 1:
    brand = sys.argv[1]

con = sqlite3.connect(DB)
c = con.cursor()

brands = c.execute("SELECT DISTINCT brand FROM vehicles_clean ORDER BY brand LIMIT 50").fetchall()
print("BRANDS (first 50):")
for (b,) in brands:
    print("-", b)

if brand:
    print(f"\nMODELS for brand = {brand!r}:")
    models = c.execute(
        "SELECT DISTINCT model FROM vehicles_clean WHERE LOWER(brand)=LOWER(?) ORDER BY model LIMIT 100",
        (brand,)
    ).fetchall()
    for (m,) in models:
        print("-", m)

con.close()
