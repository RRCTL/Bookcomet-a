# Step 4 — Clean-machine install

Recorded 2026-08-17 from a **new empty directory** (`/tmp/bookcomet-clean`) using only README commands via `scripts/clean_machine_verify.sh`. No private files or machine-local paths were copied in.

| Step | Result |
|---|---|
| Fresh tree (no existing `.env`) | yes |
| `python3 -m venv`, `pip install -r backend/requirements.txt` | ok |
| Copy `config.env.example` → `backend/.env` | ok |
| Generate a new `JWT_SECRET_KEY` (not committed) | ok |
| `alembic upgrade head` | applied through `a0b1c2d3e4f5` |
| Backend `pytest` | **420 passed** |
| Copy `frontend/.env.example` → `frontend/.env` | ok |
| `npm ci`, `npm run lint` | 0 errors (44 pre-existing hook warnings) |
| `npm run test:run` | **144 passed** |
| `npm run build` | ok |
| Cloud-AI notice in Settings + Processing source | `CLOUD_AI_NOTICE_OK` |

The generated `.env` files and SQLite database stayed in the throwaway directory and were **not** committed.

## Repeat

```bash
git clone https://github.com/RRCTL/Bookcomet-a.git
cd Bookcomet-a
./scripts/clean_machine_verify.sh
```

Then start the API (`python run.py` in `backend`) and UI (`npm run dev` in `frontend`) and confirm Settings → API plus the upload surfaces show the cloud-AI notice.
