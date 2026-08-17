# Local Development Setup

Start the **backend** first, then the **frontend**.

## Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy config.env.example .env
alembic upgrade head
python run.py
```

API: `http://localhost:8000`  
Docs: `http://localhost:8000/docs`

## Frontend

```powershell
cd frontend
npm install
copy .env.example .env
npm run dev
```

UI: `http://localhost:5173`

## Configuration

- Backend secrets and provider settings: `backend/.env` (from `backend/config.env.example`)
- Frontend API URL: `frontend/.env` (from `frontend/.env.example`)
- Never commit real `.env` files, local databases, uploads, logs, or caches

### SEC-OPS-001 — required security setup (every local PC)

Do this **after** `copy config.env.example .env` and **before** day-to-day use. Each machine must generate its own secrets. Do not copy someone else’s `.env`, and never commit `.env`.

**Always (local PC)**

1. Generate a strong JWT secret and set it in `backend/.env`:

```powershell
# Windows PowerShell
[Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
```

```bash
# Linux / macOS
openssl rand -base64 64 | tr -d '\n'
```

```env
JWT_SECRET_KEY=<paste-generated-value>
ALLOW_INSECURE_DEV_JWT=false
APP_ENV=local
HOST=127.0.0.1
REGISTER_INVITE_CODE=
```

**Pure local (recommended):** leave `REGISTER_INVITE_CODE` empty. The Create account page will **not** ask for an invite code. There is no invite code in GitHub — you only create one later if you open a public tunnel (see below).

2. Put real `VLM_API_KEY` / `LLM_API_KEY` (and optional `AI_ENHANCE_API_KEY`) only in `backend/.env`. Enable spend/rate limits in the provider console.
3. Keep `HOST=127.0.0.1` so the API is not reachable from the LAN unless you intentionally change it.
4. Restrict file access to your user account:

```powershell
# Windows
icacls "backend\.env" /inheritance:r /grant:r "%USERNAME%:F"
icacls "backend\uploads" /inheritance:r /grant:r "%USERNAME%:F"
```

```bash
# Linux / macOS
chmod 600 backend/.env
chmod 700 backend/uploads
```

5. Confirm secrets are not tracked:

```powershell
git ls-files | Select-String -Pattern '\.env$|\.db$|uploads/'
# Expect no matches
```

6. Back up `backend/.env` and the SQLite DB only with encryption; do not store the backup password next to the archive.

**Before enabling a Cloudflare / public tunnel**

1. Generate an invite code (you create it; it is not shipped in the repo). Put it in `backend/.env`, restart the API, then share that same value with trusted users who need to register:

```powershell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
```

```bash
# Linux / macOS
openssl rand -base64 32 | tr -d '\n'
```

```env
REGISTER_INVITE_CODE=<paste-generated-value>
```

To see the code again later: open `backend/.env` and copy the `REGISTER_INVITE_CODE=` line (only people with access to that file should see it).

2. Keep the API on loopback (`HOST=127.0.0.1`). Point the tunnel at the local UI/API ports; do not bind the API to `0.0.0.0` just to use a tunnel.
3. Set `CORS_ORIGINS` to your real UI origins only (localhost and/or the HTTPS tunnel hostname). Do not use `*`.

```env
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://your-app.example.com
```

4. Set `TRUST_FORWARDED_HEADERS=true` only when all public traffic reaches the API through that reverse proxy/tunnel.
5. Share the invite code only with trusted users; turn the tunnel off when idle.
6. If `.env` or a key may have leaked: rotate `JWT_SECRET_KEY`, `REGISTER_INVITE_CODE`, and provider API keys immediately.

**Boot gates (SEC-CODE-001 / SEC-CODE-002)** — the API refuses to start when:

- `JWT_SECRET_KEY` is missing, is the old weak default, or is shorter than 32 characters (unless `ALLOW_INSECURE_DEV_JWT=true` on pure localhost only).
- Exposure looks tunnel-like (non-loopback `HOST`, `TRUST_FORWARDED_HEADERS=true`, or non-localhost `CORS_ORIGINS`) and `REGISTER_INVITE_CODE` is empty.
- `CORS_ORIGINS` contains `*` (SEC-CODE-003).

Uploads are limited to document types with matching magic bytes (SEC-CODE-004). Logs redact common secret patterns (SEC-CODE-005). CI runs `pip-audit` (SEC-CODE-006).

### SEC-CODE-007 — optional SQLite encryption (SQLCipher)

Protects the local DB file if the laptop is stolen or the disk is copied. Skip this if you only use ephemeral test DBs.

1. Generate a DB password (≥16 characters) and put it in `backend/.env`:

