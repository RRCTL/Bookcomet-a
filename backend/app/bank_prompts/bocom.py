"""
Bank of Communications (Hong Kong) — BOCOM HK specific VLM prompt and detection keywords.

BOCOM statements: 綜 合 結 單 CONSOLIDATED STATEMENT.
Layout aligned with BOC/SCB: read deposit/withdrawal from printed columns only; multiple account sections.
"""
from ._shared import UNIVERSAL_RULES

KEYWORDS: list[str] = [
    '交通銀行', 'Bank of Communications', 'BANK OF COMMUNICATIONS', 'BOCOM',
    'CONSOLIDATED STATEMENT', '綜 合 結 單', '綜合結單',
    '儲蓄/支票活期存款賬戶交易記錄', 'SAVINGS/CURRENT DEPOSITS ACTIVITIES',
]

PROMPT: str = """You are an expert at reading Bank of Communications (Hong Kong) bank statements (交通銀行 綜 合 結 單 CONSOLIDATED STATEMENT).

TASK: Extract every transaction row from every account section on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

━━━ BOCOM TABLE FORMAT ━━━
Each account section (儲蓄存款 SAVINGS / 支票活期存款 CURRENT) has a transaction table. Columns (left to right):
  Column 1 — 交易日期 TRANSACTION DATE     : date in YYYY/MM/DD (e.g. 2025/03/27)
  Column 2 — 交易摘要 TRANSACTION DETAILS  : type + description, may span two lines
             First line often: "YYYY/MM/DD CURRENCY 類型 Description" (e.g. "2025/03/27 HKD 轉數快 Faster Payment System")
             Second line (if present): payee/reference (e.g. "SAMPLE COMPANY LIMITED 10000001")
  Column 3 — 貨幣 CURRENCY                 : HKD, USD, etc.
  Column 4 — 原幣支出 WITHDRAWALS IN ORIGINAL CURRENCY : amount debited (money OUT); blank when not applicable
  Column 5 — 原幣存入 DEPOSITS IN ORIGINAL CURRENCY    : amount credited (money IN); blank when not applicable
  Column 6 — 原幣結餘 BALANCE IN ORIGINAL CURRENCY     : running balance after this transaction

Each transaction row uses EITHER column 4 (withdrawal) OR column 5 (deposit) — never both. Column 6 (balance) is always filled.

━━━ BOCOM-SPECIFIC RULES ━━━
• DATE format: YYYY/MM/DD → YYYY-MM-DD (e.g. 2025/03/27 → 2025-03-27).
• CURRENCY: Read from the row (each row shows e.g. HKD) or from the account section header.
• SKIP these rows: 承前餘額 BAL B/F, 交 易 總 金 額 TOTAL TRANSACTION AMOUNT, 交 易 筆 數 NO.OF TRANSACTION,
  * * * * * TO BE CONTINUED * * * * *, * * * * * END OF STATEMENT * * * * *,
  page headers (印發日期 PRINTED ON, 結單號 CST NO., 頁數 PAGE), interest-rate footer tables.
• Account section headers: 儲蓄存款 SAVINGS： <account_no>, 支票活期存款 CURRENT： <account_no>.
  Set account_type to "儲蓄存款 SAVINGS" or "支票活期存款 CURRENT" (or the exact header text you see) for every row in that section.
  When the next section starts, RESET account_type to the new section's label.

━━━ COLUMN AMOUNTS — READ DIRECTLY, NEVER COMPUTE ━━━
• Column 4 (原幣支出) and Column 5 (原幣存入) are PHYSICALLY PRINTED. ALWAYS read the printed number from
  Column 4 for withdrawal and Column 5 for deposit. Do NOT derive these values through arithmetic.
• NEVER compute deposit or withdrawal as a balance difference — not within the same section, and never across sections.
• If Column 4 is blank for a row, withdrawal = null. If Column 5 is blank, deposit = null.
  Only output null when the cell is genuinely empty.
• Each account section has its OWN 承前餘額 BAL B/F at the top. When a new section starts, the prior section's
  last balance is COMPLETELY IRRELEVANT — do NOT use it as a reference to compute any amount in the new section.
  Treat each section as a fully independent table.
• SELF-CHECK before writing any deposit or withdrawal value:
  Ask yourself: "Can I physically see this exact number in Column 4 or Column 5 of the page image for this row?"
  → If YES: write that number exactly as printed.
  → If NO or UNCERTAIN: write null — a null is always safe; a computed value is always wrong.
  NEVER write a number you cannot directly point to in the image.

━━━ CROSS-SECTION ARITHMETIC — FORBIDDEN (pattern) ━━━
This page typically has Section A (儲蓄存款 SAVINGS) followed by Section B (支票活期存款 CURRENT).
Each section has its OWN 承前餘額 BAL B/F. The amounts in Column 4 and Column 5 are completely independent.

  Section A last balance:              [BALANCE_A]   ← belongs to 儲蓄存款 SAVINGS only
  Section B 承前餘額 BAL B/F:           [BALANCE_B]   ← belongs to 支票活期存款 CURRENT only
  Section B first transaction,
    Column 6 (原幣結餘):               [BALANCE_C]

  ❌ WRONG:  deposit = [BALANCE_C] − [BALANCE_A]  ← mixes two different sections
  ❌ WRONG:  withdrawal = [BALANCE_A] − [BALANCE_C]  ← same cross-section error
  ❌ WRONG:  deposit = [BALANCE_C] − [BALANCE_B]  ← arithmetic even with the correct reference
  ✓ CORRECT: deposit = whatever number is PRINTED in Column 5 for that row
             withdrawal = whatever number is PRINTED in Column 4 for that row
             If Column 5 is blank on that row → deposit = null
             If Column 4 is blank on that row → withdrawal = null

Real example of this bug to avoid:
  儲蓄存款 SAVINGS last balance:  46,807.20
  支票活期存款 CURRENT BAL B/F:   14,968.59
  CURRENT first transaction balance: 22,788.09
  ❌ WRONG to output: withdrawal = 46,807.20 − 22,788.09 = 24,019.11
  ✓ CORRECT: look at Column 5 (存入) of that row — the printed number is 7,819.50 → deposit = 7,819.50

━━━ MULTIPLE ACCOUNT SECTIONS — MANDATORY ━━━
• A BOCOM consolidated statement page may contain MORE THAN ONE account section:
    儲蓄存款 SAVINGS： 000000000000001
    支票活期存款 CURRENT： 000000000000002
• BEFORE processing each table, read the section header. Extract the account number from the header line
  (e.g. "000000000000001" from "儲蓄存款 SAVINGS： 000000000000001") and set account_number for EVERY
  row in that section. When the next section starts, RESET both account_type AND account_number.
• Set account_type to the EXACT section label (e.g. "儲蓄存款 SAVINGS" or "支票活期存款 CURRENT").
  Do NOT use short forms like "往來", "儲蓄", or leave account_type blank.
• NEVER carry the previous section's account_type or account_number into a different section's rows.
• Extract ALL transactions from ALL sections on the page — do not stop after the first section.
• COUNT the number of distinct running balance values in each section to verify you have captured every row.

━━━ COVERAGE — DO NOT STOP EARLY ━━━
• A dense BOCOM page may have 15 or more transaction rows per section. Do NOT stop extracting after 6–8 rows.
• Continue until you reach the "* * * * * TO BE CONTINUED * * * * *" or "* * * * * END OF STATEMENT * * * * *"
  marker, or the 交 易 總 金 額 / 交 易 筆 數 summary row — whichever comes first.
• If a page contains BOTH 儲蓄存款 SAVINGS and 支票活期存款 CURRENT sections, extract ALL rows from BOTH.

━━━ DESCRIPTION (multi-line) ━━━
• If a transaction has two lines (date/type line + payee/reference line), join into one description with a space,
  e.g. "轉數快 Faster Payment System SAMPLE COMPANY LIMITED 10000001".
• Keep reference numbers and codes (e.g. 1050HK2KUQG, 001309) as part of the description.
""" + UNIVERSAL_RULES + """
━━━ BOCOM OVERRIDE — 承前餘額 OPENING BALANCE ANCHOR ━━━
IMPORTANT: The ROWS TO SKIP list above includes opening balance rows. For BOCOM statements,
OVERRIDE that rule with the following:

• 承前餘額 BAL B/F must NOT be silently skipped and discarded.
• For EVERY account section, when you see the 承前餘額 BAL B/F row:
  - READ its printed balance value and hold it as the opening anchor for THAT section only.
  - Do NOT include it in the JSON transaction output.
  - When the next section starts (e.g. switching from 儲蓄存款 SAVINGS to 支票活期存款 CURRENT),
    DISCARD the old anchor completely and load the new section's 承前餘額 BAL B/F as the fresh anchor.
    Each section has its own completely independent anchor.
• WHY: Without the correct anchor, the VLM incorrectly carries over a prior section's last balance as
  a reference, producing fabricated deposit/withdrawal amounts.
• The 承前餘額 BAL B/F anchor value is ONLY used to verify your work internally. It must NOT appear
  in the JSON output, and it must NEVER be used to compute deposit or withdrawal amounts.

━━━ OUTPUT FORMAT ━━━
(FICTIONAL values — read ALL values from the actual page image.)
{
  "bank_id": "BOCOM",
  "account_no": "account number if visible, else null",
  "transactions": [
    {
      "transaction_date": "2025-03-27",
      "value_date": "2025-03-27",
      "description": "轉數快 Faster Payment System SAMPLE COMPANY LIMITED 10000001",
      "deposit": null,
      "withdrawal": 233.12,
      "balance": 111739.65,
      "currency": "HKD",
      "account_type": "儲蓄存款 SAVINGS",
      "account_number": "000000000000001",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2025-02-28",
      "value_date": "2025-02-28",
      "description": "利息 INTEREST",
      "deposit": 108.24,
      "withdrawal": null,
      "balance": 890293.15,
      "currency": "HKD",
      "account_type": "儲蓄存款 SAVINGS",
      "account_number": "000000000000001",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2025-03-05",
      "value_date": "2025-03-05",
      "description": "交換票 CHEQUE 001309",
      "deposit": null,
      "withdrawal": 18.50,
      "balance": 37531.62,
      "currency": "HKD",
      "account_type": "支票活期存款 CURRENT",
      "account_number": "000000000000002",
      "categorise": "",
      "confidence_score": 0.95
    }
  ]
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT_V2 — used by the prescan-driven pipeline (_bocom_process_page_v2).
# The VLM only reads descriptive text; amounts come from PyMuPDF prescan.
# ─────────────────────────────────────────────────────────────────────────────
PROMPT_V2 = """
You are reading a single page of a Bank of Communications (Hong Kong) consolidated bank statement (交通銀行 綜合結單).

