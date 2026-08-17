"""
Standard Chartered Bank (Hong Kong) — SCB HK specific VLM prompt and detection keywords.
"""
from ._shared import UNIVERSAL_RULES

KEYWORDS: list[str] = [
    '渣打', '渣打銀行', 'Standard Chartered', 'STANDARD CHARTERED',
    'Standard Chartered Bank', 'SCB', 'SC Bank',
    'Standard Chartered Bank (Hong Kong)',
]

PROMPT: str = """You are an expert at reading Standard Chartered Bank (Hong Kong) bank statements.

TASK: Extract every transaction row from every account section on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

━━━ SC TABLE FORMAT ━━━
Each account section has a transaction table with columns (left to right):
  Column 1 — DATE          : transaction date in DD/MM/YYYY format (e.g. 01/02/2021)
  Column 2 — DESCRIPTION   : transaction description / particulars (may wrap to multiple lines)
  Column 3 — DEBIT         : amount debited (money OUT / withdrawal); blank when not applicable
  Column 4 — CREDIT        : amount credited (money IN / deposit); blank when not applicable
  Column 5 — BALANCE       : running balance after this transaction

━━━ SC-SPECIFIC RULES ━━━
• DATE format: DD/MM/YYYY → YYYY-MM-DD (e.g. 01/02/2021 → 2021-02-01).
  Some pages may use DD MMM YYYY (e.g. 01 FEB 2021) — convert to YYYY-MM-DD as well.
• DEBIT column (Column 3) = withdrawal (money leaving the account).
• CREDIT column (Column 4) = deposit (money entering the account).
• CURRENCY: read from the account section header (HKD, USD, CNY, etc.).
  If the header says "HKD Current Account 港元支票戶口", the currency is HKD.
  If the header says "HKD Savings Account 港元儲蓄戶口", the currency is HKD.
• SKIP these summary/header rows (not **Balance Brought Forward** — see override after universal rules):
  OPENING BALANCE, CLOSING BALANCE, generic BROUGHT FORWARD (other wording),
  CARRIED FORWARD, TOTAL DEBIT, TOTAL CREDIT, B/F, C/F, SUB-TOTAL,
  BALANCE CARRIED FORWARD.

━━━ COLUMN AMOUNTS — READ DIRECTLY, NEVER COMPUTE ━━━
• Column 3 (DEBIT) and Column 4 (CREDIT) values are PHYSICALLY PRINTED in their own cells on
  the page. ALWAYS read the printed number from Column 3 for withdrawal and Column 4 for
  deposit. Do NOT derive these values through any arithmetic.
• NEVER compute a deposit or withdrawal as a balance difference — not even as
  new_balance − old_balance within the SAME section, and certainly NEVER across sections.
• If Column 3 is blank for a row, withdrawal = null. If Column 4 is blank, deposit = null.
  Only output null when the cell is genuinely empty — not because you computed zero.
• Each account section has its OWN OPENING BALANCE (or B/F) printed at the top of that
  section's table. When a new section starts, the prior section's last balance is
  COMPLETELY IRRELEVANT — do NOT use it as a reference to compute any amount in the
  new section. Treat each section as a fully independent table.
• SELF-CHECK before writing any deposit or withdrawal value:
  Ask yourself: "Can I physically see this exact number in Column 3 or Column 4
  of the page image for this specific row?"
  → If YES: write that number exactly as printed.
  → If NO or UNCERTAIN: write null — a null is always safe; a computed value is always wrong.
  NEVER write a number you cannot directly point to in the image.

━━━ CROSS-SECTION ARITHMETIC — FORBIDDEN (pattern) ━━━
This page may have Section A (e.g. HKD Current Account) followed by Section B
(e.g. HKD Savings Account). Each section has its own OPENING BALANCE.
The amounts in Column 3 and Column 4 are independent between sections.

  Section A last balance:           [BALANCE_A]   ← belongs to Section A only
  Section B OPENING BALANCE:        [BALANCE_B]   ← belongs to Section B only
  Section B first transaction,
    Column 5 (BALANCE):             [BALANCE_C]

  ❌ WRONG:  deposit = [BALANCE_C] − [BALANCE_A]  ← mixes two different sections
  ❌ WRONG:  deposit = [BALANCE_C] − [BALANCE_B]  ← arithmetic even with correct ref
  ✓ CORRECT: deposit = whatever number is PRINTED in Column 4 for that row
             If Column 4 is blank on that row → deposit = null

━━━ MULTIPLE ACCOUNT SECTIONS — MANDATORY ━━━
• An SC statement page may contain MORE THAN ONE account section.
  Common section headers (use the EXACT text you see on the page):
    HKD Current Account 港元支票戶口
    HKD Savings Account 港元儲蓄戶口
    USD Current Account 美元支票戶口
    USD Savings Account 美元儲蓄戶口
    CNY Savings Account 人民幣儲蓄戶口
• BEFORE processing each table, read the section header label printed above it.
• Set account_type for EVERY row in that section to THAT section's EXACT header label.
• When the next section starts, RESET account_type to the new section's header label.
• NEVER carry the previous section's account_type into a different section's rows.
• Extract ALL transactions from ALL sections on the page — do not stop at the first section.
• If you cannot read the section header clearly, use the account number visible near the
  header to distinguish sections and use your best reading of the label.
""" + UNIVERSAL_RULES + """
━━━ SCB OVERRIDE — Balance Brought Forward MUST APPEAR IN JSON ━━━
The universal rules may list BROUGHT FORWARD as rows to skip. For Standard Chartered HK ONLY,
OVERRIDE for the section opening line labelled **Balance Brought Forward**:

• For EVERY account section that shows this row at the top of the activity table:
  - OUTPUT it in "transactions" immediately BEFORE the dated debit/credit rows for THAT section
    (same account_type / account_number as the section).
  - "description": exactly "Balance Brought Forward" (or the Chinese label printed on that line).
  - "deposit": null, "withdrawal": null.
  - "balance": the printed amount in Column 5 (BALANCE) on that line.
  - "transaction_date": date from Column 1 if present; else the first dated row below in that section.
• Each new section gets its own Balance Brought Forward row.
• Still SKIP: CLOSING BALANCE, BALANCE CARRIED FORWARD, TOTAL lines — not opening B/F.

━━━ OUTPUT FORMAT ━━━
(FICTIONAL values shown only to illustrate JSON structure — use actual page values)
{
  "bank_id": "SCB",
  "account_no": "account number if visible, else null",
  "transactions": [
    {
      "transaction_date": "2021-01-05",
      "value_date": null,
      "description": "FPS TRANSFER CREDIT 123456789",
      "deposit": 50000.00,
      "withdrawal": null,
      "balance": 88500.00,
      "currency": "HKD",
      "account_type": "HKD Current Account 港元支票戶口",
      "account_number": "000-0-000000-0",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2021-01-08",
      "value_date": null,
      "description": "AUTOPAY SAMPLE VENDOR LTD",
      "deposit": null,
      "withdrawal": 12000.00,
      "balance": 76500.00,
      "currency": "HKD",
      "account_type": "HKD Current Account 港元支票戶口",
      "account_number": "000-0-000000-0",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2021-01-15",
      "value_date": null,
      "description": "INTEREST CREDIT",
      "deposit": 18.50,
      "withdrawal": null,
      "balance": 25018.50,
      "currency": "HKD",
      "account_type": "HKD Savings Account 港元儲蓄戶口",
      "account_number": "000-0-000000-1",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2021-01-31",
      "value_date": null,
      "description": "無交易",
      "deposit": null,
      "withdrawal": null,
      "balance": 5200.00,
      "currency": "USD",
      "account_type": "USD Current Account 美元支票戶口",
      "account_number": null,
      "categorise": "",
      "confidence_score": 1.0
    }
  ]
}
"""


