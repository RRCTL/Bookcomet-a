"""
Hang Seng Bank (Hong Kong) — commercial / business statement VLM prompts and keywords.

Layout is modelled on HSBC Business Direct–style grids: Date, transaction details,
Deposit / Withdrawal, Balance (HKD and FCY sections may add a CCY column).
Statement header supplies year/month; rows often use partial English month labels (DD Mon).
"""

from ._shared import UNIVERSAL_RULES

KEYWORDS: list[str] = [
    "Hang Seng Bank",
    "HANG SENG BANK",
    "\u6052\u751f\u9280\u884c",  # 恒生銀行
    "\u6052\u751f",  # 恒生
    "www.hangseng.com",
    "HASE",
]

PROMPT: str = """You are an expert at reading Hang Seng Bank (Hong Kong) account statements.

TASK: Extract every transaction row from every account activity section on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

━━━ FIRST: CHECK WHETHER A TRANSACTION TABLE EXISTS ON THIS PAGE ━━━
Before extracting anything, scan for a printed column header row that belongs to an
ACCOUNT ACTIVITY / transaction grid. Typical Hang Seng English headers include ALL of:
  Date (or Transaction Date)
  A description column (Transaction Details / Particulars / Narrative)
  Deposit AND Withdrawal (or Credit/Debit, Money In/Out), AND usually Balance

Typical Chinese headers may use \u5b58\u5165 (deposit) and \u652f\u51fa or \u63d0\u53d6 (withdrawal).

• If that full activity header pattern IS present → extract transaction rows below it.
• If it is ABSENT → this page is a cover, summary, legal notice, FX rate table, or other
  non-activity page. Output { "bank_id": "HANG_SENG", "account_no": null, "transactions": [] } and stop.

Content-based only (not page number). A large balance, account name, or marketing text
does NOT mean there is a transaction table — require the printed column headers above.

━━━ TABLE LAYOUT (typical) ━━━
HKD sections (left to right):
  Column 1 — Date: often "DD Mon" (e.g. "6 Dec") once per date group, or full date.
  Column 2 — Details: description; may span multiple lines — join with a single space.
  Column 3 — Deposit: money IN; blank if none.
  Column 4 — Withdrawal: money OUT; blank if none.
  Column 5 — Balance: running balance when printed.

Foreign currency sections may have an extra CCY column before Date — same rules; set
"currency" from the row/section (e.g. USD, EUR) when visible.

━━━ DATE RULES (same authority as HSBC — statement header on THIS page) ━━━
• Partial row dates are often "DD Mon" with no year. Read the statement period / date
  printed in the header band of THIS page (top area), e.g. "21 July 2022" or "31 December 2023".
• FORBIDDEN: do not use today's date, model cutoff, or an assumed year. Only the printed header.
• Let Y = header year, M = header month number (1–12). For each row date with month m:
    - If m > M → transaction year = Y − 1
    - If m ≤ M → transaction year = Y
• Abbreviated months: Jan–Dec → 01–12.
• Date groups: the date label may print only on the first row of a day — copy that
  transaction_date to every following row until the next date label.
• Do not treat reference codes inside descriptions as dates.

━━━ BALANCE COLUMN ━━━
• Prefer printed balance per row. If Hang Seng leaves the balance cell blank on some rows
  but prints it on the last transaction of a date group, use null for intermediate rows
  (same pattern as HSBC day-end balance). If every row has a balance, output all.
• NEVER compute balance from arithmetic. B/F rows: deposit and withdrawal null; balance
  from the printed opening figure.

━━━ AMOUNTS ━━━
• Exactly one of deposit or withdrawal is non-null per transaction row (except pure B/F).
• Read printed numbers only from the deposit/withdrawal columns — never from balance.

━━━ account_type ━━━
• Use the printed section title above the table (e.g. HKD Current, HKD Savings, FCY account
  name). Reset when a new section starts on the page.

""" + UNIVERSAL_RULES + """
OUTPUT FORMAT
{
  "bank_id": "HANG_SENG",
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

# Reserved for a future prescan+V2 pipeline (descriptions only).
PROMPT_V2: str = """
You are reading ONE page of a Hang Seng Bank (Hong Kong) statement.

Numerical amounts are handled separately — DO NOT output numbers from amount columns.

For each transaction row that has a printed deposit OR withdrawal, output:
  "y_pct" — row top as percent of page height (0–100).
  "description" — full details text; join wrapped lines with a space.
  "date_label" — "DD Mon" or full date as printed, or "".
  "account_type" — section label if visible, else "".

Rules:
1. One entry per row with a deposit or withdrawal amount.
2. If there is no activity table on this page, return {"rows": []}.
3. No numeric amounts in JSON.

Output valid JSON only:
{"rows": [{"y_pct": <float>, "description": "<string>", "date_label": "<string>", "account_type": "<string>"}]}
""".strip()

HANG_SENG_AR_MANAGER_PROMPT_PREFIX: str = """
You are auditing ONE page of a Hang Seng Bank (Hong Kong) statement. You receive:
(1) the page image, and (2) BOOKKEEPER_DRAFT_JSON — the bookkeeper's rows for this page
(same order as printed transactions). Your job is the BALANCE column ONLY.

Output ONLY valid JSON (no markdown): { "bank_id": "HANG_SENG", "transactions": [ ... ] }

--- STEP 1: TABLE HEADER CHECK ---
Look for a real account activity grid: Date + description + BOTH deposit/credit AND
withdrawal/debit style columns in context with Balance.
If that header row is ABSENT (cover, summary, legal page, FX rates only, etc.), output
{ "bank_id": "HANG_SENG", "transactions": [] } and stop. Do not invent rows from summary balances.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per row (idx 0, 1, ...). You MUST output exactly the SAME
number of objects in "transactions", in the SAME order. Do not add or remove rows.

--- B/F ROWS ---
Opening rows (B/F, 承前, Brought Forward) may have blank deposit and withdrawal.
Keep deposit and withdrawal null. Set balance to the printed opening balance for that row only.

--- READING BALANCE ---
For EACH object:
• deposit — ALWAYS null.
• withdrawal — ALWAYS null.
• balance — read from the Balance column for that row; null if the cell is blank.
• Do not compute balances by arithmetic.
• transaction_date, description, account_type — align with BOOKKEEPER_DRAFT_JSON for the same idx.

The downstream merge only copies your balance field to fill gaps; amounts stay with the bookkeeper.
""".strip()
