"""
OCBC Bank (Hong Kong) — specific VLM prompt and detection keywords.
"""
from ._shared import UNIVERSAL_RULES

KEYWORDS: list[str] = [
    'OCBC', '華僑銀行', 'OCBC Bank', 'OCBC Bank (Hong Kong)',
    'INTEGRATED ACCOUNT', 'ACCOUNT ACTIVITIES', 'B/F BALANCE',
    'HKD STATEMENT SAVINGS',
]

PROMPT: str = """You are an expert at reading OCBC Hong Kong Integrated Account bank statements.

TASK: Extract every real transaction from the ACCOUNT ACTIVITIES table on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

━━━ PAGE STRUCTURE ━━━
OCBC Integrated Account statements contain sections in this order on page 1:
  • PORTFOLIO SUMMARY  — total balances by asset class (IGNORE)
  • ACCOUNT SUMMARY    — a/c numbers, balances, exchange rates  (IGNORE)
  • ACCOUNT ACTIVITIES — the ONLY section to read

IGNORE everything above the "ACCOUNT ACTIVITIES" header.
The ACCOUNT ACTIVITIES section starts with:
  DATE  PARTICULARS  WITHDRAWAL  DEPOSIT  BALANCE  (DR=DEBIT)

━━━ STATEMENT PERIOD AND DATES (CRITICAL) ━━━
• Read the statement period / statement month / "as at" wording in the page header or footer
  (often shows month and year, e.g. April 2025).
• Use that period ONLY to resolve the correct 4-digit year for each transaction row whose
  date label is printed as DDMONYY at the far right of the row (e.g. 15APR25 → 2025-04-15).
  The two-digit YY in DDMONYY MUST match the statement period (same month/year context).
• NEVER use the statement print date, envelope date, or any correspondence date as
  transaction_date unless that exact calendar date appears on the transaction row itself.
• Rows without their own DDMONYY label still share the same calendar date as the previous
  dated row in that date group (as described below).

━━━ MULTI-SUB-ACCOUNT FORMAT (CRITICAL) ━━━
The ACCOUNT ACTIVITIES table may cover multiple sub-accounts within the same page.
Each sub-account is introduced by a plain sub-header line such as:
  HKD CURRENT (000000-001)
  HKD STATEMENT SAVINGS

These sub-headers are NOT transactions and must NOT appear in the output.
Transactions continue IMMEDIATELY below each sub-header.
Set account_type to the most recent sub-header label for every transaction that follows.
Set account_number to the sub-account number in parentheses on that same header line
(e.g. "HKD CURRENT (000000-001)" → account_number="000000-001").
If no number appears in parentheses on the sub-header (e.g. "HKD STATEMENT SAVINGS"),
carry forward the account number from the top-level account number on this page, or null.

IMPORTANT: Extract transactions from ALL sub-accounts on the page.
Do NOT skip a sub-account section because the amounts look small — even transactions
with balances of 2.00 or 7,002.00 are real and must be extracted.
The HKD CURRENT sub-account typically has small balances (single digits to thousands).
The HKD STATEMENT SAVINGS sub-account typically has larger balances (tens of thousands+).

━━━ OCBC TRANSACTION ROW FORMAT ━━━
Each transaction occupies one or more lines. The PHYSICAL visual layout is:

  [DEPOSIT_AMT]  [WITHDRAWAL_AMT]  [PARTICULARS line 1]  [BALANCE]  [DDMONYY]
                                   [PARTICULARS line 2]
                                   [PARTICULARS line 3]
                                   ...

Column positions (VISUAL, left to right):
  • DEPOSIT_AMT    — left-side amount: non-zero when money comes IN (credit); 0.00 otherwise
  • WITHDRAWAL_AMT — right-side amount: non-zero when money goes OUT (debit); 0.00 otherwise
  • PARTICULARS    — description, may continue on following lines (join with a space)
  • BALANCE        — running balance AFTER this transaction (rightmost number in the row)
  • DDMONYY        — date label at the far right; appears ONLY on the FIRST transaction of
                     each date group. All subsequent transactions until the next date label
                     share the SAME date.

NOTE: The statement header labels these columns "WITHDRAWAL  DEPOSIT" but the PHYSICAL
positions on the page place DEPOSIT (credits) on the LEFT and WITHDRAWAL (debits) on the RIGHT.
Always use PHYSICAL position, not the header label, to decide deposit vs withdrawal.

━━━ ILLUSTRATIVE FORMAT EXAMPLES ━━━
(These numbers are FICTIONAL — do NOT copy them; read the actual values from the page)

Example A — HKD CURRENT sub-account (small amounts are real, extract them all):

  HKD CURRENT (000000-001)         ← sub-account header → account_type="HKD CURRENT"
  0.00   2.00   TRANSFER-CREDIT
                SAMPLEREF001        2.00   16JAN24
→ date=2024-01-16  deposit=2.00  withdrawal=null  balance=2.00
  account_type="HKD CURRENT"  description="TRANSFER-CREDIT SAMPLEREF001"

  7,000.00   0.00   TRANSFER-CREDIT
                    SAMPLEREF002    7,002.00
→ date=2024-01-16 (same date group)  deposit=7000.00  withdrawal=null  balance=7002.00
  account_type="HKD CURRENT"  description="TRANSFER-CREDIT SAMPLEREF002"

  0.00   2,200.00   CHQ NO.999999   4,802.00   17JAN24
→ date=2024-01-17  deposit=null  withdrawal=2200.00  balance=4802.00
  account_type="HKD CURRENT"  description="CHQ NO.999999"

Example B — HKD STATEMENT SAVINGS sub-account:

  HKD STATEMENT SAVINGS            ← sub-account header → account_type="HKD STATEMENT SAVINGS"
  28.50   0.00   INTEREST PAYMENT-CR   50028.50   01JAN24
→ date=2024-01-01  deposit=28.50  withdrawal=null  balance=50028.50
  description="INTEREST PAYMENT-CR"

  3,000.00   0.00   DIRECT CREDIT
                    SAMPLE COMPANY
                    FPS
                    FRN20240101PAYC010
                    9999999999              53028.50
→ date=2024-01-01 (same group — no date shown)
  deposit=3000.00  withdrawal=null  balance=53028.50
  description="DIRECT CREDIT SAMPLE COMPANY FPS FRN20240101PAYC010 9999999999"

  0.00   1,500.00   TRANSFER-DEBIT
                    FPS
                    FRN20240101PAYC020
                    8888888888              51528.50
→ date=2024-01-01 (same group)
  deposit=null  withdrawal=1500.00  balance=51528.50
  description="TRANSFER-DEBIT FPS FRN20240101PAYC020 8888888888"

━━━ OCBC-SPECIFIC RULES ━━━
• LEFT amount column = DEPOSIT (credit); RIGHT amount column = WITHDRAWAL (debit).
• account_type: use the section sub-header (e.g. "HKD STATEMENT SAVINGS") if visible; else null.
• AMOUNTS FIRST: read the LEFT and RIGHT amount columns from the printed cells. Output the
  non-zero side as deposit or withdrawal. If both are 0.00, treat BOTH as null (keep balance).
• NEVER put a withdrawal or deposit amount into the balance field. Deposit/withdrawal and
  balance are different numbers on the same row. If only one number is readable and you cannot
  tell which column it is, prefer deposit/withdrawal by physical column position and set
  balance to null — do NOT guess a running balance.
• BALANCE: read the rightmost running-balance number when it is clearly the balance column.
  If the balance cell is unclear, output null rather than inventing a figure to make arithmetic
  close. Do NOT invent a transaction row so that balances appear continuous.
• NEVER invent or "diffuse" fake transactions to make balances match. If a printed row is hard
  to read, output what you can see (possibly with null amounts or null balance) — do not add
  virtual rows or fabricated amounts.
• B/F BALANCE anchor: if the ACCOUNT ACTIVITIES section on this page starts with a
  "B/F BALANCE" line (the carry-forward opening balance from the prior page), include it as:
    { "description": "B/F BALANCE", "deposit": null, "withdrawal": null, "balance": <value> }
  Use the ACTUAL balance value printed next to "B/F BALANCE". Do NOT invent any amount.
  The system will use it as a starting balance and will exclude it from the final output.

━━━ TRANSACTION BOUNDARY — CRITICAL ━━━
Each OCBC transaction starts with a NEW pair of left/right amount columns on a fresh line.
A continuation line (for a long PARTICULARS description) has NO amounts at the left edge —
it is indented text only.

RULE: Each physical transaction row (new left/right amount pair on a fresh line) = one JSON
object. Count printed transaction rows — not invented rows. Distinct running balances usually
match the row count, but NEVER fabricate an extra row just to fill a balance gap.

NEVER COMBINE transactions:
• A withdrawal row followed by a deposit row = TWO separate transactions, never one.
• NEVER compute a net amount: 2,610 withdrawal + 2,040 deposit ≠ one 570 withdrawal.
• Two consecutive rows of the SAME type are EQUALLY forbidden from being merged:
  WRONG — merging:  { withdrawal: 105775.00, description: "TRANSFER-DEBIT TRANSFER-DEBIT..." }
  RIGHT — separate: { withdrawal:   5775.00, description: "TRANSFER-DEBIT ..." }
                    { withdrawal: 100000.00, description: "TRANSFER-DEBIT ..." }
  NEVER add the amounts of two rows together, even if both share the same transaction type
  (e.g., both TRANSFER-DEBIT, both DIRECT CREDIT). Each row is an independent transaction.
• If consecutive transactions share the same date label, they are still SEPARATE rows.
• Each transaction gets only ONE balance value — the balance printed at the END of its own
  block, before the next amount pair begins.

NEVER SKIP a transaction: every intermediate balance must appear in the output.
Example: if the balances on the page are 78469.42 → 75469.42 → 75459.42, you need three
rows (or two rows if the first has no printable amount), NOT two rows that jump from
78469.42 to 75459.42.

━━━ TRANSACTION SUMMARY — SKIP THE ENTIRE BLOCK ━━━
At the bottom of each account section you will see a TRANSACTION SUMMARY block, e.g.:
  TRANSACTION SUMMARY
  131,207.85   118,457.00   AMOUNT
  8   8   ITEM(S)
  CARRIED FORWARD   26JUL25   98,800.27   CREDIT BALANCE

SKIP all four lines completely. In particular:
• "131,207.85 / 118,457.00 AMOUNT" — period totals, NOT transactions.
• "8 8 ITEM(S)" — item count for the whole period, NOT a transaction amount.
  Do NOT output "8" as a deposit, withdrawal, or balance.
• "CARRIED FORWARD … CREDIT BALANCE" — closing footer, NOT a transaction.
• Bilingual summary lines (not transactions): 交易總金額 TOTAL TRANSACTION AMOUNT,
  交易筆數 NO.OF TRANSACTION (or "No. Of Transaction") — period total / count only;
  NEVER output them as transaction rows even if amounts appear beside the labels.
""" + UNIVERSAL_RULES + """
━━━ OCBC OVERRIDE (takes precedence over universal balance advice) ━━━
• Prefer a null balance over inventing one so arithmetic looks continuous.
• Prefer null deposit/withdrawal over deriving them from balance differences.
• Printed amount columns always beat balance-continuity guesses.

━━━ OUTPUT FORMAT ━━━
(FICTIONAL values shown only to illustrate JSON structure — use actual page values)
{
  "bank_id": "OCBC",
  "account_no": "<main account number if visible, else null>",
  "transactions": [
    {
      "transaction_date": "2024-01-16",
      "description": "TRANSFER-CREDIT SAMPLEREF001",
      "withdrawal": null,
      "deposit": 2.00,
      "balance": 2.00,
      "currency": "HKD",
      "account_type": "HKD CURRENT",
      "account_number": "000000-001",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2024-01-01",
      "description": "INTEREST PAYMENT-CR",
      "withdrawal": null,
      "deposit": 28.50,
      "balance": 50028.50,
      "currency": "HKD",
      "account_type": "HKD STATEMENT SAVINGS",
      "account_number": "000000-002",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2024-01-01",
      "description": "DIRECT CREDIT SAMPLE COMPANY FPS FRN20240101PAYC010 9999999999",
      "withdrawal": null,
      "deposit": 3000.00,
      "balance": 53028.50,
      "currency": "HKD",
      "account_type": "HKD STATEMENT SAVINGS",
      "account_number": "000000-002",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2024-01-01",
      "description": "TRANSFER-DEBIT FPS FRN20240101PAYC020 8888888888",
      "withdrawal": 1500.00,
      "deposit": null,
      "balance": 51528.50,
      "currency": "HKD",
      "account_type": "HKD STATEMENT SAVINGS",
      "account_number": "000000-002",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "2024-01-31",
      "description": "無交易",
      "withdrawal": null,
      "deposit": null,
      "balance": 4802.00,
      "currency": "HKD",
      "account_type": "HKD CURRENT",
      "account_number": "000000-001",
      "categorise": "",
      "confidence_score": 1.0
    }
  ]
}
"""

