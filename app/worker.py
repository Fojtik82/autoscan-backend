import time
import schedule
import subprocess

# Funkce, která spustí scraper
def run_scraper():
    print("🚀 Spouštím scraper sauto.py...")
    try:
        subprocess.run(["python", "sauto.py"], check=True)
        print("✅ Scraper dokončen")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Chyba při spuštění scraperu: {e}")

# Nastavení intervalu
# Každých 6 hodin
schedule.every(6).hours.do(run_scraper)

print("👷 Worker běží – scraper se bude spouštět každých 6 hodin.")

# Nekonečný loop
while True:
    schedule.run_pending()
    time.sleep(60)
