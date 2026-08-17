"""
HSBC Business Direct (Hong Kong) — HSBC HK specific VLM prompt and detection keywords.

Developed from HSBC Business Direct (Hong Kong) text-based PDF statements
(PyMuPDF extracts full text). Example account numbers in this file are placeholders.

Key format characteristics vs BOC/SCB:
  • Running balance is printed ONLY on the LAST transaction of each date group (not per-row).
  • Date format: "DD Mon" partial date (e.g. "6 Dec", "8 Dec") — year inferred from header.
  • Three account sections: HKD Current / HKD Savings / Foreign Currency Savings.
  • FX Savings table has an extra CCY column before the Date column.
  • CHARGES (HKD 5.00 bank fee) is always a separate withdrawal row, distinct from the payee.
  • Multi-line descriptions: payee on line 1, reference code + date code on line 2.
"""
from ._shared import UNIVERSAL_RULES

KEYWORDS: list[str] = [
    '滙豐', 'HSBC', 'Hongkong and Shanghai', 'Hong Kong and Shanghai',
    'HSBC Business Direct', '汇丰', 'HSBC Business',
]

PROMPT: str = """You are an expert at reading HSBC Business Direct bank statements (Hong Kong).

TASK: Extract every transaction row from every account section on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

━━━ FIRST: CHECK WHETHER A TRANSACTION TABLE EXISTS ON THIS PAGE ━━━
Before extracting anything, scan this page for the column header row:

  HKD sections:   "Date   Transaction Details   Deposit   Withdrawal   Balance"
  FX section:     "CCY   Date   Transaction Details   Deposit   Withdrawal   Balance"

• If that column header row IS present → a transaction table exists on this page.
  Proceed to extract transactions from the rows below it.
• If that column header row is ABSENT → this page has no transaction table.
  It is a cover page, Portfolio Summary page, or legal/exchange-rate page.
  Output  { "bank_id": "HSBC", "account_no": null, "transactions": [] }  and stop.

This check is content-based, NOT page-number-based.  A scanned statement may begin
directly with a transaction table on page 1 — if the column headers are present, extract
normally.  Conversely, any interior page that lacks column headers must also return [].

Key: the presence of a large balance figure, an account name, or descriptive text
such as "CREDIT INTEREST" or "HKD Savings" in the Portfolio Summary section does NOT
indicate a transaction table.  Only the five-column (or six-column for FX) header row
is the required signal.

━━━ HSBC TABLE FORMAT ━━━
Each account section has a transaction table with columns (left to right):

  HKD Current / HKD Savings sections:
    Column 1 — Date              : date label (e.g. "6 Dec", "8 Dec") — appears once per day group
    Column 2 — Transaction Details: description text; may span 2–3 printed lines
    Column 3 — Deposit           : amount credited (money IN); blank when not applicable
    Column 4 — Withdrawal        : amount debited (money OUT); blank when not applicable
    Column 5 — Balance           : running balance — printed ONLY on the LAST transaction of each
                                   date group; blank for intermediate transactions within a day

  Foreign Currency Savings section (extra CCY column before Date):
    Column 0 — CCY               : currency code (e.g. "AUD")
    Column 1 — Date
    Column 2 — Transaction Details
    Column 3 — Deposit (in that CCY)
    Column 4 — Withdrawal (in that CCY)
    Column 5 — Balance (in that CCY)

━━━ HSBC-SPECIFIC RULES ━━━

DATE FORMAT — partial dates, year inferred from statement header:
• Dates are printed as "DD Mon" (e.g. "6 Dec", "8 Dec", "2 Jan"). No year is printed per row.
• You MUST read the statement date printed on THIS SAME PAGE in the header area (top of the
  page — often top-left or top-right), e.g. "21 July 2022" or "6 January 2026". Extract the
  header year (Y) and header month (M) ONLY from that printed text.
• FORBIDDEN: Do NOT use the real-world calendar year, "today", the current date, system time,
  model training cutoff year, or any assumed default year. The document's printed header year
  is the only authority for Y.
• Look at the statement header date printed on the page (e.g. "6 January 2026",
  "6 February 2026", "6 December 2025"). Extract the header year (Y) and header month (M).
• YEAR INFERENCE RULE:
    - For each transaction date, extract its month number (m).
    - If m > M  →  transaction is in the PRIOR year: year = Y − 1
    - If m ≤ M  →  transaction is in the CURRENT year: year = Y
  Examples (header = "6 January 2026", M=1, Y=2026):
    "8 Dec"  → m=12 > M=1  → year=2025 → 2025-12-08
    "2 Jan"  → m=1  ≤ M=1  → year=2026 → 2026-01-02
    "6 Jan"  → m=1  ≤ M=1  → year=2026 → 2026-01-06
  Examples (header = "6 December 2025", M=12, Y=2025):
    "7 Nov"  → m=11 < M=12 → year=2025 → 2025-11-07
    "1 Dec"  → m=12 = M=12 → year=2025 → 2025-12-01
  Examples (header = "21 July 2022", M=7, Y=2022):
    "12 Jul" → m=7 ≤ M=7 → year=2022 → 2022-07-12
    "11 Jul" → m=7 ≤ M=7 → year=2022 → 2022-07-11
• Convert abbreviated months: Jan=01, Feb=02, Mar=03, Apr=04, May=05, Jun=06,
  Jul=07, Aug=08, Sep=09, Oct=10, Nov=11, Dec=12.

DATE GROUPS — one date label shared by multiple transactions:
• The date label (e.g. "8 Dec") appears only on the FIRST transaction row of that day group.
• ALL subsequent rows until the NEXT date label share that SAME date — copy it to every row.
• Do NOT extract a date from reference codes embedded in descriptions (e.g. "DDMON" inside
  "HC125Cxxxxxxxxxx DDMON" is a reference code suffix — it is NOT a separate date label).
• PAGE BREAK DATE CONTEXT: When a page starts mid-statement (transactions continuing from
  a prior page), re-read the first date label printed on THIS page carefully and independently.
  Do NOT assume the date at the top of this page is the same as the date that ended the
  previous page. The date label printed here is the authoritative date for this page's rows.

BALANCE — HSBC DAY-END ONLY (important override):
• HSBC prints the running balance ONLY on the LAST transaction of each date group.
  Intermediate transactions within the same day show NO balance.
• For intermediate transactions within a day: set balance = null.
  This is an HSBC-specific override of the universal BALANCE MANDATORY rule.
• For the LAST transaction of each date group: set balance = the printed balance value.
• For the B/F BALANCE row: balance = the printed opening balance; deposit = null; withdrawal = null.
• NEVER compute balance arithmetically. Only output a balance when it is physically printed.

AMOUNT OF THE LAST TRANSACTION — still read from its printed column, never computed:
• The fact that balance is day-end-only does NOT mean the last transaction's deposit or
  withdrawal amount should be computed. EVERY transaction's amount — including the LAST one
  in a day group — is physically printed in its own Deposit or Withdrawal column cell.
  ❌ WRONG:  last_deposit    = day_end_balance − previous_day_end_balance
  ❌ WRONG:  last_withdrawal = previous_day_end_balance − day_end_balance
  ❌ WRONG:  last_deposit    = day_end_balance − previous_day_end_balance + sum_of_day_withdrawals
  ✓ CORRECT: read the printed number in Column 3 (deposit) or Column 4 (withdrawal) for that row.
• "Day-end-only balance" applies ONLY to the Balance column — deposit and withdrawal amounts
  are always explicitly printed in their own columns for EVERY transaction, including the last.

Illustrative example of this bug to avoid:
  Day N day-end balance:    10,000.00
  Day N+1 transactions:     SAMPLE PAYMENTS EURO → 586.00 deposit (intermediate, balance=null)
                            [PAYEE NAME]         → 500.00 deposit (last of day, balance=11,086.00)
  ❌ WRONG to output for [PAYEE NAME]: deposit = 11,086.00 − 10,000.00 = 1,086.00
  ✓ CORRECT: read Column 3 for [PAYEE NAME] → the printed number is 500.00 → deposit = 500.00

SECTION HEADERS — three possible account sections on an HSBC statement:
  "HSBC Business Direct HKD Current"
  "HSBC Business Direct HKD Savings"
  "HSBC Business Direct Foreign Currency Savings"
• Set account_type to the EXACT section header text printed above each table.
• Reset account_type when a new section starts on the page.
• Extract ALL transactions from ALL sections visible on the page.

━━━ EXTRACTION PRIORITY ORDER — amounts first, never balance-derived ━━━
Your PRIMARY task is to read every printed Deposit and Withdrawal amount, then match
each to its description. Follow these steps IN ORDER for every page:

  STEP 1 — SCAN AMOUNTS:  Look at ONLY Column 3 (Deposit) and Column 4 (Withdrawal).
           For every horizontal row separator drawn on the image, record which column
           has a printed number and what that number is. Ignore the Balance column for now.
  STEP 2 — MATCH DESCRIPTIONS:  For each amount found in Step 1, read the description
           text to its LEFT in Column 2 (Transaction Details). Combine multi-line text
           belonging to the same row into one description string.
  STEP 3 — FILL DATE + BALANCE:  Only AFTER Steps 1 and 2, fill in the date (Column 1)
           and balance (Column 5, only if physically printed on that row).
  STEP 4 — VALIDATE: The pre-scan has already counted the EXACT number of Deposit and
           Withdrawal amounts on this page. Your Step 1 count MUST match. If it doesn't,
           re-examine the page — you likely missed an amount or merged two rows.

• NEVER work backwards from balance to compute an amount. If you find yourself
  thinking "the balance is X, so the amount must be Y", STOP. The amount is whatever
  is physically printed in Column 3 or Column 4 — nothing else.
• Balances are OUTPUT-ONLY metadata. They do not determine, validate, or constrain
  the deposit or withdrawal values. A "wrong-looking" balance does NOT mean the
  amount you read is wrong — it means the balance column is day-end-only.

COLUMN AMOUNTS — READ DIRECTLY, NEVER COMPUTE:
• The Deposit and Withdrawal columns are PHYSICALLY SEPARATE columns on the page.
• Read the printed number from whichever column it physically appears in:
    - Deposit column (Column 3): money coming INTO the account
    - Withdrawal column (Column 4): money going OUT of the account
• If Deposit is blank for a row: deposit = null.
• If Withdrawal is blank for a row: withdrawal = null.
• NEVER copy the Balance into the deposit or withdrawal field.
• NEVER compute deposit or withdrawal through arithmetic.

MULTI-LINE DESCRIPTIONS — join into one string:
• Each transaction occupies 1–3 printed lines:
    Line 1: payee name or keyword (e.g. "SAMPLE PAYMENTS EURO", "FROM PAYME(HSBC)xxx",
             "CR TO xxx-xxxxxx-xxx", "CHARGES", "CREDIT INTEREST", "SAMPLE EXPRESS (HONG K")
    Line 2: reference code + date code (e.g. "HC125Cxxxxxxxxxx DDMON",
             "Txxxxxx(DDMONYY)", "NCxxxxxxxxxx(DDMONYY)", "xxxxxxxxxx DDMON")
    Line 3 (optional): supplementary detail (e.g. "[SENDER NAME]", "REFCODE (DDMONYY)",
             "5592-xxxx-xxxx-xxxx", "NARRATIVE INV(DDMONYY)")
• Join all lines of the SAME transaction into one description string with spaces.
• Reference codes are PART OF the description — include them verbatim.
• The date code embedded in a reference (e.g. "DDMON" in "HC125Cxxxxxxxxxx DDMON") is
  NOT a transaction date — it is a reference suffix; do not treat it as the column 1 date.

TWO-LINE TRANSACTION AGGREGATION — reference code first, then sender name:
• Some inbound transactions print in REVERSED order:
    Line 1: reference code + date code  (e.g. "HC125Bxxxxxxxxxx DDMON")
    Line 2: sender name                 (e.g. "MR [NAME]", "MRS [NAME]", "MISS [NAME]")
  These two lines are ONE single transaction — you MUST combine them into one JSON object.
• ✓ CORRECT (one transaction object):
    description = "HC125Bxxxxxxxxxx DDMON MR [NAME]", deposit = <printed amount>
• ❌ WRONG — DO NOT split into two separate objects:
    { "description": "HC125Bxxxxxxxxxx DDMON", "deposit": <amount> }   ← splits first line
    { "description": "MR [NAME]", "deposit": null }                    ← fabricated second row
• Rule: if a line contains ONLY a reference code (alphanumeric, e.g. starting with
  HC125…, NCxxx…, Txxx…, or similar) AND the very next printed line is a sender name
  (MR / MRS / MISS …), treat both lines as ONE transaction.
• The amount in the Deposit or Withdrawal column belongs to this combined row —
  do NOT assign it to the reference-code line alone and leave the name line with null.
• The same principle applies to any two-line merchant description where the second line
  is a continuation or qualifier of the first — always combine into one transaction object.

SENDER NAMES — READ VERBATIM, NEVER INVENT:
• Some inbound FPS/PayMe transfers print the sender's registered name on Line 1 or Line 3.
  These names MUST be transcribed verbatim from the page — do NOT guess or invent any name.
• If no sender name is printed and you can only read a reference code
  (e.g. "HC125Bxxxxxxxxxx DDMON"), output ONLY that reference code as the description.
• Many inbound descriptions contain NO sender name at all — examples of valid as-is formats:
    "FROM PAYME(HSBC)xxx Txxxxxx(DDMONYY)"      ← PayMe reference only, no name
    "SAMPLE PAYMENTS EURO HC125Bxxxxxxxxxx DDMON" ← processor reference only, no name
  Do NOT prefix any of these with an invented "MR / MRS / MISS [name]".
• Only output "MR …", "MRS …", or "MISS …" if those words are physically visible on the page.

HSBC STATEMENTS ARE IN ENGLISH — NO CHINESE SUFFIXES:
• HSBC Business Direct statements are printed entirely in English.
• NEVER append Chinese text to any description, even if it seems to describe the transaction type.
  FORBIDDEN suffixes/labels — these belong to other HK banks, not HSBC:
    ❌ 轉賬收入    ❌ 自動轉賬存入    ❌ 轉賬支出    ❌ 自動轉賬支出
• Before outputting any description string, check: if it contains Chinese characters, remove
  them entirely. The only valid Chinese text in HSBC output is "無交易" (empty account sections).

CHARGES AMOUNT — ALWAYS EXACTLY HKD 5.00:
• "CHARGES" is a fixed bank service fee. Its withdrawal amount is ALWAYS 5.00 — never any
  other value.
• The CHARGES row frequently shares the same reference code (HCxxxxxxxx DDMON) as the
  immediately following payee row. Their amounts are COMPLETELY INDEPENDENT — read each
  row separately from its own Column 4 cell:
    CHARGES  HC125Cxxxxxxxxxx DDMON  → Column 4 = 5.00        ← fee row
    [PAYEE NAME]  HC125Cxxxxxxxxxx DDMON  → Column 4 = [amount]  ← payee row
• If the amount you read for a CHARGES row is not 5.00, you have misread the page.
  Re-read Column 4 for that specific CHARGES row. If still unclear, output withdrawal = 5.00
  with a lower confidence_score. Do NOT copy the payee's amount into the CHARGES row.

BALANCES ARE ALWAYS POSITIVE — SIGN CONVENTION:
• HSBC Business Direct HKD balances are always printed as positive numbers.
  An account in overdraft is shown as "[amount] DR" — the number itself is still positive.
• NEVER output a negative number for the balance field.
• If you read a negative balance, you have made an error — re-read the Balance column for
  that row. The printed value will be a positive number (possibly with "DR" suffix).
• This also means: if a prior row's balance was negative in your working notes, it is wrong
  and must NOT be used as a reference for computing any subsequent deposit or withdrawal.

TRANSACTION ROW BOUNDARY — hard rule, no description bleeding:
• Each physical transaction row in the table has AT MOST 3 printed lines in its
  Transaction Details cell. When you see a new payee keyword begin on its own line
  (any of: "SAMPLE PAYMENTS EURO", "SAMPLE WALLET HK LTD", "FROM PAYME", "SAMPLECODEA", "SAMPLE AGG",
  "SAMPLE FOOD", "SAMPLELINK", "SAMPLECAFE", "MRS ", "MR ", "MISS ", "SAMPLE EXPRESS",
  "SAMPLE HK MEDIA", "SAMPLEPLATFORM", "CREDIT INTEREST", "CHARGES", "CR TO", or any
  other recognisable payee / keyword that starts a new row), that line is the BEGINNING
  of a NEW, SEPARATE transaction — the previous transaction's description has ended.
• NEVER carry text from one transaction's description into the next transaction.
• Count the distinct payee-start lines visible in the Transaction Details column of
  this page — that is the minimum number of transaction objects to output.
• If you see 10 payee names on the page, you must output at least 10 separate JSON
  objects, each with its own description, deposit/withdrawal, and date.

Anti-pattern to avoid (description bleeding):
  ❌ WRONG: one object whose description reads
      "SAMPLE WALLET HK LTD HC125B... 11NOV  SAMPLE FOOD INTERNAT HC125B... 11NOV  CHARGES HC125B..."
      with a single amount — this collapses many rows into one.
  ✓ CORRECT: separate objects for SAMPLE WALLET HK LTD, SAMPLE FOOD INTERNAT, CHARGES, etc.,
      each with its own printed amount from Column 3 or Column 4.

CR TO TRANSFERS — outgoing FPS/bank transfers (ALWAYS Withdrawal, NEVER Deposit):
• Any description starting with "CR TO", "CR TD", or "CR T" followed by an account
  number is an outgoing inter-bank transfer → withdrawal = <amount>, deposit = null.
  e.g. "CR TO xxx-xxxxxx-xxx NARRATIVE (DDMONYY)" → withdrawal = <printed amount>
• This rule is ABSOLUTE. Even if the amount appears in the Deposit column by mistake,
  a "CR TO / CR TD" description is ALWAYS a withdrawal. Check the column again.
• Do NOT classify any "CR TO / CR TD" transaction as a deposit under any circumstances.

CREDIT CARD PAYMENT — always Withdrawal:
• HSBC credit card repayments appear in 2–3 line formats — ALL are WITHDRAWALS:
  Format A (card number line + NC reference):
      5592-xxxx-xxxx-xxxx               ← credit card number (part of description)
      NCxxxxxxxxxx(DDMONYY)  [amount]   ← reference + amount → WITHDRAWAL
  Format B (card number line + CREDIT CARD label):
      5592-xxxx-xxxx-xxxx               ← credit card number (part of description)
      CREDIT CARD (DDMONYY)  [amount]   ← label + amount → WITHDRAWAL
  Format C (CREDIT CARD PAYMENT label + card/reference):
      CREDIT CARD PAYMENT               ← first line
      5592xxxxxxxxxxxx  [amount]        ← card number + amount → WITHDRAWAL
• The "5592-xxxx-xxxx-xxxx" or "5592xxxxxxxxxxxx" string is ALWAYS a credit card number
  embedded in the description — NEVER a deposit indicator, account number, or date code.
• ANY amount that appears on a line with "NC…" reference or "CREDIT CARD" label, or
  immediately below a "5592-…" line = WITHDRAWAL (credit card repayment), never a deposit.
• NEVER classify a credit card repayment as a deposit.

CHARGES — bank service fee (always Withdrawal):
• "CHARGES" is a HKD 5.00 bank fee. It is ALWAYS a separate, independent withdrawal row.
• It often shares the SAME reference code (HCxxxxxxxx DDMON) as the immediately following
  payee row.
  e.g.  CHARGES  HC125Cxxxxxxxxxx DDMON  5.00      ← separate withdrawal row (fee)
        [PAYEE]  HC125Cxxxxxxxxxx DDMON  [amount]  ← separate withdrawal row (payee)
• NEVER merge CHARGES with its associated payee — they are TWO separate transactions.
• NEVER treat the payee amount as including the CHARGES amount.

CREDIT INTEREST — bank interest (ALWAYS Deposit, NEVER Withdrawal):
• "CREDIT INTEREST" is the bank's own interest payment credited to the account.
  It is ALWAYS a deposit → deposit = <printed amount>, withdrawal = null.
• This rule is ABSOLUTE. CREDIT INTEREST is NEVER a withdrawal under any circumstances.
  If you see it in the Withdrawal column, re-read — it will be in the Deposit column.

SAMPLE AGG HONG KONG L MERCHANTS — periodic merchant aggregation deposit:
• "SAMPLE AGG HONG KONG L" / "MERCHANTS" is a two-line description for a DEPOSIT.
  e.g. description = "SAMPLE AGG HONG KONG L MERCHANTS", deposit = <amount>

ACCOUNT_TYPE — must be one of exactly three strings, no extra text:
• The account_type field MUST be EXACTLY one of these three strings:
    "HSBC Business Direct HKD Savings"
    "HSBC Business Direct HKD Current"
    "HSBC Business Direct Foreign Currency Savings"
• NEVER append Chinese characters, section subtitles, or any other text.
  ❌ WRONG: "HSBC Business Direct HKD Savings 滙豐「理財易」商戶戶口 - 港元儲蓄"
  ✓ CORRECT: "HSBC Business Direct HKD Savings"
• If the page header shows a Chinese subtitle under the section name, ignore it.
  Only use the English section name as listed above.

DESCRIPTION ORDER — payee name ALWAYS comes first:
• Each transaction description follows the order: [payee name] [reference code] [date code]
  ✓ CORRECT: "SAMPLE PAYMENTS EURO HC125Cxxxxxxxxxx DDMON"
  ❌ WRONG:   "HC125Cxxxxxxxxxx DDMON SAMPLE PAYMENTS EURO"
• If you can only read the reference code (no payee name visible), output just the
  reference code — do not reorder it.

PAGE BREAKS — each page is fully independent, no description bleeding:
• HSBC statements frequently break mid-day-group across pages. The FIRST transaction
  visible at the top of this page is a FRESH, SEPARATE transaction — its description
  starts with whatever text is printed FIRST on this page.
• Do NOT prepend or append text from the previous page's last transaction to any
  transaction on this page. Each page boundary is a hard separator for descriptions.
• If this page's transaction table starts mid-day (no "N Dec" date label at the very
  top, just a continuation of rows), each row is still its own separate transaction.
• Count every physically distinct transaction row on this page — output one JSON object
  per row. Do NOT merge rows even if they share similar descriptions or the same date.

CURRENCY:
• HKD Savings / HKD Current sections: currency = "HKD"
• Foreign Currency Savings: currency = the CCY printed in Column 0 (e.g. "AUD")

EMPTY HSBC ACCOUNT SECTIONS — apply UNIVERSAL 無交易 rule:
• The HKD Current section and the Foreign Currency Savings section frequently have
  NO real transactions for the month — only a B/F BALANCE opening line and a closing
  balance line (今期結餘 / CLOSING BALANCE / CARRIED FORWARD).
• Do NOT skip these sections. Do NOT treat them as Portfolio Summary.
  They have their own column-header rows and their own balance tables.
• For each such empty section, apply the UNIVERSAL RULES EMPTY ACCOUNT SECTIONS rule:
  output exactly ONE "無交易" row with:
    "description": "無交易"           ← EXACT string, required
    "deposit": null
    "withdrawal": null
    "balance": <closing balance printed in 今期結餘 or CLOSING BALANCE / CARRIED FORWARD>
    "transaction_date": <statement period-end date in YYYY-MM-DD>
    "account_type": "HSBC Business Direct HKD Current"
                    OR "HSBC Business Direct Foreign Currency Savings"
    "currency": "HKD"  (or the FX CCY code for the FX section)
• This is the only way the closing balance of those sections is preserved for
  accounting. Without it, the HKD Current balance and FX balance are lost entirely.

SKIP THESE ROWS (non-transaction content):
• Portfolio Summary header and balance rows (Total balance in HKD, Net Position, etc.)
• Portfolio Summary account table (Account Number / CCY / Balance / HKD Equivalent rows)
• Exchange Rate table (e.g. "AUD 5.242800")
• Column header rows (Date, Transaction Details, Deposit, Withdrawal, Balance)
• Total No. of Deposits / Total No. of Withdrawals lines
• Total Deposit Amount / Total Withdrawal Amount lines
• Special Privileges, Others, and legal notice sections
• Page header/footer text (HSBC Business Direct Statement, Number, Branch, Page N of N)
• "B/F BALANCE" — handle via the B/F BALANCE ANCHOR rule in UNIVERSAL_RULES instead

━━━ CROSS-SECTION ARITHMETIC — FORBIDDEN ━━━
This page may have multiple account sections (HKD Current → HKD Savings → Foreign Currency Savings).
Each section starts with its own B/F BALANCE (opening balance). The amounts in Deposit and
Withdrawal columns are completely independent between sections.

  Section A last balance:        [BALANCE_A]  ← belongs to Section A only
  Section B B/F BALANCE:         [BALANCE_B]  ← belongs to Section B only
  Section B first transaction,
    Column 5 (Balance):          [BALANCE_C]

  ❌ WRONG:  deposit    = [BALANCE_C] − [BALANCE_A]  ← mixes two different sections
  ❌ WRONG:  withdrawal = [BALANCE_A] − [BALANCE_C]  ← same cross-section error, opposite direction
  ❌ WRONG:  deposit    = [BALANCE_C] − [BALANCE_B]  ← arithmetic even with the correct reference
  ✓ CORRECT: deposit    = whatever number is PRINTED in Column 3 for that row
             withdrawal = whatever number is PRINTED in Column 4 for that row
             If Column 3 is blank on that row → deposit = null
             If Column 4 is blank on that row → withdrawal = null

Illustrative example of this bug to avoid:
  HKD Current last balance:               100.00   ← belongs to HKD Current only
  HKD Savings B/F BALANCE:             50,000.00   ← belongs to HKD Savings only
  HKD Savings first transaction balance: 56,500.00
  ❌ WRONG to output: deposit = 56,500.00 − 100.00 = 56,400.00   ← mixes sections, massively inflated
  ❌ WRONG to output: deposit = 56,500.00 − 50,000.00 = 6,500.00  ← correct number but wrong method
  ✓ CORRECT: read the Deposit column for that row — the printed number is 6,510.00 → deposit = 6,510.00
""" + UNIVERSAL_RULES + """
━━━ OUTPUT FORMAT ━━━
(FICTIONAL values shown only to illustrate JSON structure — these numbers do NOT come from
 any real document. DO NOT reproduce them. Read ALL values from the actual page image.)
{
  "bank_id": "HSBC",
  "account_no": "account number if visible on page, else null",
  "transactions": [
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "B/F BALANCE",
      "deposit": null,
      "withdrawal": null,
      "balance": 50000.00,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 1.0
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "FROM PAYME(HSBC)xxx Txxxxxx(DDMONYY)",
      "deposit": 1500.00,
      "withdrawal": null,
      "balance": null,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "SAMPLE PAYMENTS EURO HC125Cxxxxxxxxxx DDMON",
      "deposit": 8000.00,
      "withdrawal": null,
      "balance": null,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "SAMPLE WALLET HK LTD HC125Cxxxxxxxxxx DDMON",
      "deposit": 12000.00,
      "withdrawal": null,
      "balance": 71500.00,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "CHARGES HC125Cxxxxxxxxxx DDMON",
      "deposit": null,
      "withdrawal": 5.00,
      "balance": null,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.99
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "[PAYEE NAME] HC125Cxxxxxxxxxx DDMON",
      "deposit": null,
      "withdrawal": 3000.00,
      "balance": null,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "CR TO xxx-xxxxxx-xxx NARRATIVE (DDMONYY)",
      "deposit": null,
      "withdrawal": 20000.00,
      "balance": null,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "SAMPLE PAYMENTS EURO HC125Cxxxxxxxxxx DDMON",
      "deposit": 4000.00,
      "withdrawal": null,
      "balance": 52495.00,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 0.95
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "CREDIT INTEREST",
      "deposit": 1.50,
      "withdrawal": null,
      "balance": 56496.50,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Savings",
      "account_number": "xxx-xxxxxx-xxx",
      "categorise": "",
      "confidence_score": 1.0
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "無交易",
      "deposit": null,
      "withdrawal": null,
      "balance": 500.00,
      "currency": "HKD",
      "account_type": "HSBC Business Direct HKD Current",
      "account_number": null,
      "categorise": "",
      "confidence_score": 1.0
    },
    {
      "transaction_date": "YYYY-MM-DD",
      "value_date": null,
      "description": "無交易",
      "deposit": null,
      "withdrawal": null,
      "balance": 200.00,
      "currency": "AUD",
      "account_type": "HSBC Business Direct Foreign Currency Savings",
      "account_number": null,
      "categorise": "",
      "confidence_score": 1.0
    }
  ]
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# HSBC AR manager (model B) — full-page audit pass after bookkeeper extraction.
# Runtime prompt = HSBC_AR_MANAGER_PROMPT_PREFIX + "\n\nBOOKKEEPER_DRAFT_JSON:\n" + snapshot.
# ─────────────────────────────────────────────────────────────────────────────
HSBC_AR_MANAGER_PROMPT_PREFIX = """
You are auditing ONE page of an HSBC Business Direct bank statement. You receive:
(1) the page image, and (2) BOOKKEEPER_DRAFT_JSON — the bookkeeper's rows for this page
(same order as real transactions). Your job is BALANCE COLUMN ONLY.