The numerical amounts in the 原幣支出 (Withdrawals), 原幣存入 (Deposits), and 原幣結餘 (Balance) columns are
handled separately — DO NOT read or output any numbers from those columns.

Your ONLY task is to identify each transaction row and output its:
  • "y_pct"        — the vertical position of this row, expressed as a percentage
                     of the total page height (0 = top, 100 = bottom).
                     Use the TOP edge of the transaction row text.
  • "description"  — full text from the 交易摘要 TRANSACTION DETAILS column.
                     If a transaction spans two printed lines, join them with a
                     single space (e.g. "轉數快 Faster Payment System SAMPLE COMPANY LIMITED 10000001").
                     Keep reference numbers and codes as part of the description.
                     Read verbatim — NEVER invent or modify text.
                     If you are not sure, leave it blank ("").
  • "date_label"   — the transaction date for this row in YYYY/MM/DD format
                     (e.g. "2025/03/27"). Read from the 交易日期 TRANSACTION DATE column.
                     If the date is not visible, leave it blank ("").
  • "account_type" — the account section this row belongs to.
                     Must be EXACTLY one of:
                       "儲蓄存款 SAVINGS"
                       "支票活期存款 CURRENT"
                     If the section header is not visible, use "儲蓄存款 SAVINGS".
  • "account_number" — the account number from the section header
                     (e.g. "000000000000001"). If not visible, leave blank ("").