```env
DATABASE_PASSWORD=<openssl rand -base64 32>
```

2. If you already have a plaintext `ai_accounting.db`, migrate it **before** restarting the API:

```powershell
cd backend
python scripts/encrypt_sqlite_db.py --replace
```

3. Restart the API. New empty DBs are created encrypted automatically when `DATABASE_PASSWORD` is set.
4. Store `DATABASE_PASSWORD` with the same care as `JWT_SECRET_KEY`. Losing it means the DB file cannot be opened.

### SEC-OPS-002 — secret storage and rotation (every local PC)

1. Prefer keeping API keys and passwords only in `backend/.env` with restrictive ACLs (`icacls` / `chmod 600`) — see SEC-OPS-001.
2. Optional: store `JWT_SECRET_KEY`, `DATABASE_PASSWORD`, and provider API keys in the OS secret store (Windows Credential Manager / DPAPI, macOS Keychain, or a password manager) and paste into `.env` only on that machine — do not sync `.env` via cloud folders.
3. Rotation policy (do this immediately after any suspected leak; otherwise at least quarterly):
   - Rotate `JWT_SECRET_KEY` (signs everyone out).
   - Rotate `REGISTER_INVITE_CODE` if you use a tunnel.
   - Rotate `DATABASE_PASSWORD` only with a planned re-encrypt/migrate (export → new encrypted DB).
   - Rotate VLM/LLM provider keys in the vendor console and update `.env`.
4. Session revoke: use **Log out** for this browser, or **Sign out all sessions** in Account settings (`POST /auth/revoke-sessions`, SEC-CODE-008) to invalidate refresh + access tokens. Changing `JWT_SECRET_KEY` also invalidates all access tokens immediately. Tenant APIs and workflow WebSockets also honor revoke (SEC-CODE-010). Do not put JWTs in WebSocket query strings (SEC-CODE-013). Workspace memberships can only be created for the active company (SEC-CODE-011). Process-wide keys such as `JWT_SECRET_KEY` cannot be rewritten from the in-app env editor (SEC-CODE-012).
5. Optional MFA (SEC-CODE-009): in Account settings → **Set up authenticator**, scan/add the `otpauth` URL in an authenticator app, confirm with a 6-digit code. Recommended when you keep a public tunnel on for long periods.

### SEC-OPS-004 — ongoing ops (Phase 3)

Do these on a schedule; they are not one-time setup.

1. **After any suspected leak** of tunnel URL or `.env`: rotate `JWT_SECRET_KEY`, `REGISTER_INVITE_CODE`, provider API keys, and (if used) `DATABASE_PASSWORD` with a planned re-encrypt.
2. **About every 90 days:** rotate VLM/LLM provider keys in the vendor console; update `backend/.env`.
3. **On dependency upgrades:** ensure CI `pip-audit` (SEC-CODE-006) is green; bump pinned packages when advisories appear.
4. **Tunnel hygiene:** keep the Cloudflare/public tunnel **off** when idle; leave pure-local invite empty (`REGISTER_INVITE_CODE=`).
5. **Quarterly re-audit** (target ~every 3 months): re-read this checklist, confirm loopback bind, CORS origins, invite/MFA for any shared access, encrypted backups, and that no `.env`/`.db`/uploads are tracked in git.

### Optional UI flags (`frontend/.env`)

- Default after login: Comfy-style `NodeWorkspace` (tabs + graph). OCR runs via `POST /api/workflows/runs/{id}/execute`.
- `VITE_LEGACY_WORKSPACE=1` — restore the old chat workspace.
- `VITE_UI_THEME=erp` — ERP shell theme.

### Production-style builds

If `VITE_API_URL` is not set at build time, the SPA calls `/api/...` on the same hostname as the page. A reverse proxy or tunnel must forward `/api` to FastAPI, or set `VITE_API_URL` to your API base (see `frontend/.env.example`).

## Optional Cloudflare tunnel

Complete the **SEC-OPS-001 — before enabling a tunnel** checklist above first.

Quick tunnel helpers live under `dev/cloudflare-tunnel/`. Copy `config.example.yml` for a named-tunnel layout. Do not commit `tunnel-credentials.json`.

A **502** from Cloudflare usually means the tunnel origin is wrong or Vite is not running on the expected port (`5173` for the app, `8000` for the API). Confirm `http://127.0.0.1:5173` works locally before debugging the tunnel.

## Smoke checks

```powershell
# backend
cd backend
pytest

# frontend
cd frontend
npm run lint
npm run typecheck
npm run build
```

Stop servers with **Ctrl+C**.