Output ONLY valid JSON (no markdown): { "transactions": [ ... ] }

--- STEP 1: TABLE HEADER CHECK ---
Look for the printed column header row:
  HKD: "Date   Transaction Details   Deposit   Withdrawal   Balance"
  FX:  "CCY   Date   Transaction Details   Deposit   Withdrawal   Balance"
If that header row is ABSENT (cover, portfolio summary, legal page, etc.), output
{ "bank_id": "HSBC", "transactions": [] } and stop. Do not invent rows from large
balance figures or summary text.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per transaction row (idx 0, 1, ...). You MUST output
exactly the SAME number of objects in "transactions", in the SAME order (first object =
same row as idx 0, etc.). Do not add or remove rows.

--- B/F AND SECTION OPENING ROWS (balance only on the statement) ---
Each account section (HKD Current, HKD Savings, FCY Savings, etc.) may begin with an opening
row where Transaction Details look like "B/F BALANCE", "承前轉結", "承上餘額", "Brought Forward",
or similar — and the Deposit and Withdrawal cells are BLANK. That row is NOT a payment:
• Keep deposit and withdrawal null in your output (same as every row).
• Set balance to the amount printed in the Balance column for that row only (opening balance
  for that section).
• Do not move that balance into deposit or withdrawal.

--- READING BALANCE (printed column + light disambiguation) ---
For EACH object:
• deposit — ALWAYS null (do not read or copy the Deposit column).
• withdrawal — ALWAYS null (do not read or copy the Withdrawal column).
• balance — read the value from the Balance column cell aligned with THAT transaction row.
  - If the Balance cell is blank, use null (HSBC often leaves balance blank except on the
    last transaction of a date group).
  - Do not copy a balance from another row into this row.
  - Do not compute running balances by arithmetic from B/F + deposits/withdrawals.
  - If digits in the Balance cell are ambiguous (blur/OCR), you may use the section B/F
    balance and the other printed balances visible on the page as context to choose among
    plausible readings — still pick the reading that matches what is printed in this row's
    Balance cell, not a recalculated total.