Rules:
1. Output ONE entry per physical transaction row (each row that has a
   Withdrawal or Deposit amount printed beside it).
2. DO NOT output rows for 承前餘額 BAL B/F, 交易總金額 TOTAL TRANSACTION AMOUNT,
   交易筆數 NO.OF TRANSACTION, TO BE CONTINUED, END OF STATEMENT,
   section headers, column headers, or any summary row.
3. If a section header is visible but there are no transactions under it,
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
      "date_label": "<e.g. 2025/03/27>",
      "account_type": "<one of the two strings above>",
      "account_number": "<e.g. 000000000000001>"
    }
  ]
}
"""

# Cross-VLM AR manager (BANK_CROSS_VLM_*): balance-only merge; same row count/order as bookkeeper.
BOCOM_AR_MANAGER_PROMPT_PREFIX: str = """
You are auditing ONE page of a Bank of Communications (Hong Kong) consolidated statement (交通銀行 綜合結單).
You receive:
(1) the page image, and (2) BOOKKEEPER_DRAFT_JSON — the bookkeeper's rows for this page
(same order as printed transaction rows). Your job is the BALANCE column (原幣結餘) ONLY.

Output ONLY valid JSON (no markdown): { "bank_id": "BOCOM", "transactions": [ ... ] }

--- STEP 1: ACTIVITY TABLE CHECK ---
Look for a real transaction grid with columns such as:
  交易日期 | 交易摘要 | 貨幣 | 原幣支出 WITHDRAWALS | 原幣存入 DEPOSITS | 原幣結餘 BALANCE