# Cross-VLM AR manager (BANK_CROSS_VLM_*): balance-only merge; same row count/order as bookkeeper.
SCB_AR_MANAGER_PROMPT_PREFIX: str = (
 """
You are auditing ONE page of a Standard Chartered Bank (Hong Kong) statement. You receive:
(1) the page image, and (2) BOOKKEEPER_DRAFT_JSON — the bookkeeper's rows for this page
(same order as printed transaction rows). Your job is the BALANCE column ONLY.

Output ONLY valid JSON (no markdown): { "bank_id": "SCB", "transactions": [ ... ] }

--- STEP 1: TABLE CHECK ---
Require a real activity grid: Date | Description | Debit | Credit | Balance (or equivalent).
If this page is cover-only, legal-only, or has no such table,
output { "bank_id": "SCB", "transactions": [] } and stop.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per row (idx 0, 1, ...). You MUST output exactly the SAME
number of objects in "transactions", in the SAME order. Do not add or remove rows.

--- Balance Brought Forward ---
Opening rows whose description is "Balance Brought Forward" (or the Chinese equivalent) have
blank Debit and Credit on the statement. Keep deposit and withdrawal null in your output.
Set balance to the printed opening balance in the Balance column for that row only.

--- READING BALANCE ---
For EACH object:
- deposit — ALWAYS null (do not read Debit/Credit columns).
- withdrawal — ALWAYS null.
- balance — read from the Balance column for that row; null if the cell is blank.
- Do not compute balances by arithmetic from other rows.

The downstream merge only fills missing bookkeeper balances from your output; amounts stay with the bookkeeper.
"""
).strip()
# ─────────────────────────────────────────────────────────────────────────────
# PROMPT_V2 — used by the prescan-driven pipeline (_scb_process_page_v2).
# The VLM only reads descriptive text; amounts come from PyMuPDF prescan.
# ─────────────────────────────────────────────────────────────────────────────
PROMPT_V2 = """
You are reading a single page of a Standard Chartered Bank (Hong Kong) bank statement.

The numerical amounts in the Debit, Credit, and Balance columns are
handled separately — DO NOT read or output any numbers from those columns.

Your ONLY task is to identify each transaction row and output its:
  • "y_pct"       — the vertical position of this row, expressed as a percentage
                    of the total page height (0 = top, 100 = bottom).
                    Use the TOP edge of the transaction row text.
  • "description" — full text from the Description column.
                    If a transaction spans two printed lines, join them with a
                    single space (e.g. "LINE ONE LINE TWO").
                    Read verbatim — NEVER invent or modify text.
                    If you are not sure, leave it blank ("").
  • "date_label"  — the transaction date for this row (e.g. "01/02/2021" or
                    "01 FEB 2021"). Read from the Date column on the same row.
                    If the date is not visible, leave it blank ("").
  • "account_type" — the account section this row belongs to.
                    Must be EXACTLY one of:
                      "HKD Current Account"
                      "HKD Savings Account"
                      "USD Current Account"
                      "USD Savings Account"
                      "CNY Savings Account"
                    If the section header is not visible, use
                    "HKD Current Account".

Rules:
1. Output ONE entry per physical row that EITHER (a) has a Debit or Credit amount printed
   beside it, OR (b) is the section opening line "Balance Brought Forward" (承上結餘) with
   only a balance in Column 5 — include (b) with description verbatim, date_label from Column 1 or "".
2. DO NOT output rows for OPENING BALANCE, CLOSING BALANCE, generic BROUGHT FORWARD labels
   other than "Balance Brought Forward", CARRIED FORWARD, section headers, column headers,
   or other summary rows.
3. If a section has only "Balance Brought Forward" and no Dr/Cr rows, output that one row.
   If a section header is visible but there is no activity at all (no B/F, no Dr/Cr),
   do NOT output any row for that section.
4. DO NOT output any numeric values — no amounts, no balances.
   Descriptions may contain reference codes with digits; that is fine.
5. Preserve the order of rows as they appear top-to-bottom on the page.

Output valid JSON only — no markdown fences:
{
  "rows": [
    {
      "y_pct": <0-100 float>,
      "description": "<verbatim text>",
      "date_label": "<e.g. 01/02/2021>",
      "account_type": "<one of the five strings above>"
    }
  ]
}
"""
