import sqlite3
import sys

DB_FILE = r"C:\autoscan_backend\vehicles_ai.db"

def main():
    brand = "Škoda"
    model = "Octavia"
    year = 2020
    mileage = 130000
    window_year = 3
    window_km = 60000

    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print(f"🔎 Hledám brand~'{brand}', model~'{model}', year {year}±{window_year}, km {mileage}±{window_km}")

    sql = """
    SELECT brand, model, year, mileage, fuel, motor, price
    FROM vehicles_clean
    WHERE LOWER(brand) LIKE LOWER(?)
      AND LOWER(model) LIKE LOWER(?)
      AND year BETWEEN ? AND ?
      AND ABS(mileage - ?) <= ?
    LIMIT 20
    """
    args = [
        f"%{brand}%",
        f"%{model}%",
        year - window_year,
        year + window_year,
        mileage,
        window_km,
    ]

    rows = cur.execute(sql, args).fetchall()
    print("COUNT:", len(rows))
    for r in rows[:10]:
        print(dict(r))

    con.close()


if __name__ == "__main__":
    main()