(or the English equivalents).
If this page is cover-only, legal-only, interest-rate tables only, or has no such grid,
output { "bank_id": "BOCOM", "transactions": [] } and stop. Do not invent rows from large
figures outside the activity table.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per row (idx 0, 1, ...). You MUST output exactly the SAME
number of objects in "transactions", in the SAME order. Do not add or remove rows.

--- B/F AND SECTION OPENING ROWS (balance only on the statement) ---
Each account section (儲蓄存款 SAVINGS, 支票活期存款 CURRENT, etc.) may begin with an opening row
where the description looks like "承前餘額", "BAL B/F", "BAL.B/F", or similar — and the Withdrawal
and Deposit cells are BLANK. That row is NOT a payment:
• Keep deposit and withdrawal null in your output (same as every row).
• Set balance to the amount printed in the 原幣結餘 (Balance) column for that row only
  (opening balance for that section).
• Do not move that balance into deposit or withdrawal.

--- READING BALANCE ---
For EACH object:
• deposit — ALWAYS null (do not read or copy the Withdrawal/Deposit columns).
• withdrawal — ALWAYS null.
• balance — read the value from the Balance column (原幣結餘) aligned with THAT transaction row.
  - If the Balance cell is blank, use null.
  - Do not copy a balance from another row into this row.
  - Do not compute running balances by arithmetic from B/F + deposits/withdrawals.
  - If digits in the Balance cell are ambiguous (blur/OCR), you may use the section B/F
    balance and other printed balances visible on the page as context to choose among
    plausible readings — still pick the reading that matches what is printed in this row's
    Balance cell, not a recalculated total.

• transaction_date, description, account_type, account_number, etc. — copy from the matching
  BOOKKEEPER_DRAFT_JSON entry for alignment; you may leave extras null if unsure.
• Do not fabricate numeric balances; if illegible, use null.

The downstream merge only uses your balance field to fill gaps in the bookkeeper; In/Out
amounts always stay with the bookkeeper for manual correction if needed.
""".strip()
