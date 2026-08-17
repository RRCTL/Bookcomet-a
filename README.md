# Bookcomet

> **Open-source AI accounting for document capture, intelligent coding, reconciliation, and human-reviewed draft journals.**

Bookcomet is an open-source AI accounting workspace for developers, solo operators, and small accounting teams. It turns receipts, invoices, and bank statements into **reviewable AP, AR, Bank, reconciliation, and draft GL records**—while keeping your company context, rules, and accounting decisions visible and under your control.

Bookcomet is designed for the parts of bookkeeping that consume the most time and attention: separating multiple receipts in one upload, extracting transaction rows from difficult bank statements, suggesting account codes using your company’s own knowledge, and reconciling bank activity against AR/AP records. Automation prepares structured work; users retain control over review and final journal posting.

This repository, **[RRCTL/Bookcomet-a](https://github.com/RRCTL/Bookcomet-a)**, is the public MVP.

[Website](https://bookcomet.net/) · [Quick start](#quick-start) · [Privacy](PRIVACY.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

![Bookcomet workflow: document capture to reviewed draft journals](docs/assets/bookcomet-workflow.svg)

## Why Bookcomet

Accounting automation often breaks down at the document boundary. A page may contain several receipts, a statement PDF may be image-only, a transaction table may differ by bank, or a generic model may confuse running balances with individual transactions. Bookcomet is built to address these practical workflows before they become manual data entry and reconciliation work.

| Challenge | Bookcomet workflow |
|---|---|
| **Multiple receipts in one scan** | Identify separate receipt regions, crop each document, and process each crop through VLM parsing and structured field extraction. |
| **Difficult bank-statement layouts** | Use bank-aware VLM prompts that focus on transaction dates, deposits, withdrawals, balances, descriptions, and row integrity. |
| **Inconsistent accounting classification** | Suggest AR, AP, and BANK account codes from your mode-specific Chart of Accounts, company profile, and active company rules. |
| **Time-consuming reconciliation** | Find duplicate records and propose evidence-based bank-to-ledger matches, then prepare draft GL journals. |
| **Closed and opaque financial tooling** | Run the application in your own environment, inspect the code, update company knowledge, and extend the workflow. |

> **Human review is a product boundary, not an afterthought.** Bookcomet helps prepare accounting data and draft journals. It does not replace qualified accounting, tax, legal, audit, or regulatory review.

## Document intelligence

### Process multiple receipts in one upload

A composite scan can contain two or more receipts or payment slips. Instead of applying a single generic pass to the whole page, Bookcomet can identify separate receipt regions, generate one crop per receipt, and apply VLM document parsing and structured extraction to every individual document. This creates individual, reviewable outcomes with crop-level source provenance.

For AP processing, Bookcomet can optionally use a VLM layout pass to locate receipt boxes from a page thumbnail. The response is validated for confidence, receipt count, and geometry. If the layout response is invalid or unsuitable, the workflow falls back to image-based segmentation. A user-confirmed multi-receipt flow can also attempt a forced split when automatic detection finds only one region.

| Multi-receipt step | What happens |
|---|---|
| **Detect** | Classify whether a page represents one logical document or separate receipts. |
| **Separate** | Create candidate receipt regions with optional VLM layout detection and image-based fallback logic. |
| **Validate** | Avoid low-value OCR calls by filtering malformed, tiny, invalid-aspect, and likely over-split regions. |
| **Extract** | Process each viable crop through VLM parsing and structured AP/AR field extraction. |
| **Review** | Preserve individual results, source-region context, and exceptions for review. |

This workflow is designed to improve document separation and extraction reliability for receipt batches; it does not guarantee that every scan will segment or read perfectly.

### Focus VLM extraction on bank-statement transactions

Bank statements are not generic tables. They can contain statement headers, transaction rows, running balances, opening/closing balances, aggregate summaries, portfolio sections, and multiple accounts on one page. Bookcomet’s bank pipeline focuses VLM instructions on the individual transaction table and applies bank-specific prompt logic when a supported statement layout is identified.

The workflow identifies the issuing bank where possible, selects a relevant bank-specific prompt, and applies shared extraction rules for dates, deposits, withdrawals, running balances, descriptions, and one-row-per-transaction integrity. It distinguishes transaction rows from statement-level summaries and tells the model not to calculate or invent transaction amounts. If a specific path does not produce valid rows, Bookcomet can use a general fallback extraction path.

| Bank-statement safeguard | Intended benefit |
|---|---|
| **Bank identification and targeted prompts** | Align extraction instructions to known bank layouts rather than using one generic prompt for every statement. |
| **Transaction-table focus** | Capture individual dates, deposits, withdrawals, balances, descriptions, and references while excluding summaries. |
| **Dense-page handling** | Classify page density and, when configured, adjust image handling or use chunking for difficult transaction pages. |
| **Fallback extraction** | Use a general-prompt path when a bank-specific path yields no valid transaction rows. |
| **Optional cross-VLM verification** | Compare extracted page-level totals against an independent balance/totals check and flag exceptions for review. |

Bookcomet is designed to make bank-statement extraction **more reliable**, not to promise a universal accuracy or success rate. Review low-confidence items, exceptions, and final accounting records.

## AI accounting workflows

### Suggest account codes using your company context

Bookcomet can suggest account codes for **AR, AP, and BANK** records. For each module, the AI receives the Chart of Accounts that is available for that mode, along with relevant transaction fields and active company context. The suggestion response includes an account code and confidence value; the AI is constrained to select from the available codes or return no suggestion when there is no reliable match.

The classification context can include the company profile, industry, accounting basis, fiscal-year information, Company Manual, and active company rules or knowledge articles. This lets Bookcomet reflect how a particular business handles vendors, customers, bank activity, and recurring classifications instead of relying solely on generic accounting logic.

| Module | AI coding focus |
|---|---|
| **AR** | Customer invoices, bank-in slips, received cheques, and the accounts relevant to receivables. |
| **AP** | Supplier invoices, payment slips, issued cheques, and payable/expense classifications. |
| **BANK** | Deposits, withdrawals, transaction memos, counterparties, and bank-appropriate account categories. |

### Start with company setup, then keep knowledge current

The company setup workflow can create a Company Profile and Company Manual, generate initial rule memory for each accounting mode, and create relevant bank Chart-of-Accounts entries from setup details. This gives the AI a business-specific baseline before documents are processed.

Each module has its own **versioned rule memory**. Teams can review, edit, import, export, restore, activate, or deactivate its content through dedicated knowledge-management actions. This makes business rules transparent and auditable rather than hidden inside an opaque model prompt.

| Knowledge source | How Bookcomet uses it |
|---|---|
| **Company Profile and Company Manual** | Provides business background for accounting and account-code suggestions. |
| **Mode-specific Rule Memory** | Supplies reusable vendor, keyword, default, and AI instruction rules for AR, AP, BANK, and other supported modes. |
| **Chart of Accounts** | Restricts account-code suggestions to the codes configured and allowed for the relevant module. |
| **User feedback through AI Chat** | Can observe repeated classification corrections and propose a reusable rule for explicit user confirmation. |

### How learning from account-code corrections works

Bookcomet does **not** silently convert every one-off table correction into a permanent accounting rule. When an account-code, category, or transaction-type edit is made through the AI Chat workflow, Bookcomet can observe repeated vendor patterns and ask whether the user wants to save a reusable rule. The rule is stored only after explicit confirmation, or when the user directly asks the assistant to remember or save it.

This safety model prevents an accidental or exceptional **AP, AR, or BANK** correction from changing future classification behavior without the user’s knowledge.

> **Important:** Directly editing an `account_code` in an **AP, AR, or BANK** module table and pressing **Save** persists the transaction and synchronizes it to the reconciliation workflow. It does **not** automatically update that module’s Rule Memory. To make the correction reusable, save or confirm it through the knowledge/rule workflow.

## AI-assisted reconciliation and controlled posting

Bookcomet’s reconciliation assistant analyzes selected bank and ledger records to identify duplicate bank records and propose bank-to-ledger matches. It uses amount equality as a hard condition, then considers reference or voucher evidence, date proximity, and counterparty or memo similarity. Server-side validation rejects invalid IDs, duplicate pairings, fabricated records, and unequal-amount matches.

When a user starts **AI Match**, Bookcomet processes eligible candidates and prepares valid reconciliation groups. It can then create or ensure a **draft** GL journal for the result. A draft is not a posted journal: an authenticated user must review the result and take the separate posting action when it is approved.

| Control | What it protects |
|---|---|
| **Equal-amount requirement** | Avoids matching two records that do not represent the same economic event. |
| **Real transaction IDs only** | Prevents invented or untraceable reconciliation pairs. |
| **Duplicate and reuse checks** | Prevents one bank or ledger item from being used in multiple proposed pairs. |
| **Draft-journal workflow** | Keeps generated journal entries reviewable and editable before posting. |
| **Explicit posting action** | Ensures a user—not the AI—makes the final decision to post a journal. |

## From source documents to reviewable journals

```text
Receipts / invoices / bank statements / CSV
                    │
                    ▼
        VLM and OCR document extraction
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
Multi-receipt separation   Bank-aware transaction extraction
       │                         │
       └────────────┬────────────┘
                    ▼
     Editable AP / AR / Bank transaction grids
                    │
                    ▼
  Company-aware account-code suggestions and review
                    │
                    ▼
  AI-assisted reconciliation with validation safeguards
                    │
                    ▼
        Draft GL journals → user review → explicit post
```

## Core workspace modules

| Module | What it supports |
|---|---|
| **Processing** | PDF, image, and CSV intake with OCR/VLM-assisted extraction. |
| **Accounts Payable** | Invoice and payment capture, composite-receipt handling, account-code suggestions, editable review, and approval flows. |
| **Accounts Receivable** | Receivables capture, document review, account-code suggestions, and approval workflows. |
| **Bank** | Bank-statement parsing, bank-focused VLM extraction, account-code suggestions, and transaction review. |
| **Reconciliation** | Duplicate checks, bank-to-ledger matching, AI-assisted candidate processing, draft-journal creation, and controlled user posting. |
| **General Ledger** | Review and management of draft journal entries produced by reviewed workflows. |
| **Company setup and knowledge** | Multi-company context, Chart of Accounts, Company Manual, rules, and versioned rule memory. |
| **Workflow canvas** | Node workflows, skills, batch files, and run history. |

## Who Bookcomet is for

Bookcomet is intended for **developers who want control over their accounting stack**, **solo founders who need to reduce document-entry work**, and **small accounting firms that need adaptable document review, coding, and reconciliation workflows**.

It is not a substitute for professional accounting, tax, legal, audit, or regulatory advice. Do not rely on any output as final statutory, tax, or financial-reporting data without appropriate review and approval for your jurisdiction.

## Technology stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11, FastAPI, SQLAlchemy, Alembic |
| **Local development data store** | SQLite |
| **Optional services** | PostgreSQL and Redis |
| **Frontend** | Node.js 22 LTS, React 19, TypeScript, Vite |
| **Document intelligence** | DeepSeek-compatible OCR/LLM providers and configurable VLM models |
| **File storage** | Local storage or S3-compatible storage |

## Quick start

The local-development instructions start the backend and frontend separately. Windows PowerShell and Unix shells are both shown.

### Prerequisites

Install Python **3.11**, Node.js **22 LTS**, and Git.

```bash
git clone https://github.com/RRCTL/Bookcomet-a.git
cd Bookcomet-a
```

### Start the backend

```powershell
# Windows PowerShell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy config.env.example .env
alembic upgrade head
python run.py
```

```bash
# Linux / macOS
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp config.env.example .env
alembic upgrade head
python run.py
```

The API starts at [`http://localhost:8000`](http://localhost:8000), with interactive documentation at [`http://localhost:8000/docs`](http://localhost:8000/docs).

### Start the frontend

Open a second terminal and run:

```powershell
# Windows PowerShell
cd frontend
npm install
copy .env.example .env
npm run dev
```

```bash
# Linux / macOS
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open [`http://localhost:5173`](http://localhost:5173) in your browser.

## Security and configuration

Bookcomet handles sensitive financial documents. Treat deployment and provider configuration as part of the security boundary.

**Cloud vs local AI:** Default VLM/LLM settings use an OpenAI-compatible gateway. When cloud OCR / AI is configured, uploaded document images, OCR content, and necessary company profile data are sent to that provider. Point `VLM_BASE_URL` / `LLM_BASE_URL` (or Settings → API) at a local endpoint to keep data on this device. Bookcomet is not a fully offline product unless you choose local endpoints and local storage.

This notice also appears in Settings → API, company onboarding before Generate Company Profile, and the document upload surfaces. See [`PRIVACY.md`](PRIVACY.md) for local storage, retention, and deletion behavior.

| Area | Location | Required action |
|---|---|---|
| Backend secrets and AI/provider settings | `backend/.env` | Copy `backend/config.env.example` and complete values appropriate for your environment. |
| Frontend API endpoint | `frontend/.env` | Copy `frontend/.env.example` and configure the API URL. |
| Local baseline | `LOCAL_DEV_SETUP.md` | Complete **SEC-OPS-001** after creating `backend/.env`: use a strong `JWT_SECRET_KEY` and bind local services to loopback. |
| Local registration | `backend/.env` | For pure local development, leave `REGISTER_INVITE_CODE` empty. Set an invite only before exposing a public tunnel. |
| VLM receipt-layout detection | `backend/.env` | Configure `AP_VLM_LAYOUT_CROP_ENABLED` only when you want the optional VLM-first receipt-layout path for AP. |
| Bank-statement cross-check | `backend/.env` | Configure `BANK_CROSS_VLM_VERIFY` and `BANK_CROSS_VLM_MODEL` only when you want optional cross-VLM balance/totals checks. |
| Further hardening | `LOCAL_DEV_SETUP.md` | Review SQLCipher (**SEC-CODE-007**), MFA (**SEC-CODE-009**), and operational controls (**SEC-OPS-004**) when applicable. |

**Never commit** real `.env` files, local databases, uploads, logs, caches, or credentials. See [`LOCAL_DEV_SETUP.md`](LOCAL_DEV_SETUP.md) for detailed Windows setup, tunnel configuration, security checklists, and troubleshooting.

## Development checks

Run these checks before opening a pull request or sharing a build:

```powershell
# Backend
cd backend
pytest

# Frontend
cd frontend
npm run lint
npm run build
```

## Database migrations

Bookcomet uses Alembic for schema changes. Do not treat runtime databases or local SQLite files as the schema source of truth.

```powershell
cd backend
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Contributing

Follow [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Never commit real receipts, bank statements, customer data, `.env` files, databases, or secrets.

Before submitting a change, run the relevant backend tests, frontend linting, and frontend build. Store durable documentation in `docs/` or the relevant feature directory, such as `backend/docs/`.

## Support and security reporting

Use [GitHub Issues](https://github.com/RRCTL/Bookcomet-a/issues) for reproducible defects, feature requests, and integration proposals. A useful report includes the Bookcomet-a commit, operating system, sanitized configuration, steps to reproduce, expected behavior, and actual behavior.

Do not post credentials, financial documents, or security vulnerabilities in public issues. See [`SECURITY.md`](SECURITY.md) for private reporting.

## License

Bookcomet is released under the **[Apache License, Version 2.0](LICENSE)** (`Apache-2.0`). You may use, modify, and distribute the software—including in commercial products—subject to the license terms, preservation of required notices, and applicable attribution requirements.

Third-party attribution is recorded in [`NOTICE`](NOTICE). The Apache-2.0 license does not grant permission to use third-party trademarks or names as though they endorse Bookcomet.

---

**Capture documents. Classify with your company context. Reconcile with safeguards. Review before you post.**
