"""
Universal extraction rules shared by ALL bank statement prompts.

Every bank-specific prompt appends UNIVERSAL_RULES at the end so that guardrails
discovered from debugging any bank automatically apply to every bank and every
future prompt added to this package.
"""

UNIVERSAL_RULES: str = """
━━━ UNIVERSAL RULES (apply to all banks) ━━━

OUTPUT FORMAT
• Output ONLY a valid JSON object — no markdown fences, no code blocks, no explanation.
• All numbers must be plain float values (no comma separators): 29,491.54 → 29491.54

AMOUNTS
• Each transaction has EITHER a deposit OR a withdrawal — never both non-null simultaneously.
• Use null ONLY when the deposit or withdrawal cell is genuinely BLANK/EMPTY on the page
  (i.e. no number is printed there at all).
• If a number IS printed in the deposit or withdrawal column but is hard to read, output
  your best float estimate — never output null for a visible number.
• NEVER copy the BALANCE (running total) into the deposit or withdrawal field.
  The deposit/withdrawal amount and the balance are always different numbers on the same row.
• NEVER compute deposit or withdrawal through arithmetic — no subtraction, no delta, no
  net calculation. The deposit and withdrawal values are always PHYSICALLY PRINTED in their
  own columns on the page. Read the printed cell value directly; do not derive it.
• When a page contains multiple account sections, each section is COMPLETELY INDEPENDENT.
  The closing balance of one section is NEVER the opening balance of the next section.
  NEVER use the last balance from section A to compute any amount in section B.

BALANCE (running total) — MANDATORY
• The running balance MUST always be output as a non-null float for every transaction row.
• Read the rightmost number on each transaction row — that is the running balance.
• If one digit is unclear, make your best estimate; a slightly-off balance is far more
  useful than null, because null prevents amount recovery from balance deltas.

DATES — EVERY ROW MUST HAVE A DATE
• EVERY transaction row in your output MUST have a non-null transaction_date in YYYY-MM-DD.
  A row without a date is invalid and will be discarded by the system.
• If a date label appears only on the first row of a date group, every following row in
  that group (until the next date label) shares that SAME date — copy it explicitly to
  every row in the group. Do NOT leave transaction_date blank for group members.
• Convert to YYYY-MM-DD:
    DDMONYY  →  31MAR25 = 2025-03-31 | 10JUN25 = 2025-06-10 | 26JUN25 = 2025-06-26
    YYYY/MM/DD  →  2022/01/03 = 2022-01-03
    DD/MM/YYYY  →  03/01/2022 = 2022-01-03
• NEVER extract a date from an FRN reference number or any other transaction reference code.
  e.g. FRN20250610PAYC010 is a payment reference — "20250610" inside it is NOT a date.
• If the page has no visible date at all, use null — but this means the row likely came from
  a non-transaction section and should not have been included in the first place.

TRANSACTION SUMMARY BLOCK
• At the bottom of some pages there may be a footer block showing aggregate totals for the
  ENTIRE statement period, for example:
      TRANSACTION SUMMARY
      259,509.19   248,320.00   AMOUNT
      15   10   ITEM(S)
      CARRIED FORWARD   27JUN25   86,049.42   CREDIT BALANCE
• These are statement-level totals, NOT individual transactions.
• SKIP the entire block. NEVER use those aggregate figures as deposit or withdrawal amounts.
• "15   10   ITEM(S)" is the total item COUNT for the period — NOT a transaction.
  Do NOT output "15", "10", or "8" (or any count number) as a deposit, withdrawal, or balance.
• "CARRIED FORWARD … CREDIT BALANCE" is a closing footer line — NOT a transaction.

B/F BALANCE ANCHOR (special case — include, do NOT skip)
• If the ACCOUNT ACTIVITIES section on this page starts with a row whose printed description
  is literally "B/F BALANCE" or "BALANCE B/F" (a carry-forward opening balance from the
  prior page), include it in the output as:
    { "description": "B/F BALANCE", "deposit": null, "withdrawal": null, "balance": <value> }
  where <value> is the ACTUAL balance printed next to that label on the page.
• Do NOT fabricate or invent any amount for this row — deposit and withdrawal must be null.
• Do NOT apply this rule to rows from PORTFOLIO SUMMARY, ACCOUNT SUMMARY, or any other
  section. ONLY rows explicitly labelled "B/F BALANCE" / "BALANCE B/F" qualify.
• The system will automatically use the balance as the page opening balance and will exclude
  this row from the final output.

ROWS TO SKIP — do NOT include any of the following in the output:
  English:  B/F, CARRIED FORWARD, CARRY FORWARD, BROUGHT FORWARD,
            OPENING BALANCE, CLOSING BALANCE, CREDIT BALANCE,
            TRANSACTION SUMMARY, NET POSITION, TOTAL OVERDRAFT LIMIT,
            TOTAL TRANSACTION AMOUNT, NO.OF TRANSACTION, NO. OF TRANSACTION
  Chinese:  承前結餘, 承前结余, 今期結餘, 今期结余, 合計, 合计, 賬戶結餘, 账户结余,
            交易總金額, 交易笔数, 交易筆數

SECTIONS TO SKIP — non-transaction content:
  PORTFOLIO SUMMARY, ACCOUNT SUMMARY, FOREIGN CURRENCY DEPOSIT, INVESTMENT,
  OVERDRAFT overview rows, remarks / legal notice pages.

DESCRIPTIONS
• If a description spans multiple lines, join all lines with a single space.
• Keep reference numbers (FRNxxxxxxxx, SO-xxxxxxx, numeric codes) as part of the description.

NO DUPLICATE ROWS
• Every running balance on a page must be unique. Each transaction changes the balance, so
  the same balance value MUST NOT appear more than once in your output for this page.
• If you find yourself about to output a row whose balance already appeared earlier in your
  output for this page, STOP — you are echoing a transaction you already wrote. Do not
  output it again.
• Do NOT repeat a transaction you have already output. Each physical row in the statement
  table must appear exactly once in the JSON.
• Any row that has BOTH a null/missing date AND null/missing amounts (deposit + withdrawal)
  is invalid — do not include it. Such rows come from summary panels, not real transactions.
  (The scoring system penalises dateless rows by −1.5 and duplicate-balance rows by −2.0
  per row. Outputting several such rows will cause the entire result to score near zero,
  meaning the correct result from the other processing track will be chosen instead.)

NEVER COMBINE TRANSACTIONS — NEVER NET AMOUNTS
• A withdrawal followed by a deposit (or vice versa) are always TWO SEPARATE transactions.
  NEVER compute the net: 2,610 withdrawal + 2,040 deposit ≠ one combined 570 withdrawal.
• Two consecutive rows of the SAME type are equally forbidden from being merged:
  5,775 withdrawal + 100,000 withdrawal ≠ one combined 105,775 withdrawal.
  Same description type (e.g., both TRANSFER-DEBIT) does NOT mean they are one transaction.
• Each physical transaction row in the table must produce exactly ONE JSON object.
  Do not merge consecutive rows even if they share the same date or similar descriptions.
• NEVER SKIP an intermediate balance: if the table shows balances
  A → B → C, you must output three rows (or two if A is a B/F anchor). Jumping from A to C
  by combining two transactions is forbidden.
• COUNT the distinct running balance values visible in the transaction table — that is the
  minimum number of transaction rows you must output for this page.

EMPTY ACCOUNT SECTIONS (special case — one row per inactive section)
• If an account section contains NO real transaction rows — only balance summary lines
  such as 承前結餘/今期結餘, OPENING BALANCE/CLOSING BALANCE, or CARRIED FORWARD —
  output EXACTLY ONE summary row with these exact fields:
    "description": "無交易"    ← use this EXACT string — any other label will be discarded
    "deposit": null
    "withdrawal": null
    "balance": <closing balance from 今期結餘 or CLOSING BALANCE/CARRIED FORWARD line>
    "transaction_date": <period end date from statement header or closing row, YYYY-MM-DD>
    "account_type": <exact section header label, e.g. 外幣儲蓄, HKD CURRENT>
    "currency": <currency for that section>
    "confidence_score": 1.0
• EXCEPTION to the "null amounts = invalid" rule below: a 無交易 row is valid even
  though deposit and withdrawal are both null, because it carries the closing balance
  needed for accounting records. Do NOT discard it.
• Do NOT output 承前結餘, 今期結餘, OPENING BALANCE, or CLOSING BALANCE rows directly —
  only the single 無交易 summary row described above.
• If a section HAS real transactions, output those transactions normally — do NOT add
  a 無交易 row in addition to them.

DATA INTEGRITY
• NEVER fabricate or invent transactions.
• NEVER create a summary or aggregate transaction to make running balances add up.
• Output ONLY the individual transaction rows that are actually visible in the transaction
  table (ACCOUNT ACTIVITIES / main ledger section). Do NOT extract numbers from
  PORTFOLIO SUMMARY, ACCOUNT SUMMARY, balance overview panels, or any non-table section.
• For text fields (description, reference, date): if genuinely unreadable, output null.
• For NUMERIC fields (deposit, withdrawal, balance): if the number is PRINTED on the page
  but one digit is unclear, output your best reading — never output null for a visible number.
  Only output null for deposit/withdrawal when the cell is completely blank on the page.
  Balance must never be null (see BALANCE MANDATORY above).
• Include confidence_score (0.0–1.0) for every row based on how clearly you can read it.

BLANK IS BETTER THAN FABRICATED
• If you cannot clearly read a description or payee name from the page image, output only
  the portion you CAN read (e.g. just the reference code). Output null if nothing is legible.
  Do NOT guess, infer, or invent a name from training knowledge or surrounding context.
• A fabricated description with a correct amount is WORSE than a blank description with a
  correct amount — a wrong payee name cannot be corrected downstream.
• Do NOT append language labels, transaction-type suffixes, or category text to descriptions
  that are not physically printed on the page. Output ONLY what is literally visible.
  FORBIDDEN examples:
    ❌ description = "SAMPLE PAYMENTS EURO HC125C... 03DEC 轉賬收入"
       (suffix "轉賬收入" is not printed in the document — do not add it)
    ❌ description = "PAYEE A ... PAYEE B ..."
       (two separate payee names — these are two separate rows, never merge them)
    ✓ description = "SAMPLE PAYMENTS EURO HC125Cxxxxxxxxxx 03DEC"
       (verbatim from the page — correct)
• For amounts: if a number IS printed but hard to read, output your best reading with a low
  confidence_score. Only output null for deposit/withdrawal when the cell is completely blank.
  NEVER compute an amount to fill in a cell that appears blank on the page.
"""
