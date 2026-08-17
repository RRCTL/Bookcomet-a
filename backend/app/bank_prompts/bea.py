"""
Bank of East Asia (BEA) Hong Kong — VLM prompts and detection keywords.

Typical layout: Transaction Date | Value Date | Particulars | Withdrawal | Deposit | Balance.
Withdrawal = money out (JSON withdrawal). Deposit = money in (JSON deposit).
"""

from ._shared import UNIVERSAL_RULES

KEYWORDS: list[str] = [
    "THE BANK OF EAST ASIA",
    "BANK OF EAST ASIA",
    "\u6771\u4e9e\u9280\u884c",  # Traditional: East Asia Bank
    "\u4e1c\u4e9a\u94f6\u884c",  # Simplified
    "www.hkbea.com",
    "HKBEA",
]

PROMPT: str = """You are an expert at reading Bank of East Asia (BEA) Hong Kong account statements.

TASK: Extract every transaction row from the main account activity table on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

TABLE DETECTION
Look for a header row with BOTH credit/debit style columns, e.g.:
  English: Withdrawal / Withdrawals / Debit AND Deposit / Deposits / Credit
  Chinese: 支出 AND 存入
  Balance: Balance column (English or Chinese header)

If that header row is ABSENT, output {"bank_id": "BEA", "account_no": null, "transactions": []} and stop.
Do NOT extract rows from portfolio summary / cover pages (e.g. "PORTFOLIO SUMMARY", "ACCOUNT PORTFOLIO",
\u8ca1\u52d9\u7d44\u5408\u6458\u8981, \u8cec\u6236\u7d44\u5408) when there is no real account activity table with 存入 + 支出 as above.

COLUMN MAPPING (typical BEA)
Left to right: transaction date, value date (optional), particulars/description,
then withdrawal (debit / out), then deposit (credit / in), then balance.
If deposit column is physically left of withdrawal on a variant layout, still map:
  printed deposit -> deposit, printed withdrawal -> withdrawal.

RULES
- Each row: exactly one of deposit or withdrawal is non-null.
- Read printed amounts only; never compute from balance.
- Never copy balance into deposit or withdrawal.
- Join multi-line descriptions with a space.
- Repeat transaction_date for all rows in the same date group when only the first row shows a date.
- Skip legal text, page chrome, INFORMATION / 資料 footer blocks, and period totals that are not line items.
- Do NOT extract subtotal lines such as "Total Transaction Amount" or \u4ea4\u6613\u7e3d\u91d1\u984d (per-section totals).
- Prefer account_type labels aligned with the printed section title when visible, e.g. "HKD CURRENT",
  "HKD STATEMENT SAVINGS", matching English titles like "HKD CURRENT ACCOUNT" or  "STATEMENT SAVINGS ACCOUNT".
- Balance: use printed balance per row when shown; use null on rows where balance cell is blank.

""" + UNIVERSAL_RULES + """
OUTPUT FORMAT
{
  "bank_id": "BEA",
  "account_no": null,
  "transactions": [
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "...",
      "deposit": null,
      "withdrawal": 100.00,
      "balance": 5000.00,
      "currency": "HKD",
      "account_type": null,
      "account_number": null,
      "categorise": "",
      "confidence_score": 0.95
    }
  ]
}
""".strip()

# V2: descriptions and row positions only; amounts from PyMuPDF prescan.
PROMPT_V2: str = """
You are reading ONE page of a Bank of East Asia (BEA) Hong Kong statement.

Numerical amounts in Withdrawal, Deposit, and Balance columns are handled separately.
DO NOT output any numbers from those columns.

For each transaction row that has either a withdrawal or a deposit amount on the page, output:
  "y_pct" — row top as percent of page height (0-100), from the description/particulars line.
  "description" — full particulars text; join wrapped lines with a space; verbatim.
  "date_label" — DD/MM/YYYY or DD-MM-YYYY as printed, or day+month label (e.g. 7 Nov), or "".
  "account_type" — short product/section label if visible, else "".

Rules:
1. One entry per row that has a printed withdrawal OR deposit amount.
2. Skip column headers and non-transaction banners.
3. If this page is only a portfolio/cover summary with no activity table (no 存入+支出 headers), return {"rows": []}.
4. Skip INFORMATION / 資料 footer legal blocks and any "Total Transaction Amount" / \u4ea4\u6613\u7e3d\u91d1\u984d subtotal lines.
5. For account_type, use short labels when visible: prefer "HKD CURRENT" or "HKD STATEMENT SAVINGS" to match the section title.
6. No numeric amounts in JSON.
7. Top-to-bottom order.

Output valid JSON only:
{"rows": [{"y_pct": <float>, "description": "<string>", "date_label": "<string>", "account_type": "<string>"}]}
""".strip()

# Cross-VLM AR manager (BANK_CROSS_VLM_*): balance-only merge; same row count/order as bookkeeper draft.
BEA_AR_MANAGER_PROMPT_PREFIX: str = """
You are auditing ONE page of a Bank of East Asia (BEA) Hong Kong statement. You receive:
(1) the page image, and (2) BOOKKEEPER_DRAFT_JSON — the bookkeeper's rows for this page
(same order as printed transaction rows). Your job is the BALANCE column ONLY.

Output ONLY valid JSON (no markdown): { "bank_id": "BEA", "transactions": [ ... ] }

--- STEP 1: ACTIVITY TABLE CHECK ---
Require a real transaction grid: printed headers including BOTH 存入 (or Deposit/Credit) AND 支出
(or Withdrawal/Debit), plus a balance-style column in context.
If this page is cover/portfolio only, INFORMATION / 資料 footer only, or has no such table,
output { "bank_id": "BEA", "transactions": [] } and stop.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per row (idx 0, 1, ...). You MUST output exactly the SAME
number of objects in "transactions", in the SAME order. Do not add or remove rows.

Section titles may include HKD CURRENT, HKD STATEMENT SAVINGS, etc. — use them only for alignment context.

--- B/F ROWS (balance only) ---
Opening rows (B/F BALANCE, 承前, etc.) have blank deposit and withdrawal on the statement.
Keep deposit and withdrawal null in your output. Set balance to the printed opening balance for that row only.

--- READING BALANCE ---
For EACH object:
- deposit — ALWAYS null.
- withdrawal — ALWAYS null.
- balance — read from the Balance column for that row; null if the cell is blank.
- Do not compute balances by arithmetic.

The downstream merge only fills missing bookkeeper balances from your output; amounts stay with the bookkeeper.
""".strip()