# Cross-VLM AR manager (BANK_CROSS_VLM_*): balance-only merge; same row count/order as bookkeeper draft.
OCBC_AR_MANAGER_PROMPT_PREFIX: str = """
You are auditing ONE page of an OCBC Bank (Hong Kong) Integrated Account statement. You receive:
(1) the page image, and (2) BOOKKEEPER_DRAFT_JSON — the bookkeeper's rows for this page
(same order as printed transaction rows in ACCOUNT ACTIVITIES). Your job is the BALANCE column ONLY.

Output ONLY valid JSON (no markdown): { "bank_id": "OCBC", "transactions": [ ... ] }

--- STEP 1: ACTIVITY TABLE CHECK ---
Require a real ACCOUNT ACTIVITIES grid: DEPOSIT and WITHDRAWAL amount columns (left/right pair)
and a running BALANCE for each transaction row.
Lines such as 交易總金額 TOTAL TRANSACTION AMOUNT or 交易筆數 NO.OF TRANSACTION are period
summaries, not activity rows — BOOKKEEPER_DRAFT_JSON should list only real activity lines.
If this page is cover/portfolio summary only (no ACCOUNT ACTIVITIES), legal notices only,
or has no such table, output { "bank_id": "OCBC", "transactions": [] } and stop.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per row (idx 0, 1, ...). You MUST output exactly the SAME
number of objects in "transactions", in the SAME order. Do not add or remove rows.

Multiple sub-account sections (e.g. HKD CURRENT, HKD STATEMENT SAVINGS) may appear —
use account_type only for alignment context.

--- B/F BALANCE / opening rows ---
Rows labelled B/F BALANCE may have blank deposit and withdrawal on the statement.
Keep deposit and withdrawal null. Set balance to the printed running balance for that row only.

--- READING BALANCE ---
For EACH object:
- deposit — ALWAYS null.
- withdrawal — ALWAYS null.
- balance — read the running balance printed for that transaction row (rightmost balance
  in the row block); null if the cell is blank.
- Do not compute balances by arithmetic.

The downstream merge only fills missing or corrected bookkeeper balances from your output;
deposit and withdrawal amounts stay with the bookkeeper.
""".strip()
