import sqlite3, argparse, pathlib, datetime, hashlib

def pick(row, *candidates):
    for c in candidates:
        if c in row and row[c] not in (None, ""):
            return row[c]
    return None

def to_int(x, default=0):
    try:
        return int(x)
    except:
        return default

def make_unique_url(r: dict) -> str:
    parts = [
        (r.get("brand") or "").strip().lower(),
        (r.get("model_base") or r.get("model") or "").strip().lower(),
        str(r.get("year") or ""),
        str(r.get("mileage") or ""),
        (r.get("fuel_norm") or r.get("fuel") or ""),
        (r.get("motor_fold") or r.get("motor") or ""),
        (r.get("transmission_norm") or r.get("transmission") or ""),
        (r.get("drive_norm") or r.get("drive") or ""),
        str(r.get("price") or ""),
        (r.get("vin") or ""),
        str(r.get("id") or ""),
    ]
    key = "|".join(parts)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return f"seed://vehicles_clean/{h}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-db", required=True)
    ap.add_argument("--src-table", default="vehicles_clean")
    ap.add_argument("--dst-db", required=True)
    args = ap.parse_args()

    src_path = pathlib.Path(args.src_db)
    dst_path = pathlib.Path(args.dst_db)
    if not src_path.exists():
        raise SystemExit(f"Zdrojová DB neexistuje: {src_path}")
    if not dst_path.exists():
        raise SystemExit(f"Cílová DB neexistuje: {dst_path} (spusť backend, aby se vytvořila)")

    now = datetime.datetime.utcnow().isoformat()

    src = sqlite3.connect(str(src_path)); src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(dst_path))
    cur = src.cursor()

    cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{args.src_table}')").fetchall()]
    need = ["id","brand","model","model_base","year","mileage","fuel","fuel_norm",
            "motor","motor_fold","transmission","transmission_norm",
            "drive","drive_norm","price","vin"]
    sel = [c for c in need if c in cols]
    if "id" not in sel:
        raise SystemExit("Zdrojová tabulka musí mít sloupec 'id'.")

    q = "SELECT " + ",".join(sel) + f" FROM {args.src_table}"
    rows = cur.execute(q)

    dcur = dst.cursor(); count = 0
    for r in rows:
        r = dict(r)

        brand = pick(r, "brand") or ""
        model = pick(r, "model_base","model") or ""
        year = to_int(r.get("year"))
        mileage = to_int(r.get("mileage"))
        fuel = pick(r, "fuel_norm","fuel")
        motor = pick(r, "motor_fold","motor")
        transmission = pick(r, "transmission_norm","transmission")
        drive = pick(r, "drive_norm","drive")
        price_czk = to_int(r.get("price"))
        vin = pick(r, "vin")

        url = make_unique_url(r)  # <<< unikátní URL z hashe

        row = {
            "source": "seed",
            "url": url,
            "scraped_at": now,
            "brand": brand.strip().lower(),
            "model": model.strip().lower(),
            "year": year,
            "mileage": mileage,
            "fuel": (fuel or None),
            "motor": (motor or None),
            "transmission": (transmission or None),
            "drive": (drive or None),
            "price_czk": price_czk,
            "vat": None,
            "vin": (vin or None),
            "location": None,
        }

        dcur.execute("""
          INSERT INTO listings_fresh
          (source,url,scraped_at,brand,model,year,mileage,fuel,motor,transmission,drive,price_czk,vat,vin,location)
          VALUES (:source,:url,:scraped_at,:brand,:model,:year,:mileage,:fuel,:motor,:transmission,:drive,:price_czk,:vat,:vin,:location)
          ON CONFLICT(url) DO UPDATE SET
            scraped_at=excluded.scraped_at,
            brand=excluded.brand, model=excluded.model, year=excluded.year, mileage=excluded.mileage,
            fuel=excluded.fuel, motor=excluded.motor, transmission=excluded.transmission, drive=excluded.drive,
            price_czk=excluded.price_czk, vat=excluded.vat, vin=excluded.vin, location=excluded.location
        """, row)

        count += 1
        if count % 5000 == 0:
            dst.commit()
            print(f"... zapsáno {count}")

    dst.commit()
    print(f"✅ Hotovo: {count} řádků")
    src.close(); dst.close()

if __name__ == "__main__":
    main()
