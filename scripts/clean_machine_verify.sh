#!/usr/bin/env bash
# Step 4 helper: README-only install in THIS directory. Use a fresh clone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f backend/.env || -f frontend/.env ]]; then
  echo "Refusing to run: this tree already has a .env. Use a fresh clone." >&2
  exit 1
fi

JWT="$(openssl rand -base64 64 | tr -d '\n')"

python3 -m venv backend/.venv
# shellcheck disable=SC1091
source backend/.venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
pip install pytest pytest-asyncio
cp backend/config.env.example backend/.env
python - "$JWT" <<'PY'
from pathlib import Path
import sys
key = sys.argv[1]
path = Path("backend/.env")
lines = path.read_text().splitlines()
out, found = [], False
for line in lines:
    if line.startswith("JWT_SECRET_KEY="):
        out.append("JWT_SECRET_KEY=" + key)
        found = True
    else:
        out.append(line)
if not found:
    out.append("JWT_SECRET_KEY=" + key)
path.write_text("\n".join(out) + "\n")
PY

export APP_ENV=local
export DATABASE_URL=sqlite:///./ai_accounting.db
export JWT_SECRET_KEY="$JWT"
export PYTHONPATH=.
(
  cd backend
  alembic upgrade head
  pytest -q --tb=line
)

cp frontend/.env.example frontend/.env
(
  cd frontend
  npm ci
  npm run lint
  npm run test:run
  npm run build
)

python - <<'PY'
from pathlib import Path
needles = [
    Path("frontend/src/constants/privacyNotices.ts"),
    Path("frontend/src/components/settings/ApiSettingsPanel.tsx"),
    Path("frontend/src/features/erpShell/ProcessingView.tsx"),
]
text = "\n".join(p.read_text() for p in needles)
assert "CLOUD_AI_DATA_NOTICE" in text or "cloud OCR" in text.lower()
print("CLOUD_AI_NOTICE_OK")
PY

echo "CLEAN_MACHINE_VERIFY_OK"
