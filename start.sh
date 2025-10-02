#!/usr/bin/env bash
set -e

# pokud je DB zabalena kvůli limitu 100 MB, rozbalíme ji
if [ -f vehicles_ai.zip ] && [ ! -f vehicles_ai.db ]; then
python - <<'PY'
import zipfile
z = zipfile.ZipFile('vehicles_ai.zip')
z.extract('vehicles_ai.db')
print('DB extracted')
PY
fi

exec uvicorn app.api_server:app --host 0.0.0.0 --port \
