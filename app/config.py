import os

# Kde leží SQLite soubor
DB_PATH = os.environ.get("DB_FILE", os.path.abspath("./vehicles_ai.db"))

# Povolené originy pro CORS (pro vývoj necháváme *, v produkci dodej svůj domain)
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

# Jednoduchý API klíč (volitelné). Když není nastaveno, auth se neřeší.
API_KEY = os.environ.get("API_KEY", "")

# Výchozí stáří dat (v hodinách) – u seed view s 'n/a' se ignoruje
FRESH_HOURS_DEFAULT = int(os.environ.get("FRESH_HOURS_DEFAULT", "720"))