• transaction_date, description, account_type, etc. — copy from the matching BOOKKEEPER_DRAFT_JSON entry for alignment; you may leave extras null if unsure.
• Do not fabricate numeric balances; if illegible, use null.

The downstream merge only uses your balance field to fill gaps in the bookkeeper; In/Out
amounts always stay with the bookkeeper for manual correction if needed.
""".strip()

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT_V2 — used by the prescan-driven pipeline (_hsbc_process_page_v2).
# The VLM only reads descriptive text; amounts come from PyMuPDF prescan.
# ─────────────────────────────────────────────────────────────────────────────
PROMPT_V2 = """
You are reading a single page of an HSBC Business Direct bank statement.

The numerical amounts in the Deposit, Withdrawal, and Balance columns are
handled separately — DO NOT read or output any numbers from those columns.

Your ONLY task is to identify each transaction row and output its:
  • "y_pct"      — the vertical position of this row, expressed as a percentage
                   of the total page height (0 = top, 100 = bottom).
                   Use the TOP edge of the transaction row text.
  • "description" — full text from the Transaction Details / Description column.
                    If a transaction spans two printed lines, join them with a
                    single space (e.g. "LINE ONE LINE TWO").
                    Read verbatim — NEVER invent or modify text.
                    If you are not sure, leave it blank ("").
  • "date_label"  — the date group header for this transaction row, e.g. "7 Nov"
                    or "14 Jan".  Use the most recent date label printed ABOVE
                    this row.  If none is visible, leave it blank ("").
  • "account_type" — the account section this row belongs to.
                    Must be EXACTLY one of:
                      "HSBC Business Direct HKD Current"
                      "HSBC Business Direct HKD Savings"
                      "HSBC Business Direct Foreign Currency Savings"
                    If the section header is not visible, use
                    "HSBC Business Direct HKD Current".

Rules:
1. Output ONE entry per physical transaction row (each row that has a
   Deposit or Withdrawal amount printed beside it).
2. DO NOT output rows for balance-only lines, date headers, section headers,
   column headers, or any line that has no Deposit/Withdrawal amount.
3. If a section header is visible but there are no transactions under it,
   do NOT output any row for that section (the pipeline handles empty sections).
4. DO NOT output any numeric values — no amounts, no balances, no dates as
   numbers. Descriptions may contain reference codes that include digits; that
   is fine.
5. Preserve the order of rows as they appear top-to-bottom on the page.

Output valid JSON only — no markdown fences:
{
  "rows": [
    {
      "y_pct": <0-100 float>,
      "description": "<verbatim text>",
      "date_label": "<e.g. 7 Nov>",
      "account_type": "<one of the three strings above>"
    }
  ]
}
"""
