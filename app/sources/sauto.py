import sqlite3
import time
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# -----------------------------
# Pomocné funkce
# -----------------------------
def parse_price(text):
    """Očistí cenu a převede na číslo"""
    try:
        return int(re.sub(r"\D", "", text))
    except:
        return None

def parse_mileage(text):
    """Očistí km a převede na číslo"""
    try:
        return int(re.sub(r"\D", "", text))
    except:
        return None

def parse_year(text):
    """Z textu zkusí vytáhnout rok výroby"""
    try:
        match = re.search(r"(19|20)\d{2}", text)
        if match:
            return int(match.group(0))
    except:
        return None
    return None

def guess_fuel(motor_text, fallback=""):
    """Z motoru odhadne palivo"""
    motor_text = motor_text.lower()
    if "tdi" in motor_text or "dci" in motor_text or "cdti" in motor_text or "nafta" in motor_text:
        return "nafta"
    if "tsi" in motor_text or "mpi" in motor_text or "benzin" in motor_text:
        return "benzin"
    if "hybrid" in motor_text:
        return "hybrid"
    if "elektro" in motor_text or "ev" in motor_text:
        return "elektro"
    return fallback

# -----------------------------
# DB inicializace
# -----------------------------
def init_db():
    conn = sqlite3.connect("vehicles_sauto.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            model TEXT,
            year INTEGER,
            mileage INTEGER,
            fuel TEXT,
            motor TEXT,
            price INTEGER,
            vin TEXT,
            transmission TEXT,
            url TEXT
        )
    """)
    conn.commit()
    return conn

# -----------------------------
# Scraper
# -----------------------------
def scrape_sauto():
    # Selenium headless
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)

    # Startovací URL (Škoda, 1040 stran)
    start_urls = [
        f"https://www.sauto.cz/inzerce/osobni/skoda?page={i}"
        for i in range(1, 20)  # ⚠️ pro test jen 20 stránek, pak můžeš dát až 1040
    ]

    conn = init_db()
    cur = conn.cursor()

    for url in start_urls:
        print(f"🔎 Načítám {url}")
        driver.get(url)
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        ads = soup.select(".c-item a.c-item__link")

        for ad in ads:
            ad_url = "https://www.sauto.cz" + ad.get("href")
            try:
                driver.get(ad_url)
                time.sleep(1.5)
                ad_soup = BeautifulSoup(driver.page_source, "html.parser")

                # Titulek = brand + model
                title = ad_soup.select_one("h1").get_text(strip=True) if ad_soup.select_one("h1") else ""

                # Cena
                price_el = ad_soup.select_one(".price")
                price = parse_price(price_el.get_text()) if price_el else None

                # Rok výroby
                year = parse_year(title)

                # Najeto
                mileage_el = ad_soup.find(string=re.compile("km"))
                mileage = parse_mileage(mileage_el) if mileage_el else None

                # Motor
                motor = ""
                motor_el = ad_soup.find(string=re.compile(r"\d\.\d"))
                if motor_el:
                    motor = motor_el.strip()

                # Palivo
                fuel = guess_fuel(motor)

                # VIN
                vin = None
                vin_el = ad_soup.find(string=re.compile("VIN", re.IGNORECASE))
                if vin_el:
                    vin = vin_el.split()[-1]

                # Převodovka
                transmission = None
                trans_el = ad_soup.find(string=re.compile("Převodovka", re.IGNORECASE))
                if trans_el:
                    transmission = trans_el.parent.get_text(strip=True).split(":")[-1]

                # Uložení do DB
                cur.execute("""
                    INSERT INTO vehicles (brand, model, year, mileage, fuel, motor, price, vin, transmission, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "Skoda",
                    title.replace("Škoda", "").strip(),
                    year,
                    mileage,
                    fuel,
                    motor,
                    price,
                    vin,
                    transmission,
                    ad_url
                ))
                conn.commit()

                print(f"✅ Uloženo: {title} ({price} Kč)")

            except Exception as e:
                print(f"⚠️ Chyba u {ad_url}: {e}")
                continue

    driver.quit()
    conn.close()
    print("🎉 Hotovo – auta uložená do vehicles_sauto.db")


if __name__ == "__main__":
    scrape_sauto()
