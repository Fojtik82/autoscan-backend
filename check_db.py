import sqlite3

# ⚙️ Nastavení filtru
brand = "skoda"
model = "octavia"
year_from = 2015
year_to = 2021

# Připojení k DB
con = sqlite3.connect(r"C:\autoscan_backend\listings_fresh.db")
c = con.cursor()

# Celkový počet
q_count = f"""
SELECT COUNT(*) FROM listings_fresh
WHERE brand = '{brand}'
  AND model LIKE '{model}%'
  AND year BETWEEN {year_from} AND {year_to};
"""
count = c.execute(q_count).fetchone()[0]
print(f"🔎 Počet záznamů pro {brand} {model} ({year_from}-{year_to}): {count}")

# Ukázkové řádky
q_sample = f"""
SELECT brand, model, year, mileage, price_czk
FROM listings_fresh
WHERE brand = '{brand}'
  AND model LIKE '{model}%'
  AND year BETWEEN {year_from} AND {year_to}
LIMIT 5;
"""
rows = c.execute(q_sample).fetchall()

print("\n📋 Ukázkové záznamy:")
for r in rows:
    print(r)

con.close()
