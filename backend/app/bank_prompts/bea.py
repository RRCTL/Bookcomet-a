"""
Bank of East Asia (BEA) Hong Kong — VLM prompts and detection keywords.

Typical ACCOUNT ACTIVITIES layout (English / Chinese bilingual headers):
  Cur | Date | Bk Ref | Transaction Details | Deposit | Withdrawal | Balance
  貨幣 | 日期 | 銀行備考 | 交易項目 | 存入 | 支出 | 結餘

Quirks: Cur is often filled ONLY on the Balance B/F line (e.g. HKD) and blank on
transaction rows — never shift Date into Currency when Cur is empty. Dates are
often DDMMMYY (05AUG20). A 4-digit Bk Ref sits after Date. Wrapped lines merge
into description. Section footers "Total Transaction Amount / 交易總金額" are not
transactions.
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

TASK: Extract every transaction row from the main ACCOUNT ACTIVITIES table on this page.
Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

TABLE DETECTION
Look for a header row with BOTH credit/debit style columns, e.g.:
  English: Deposit / Deposits / Credit AND Withdrawal / Withdrawals / Debit
  Chinese: 存入 AND 支出
  Plus Date (日期) and Balance (結餘). A leading Cur / Currency / 貨幣 column is common.

If that header row is ABSENT, output {"bank_id": "BEA", "account_no": null, "transactions": []} and stop.
Do NOT extract rows from portfolio summary / cover pages (e.g. "PORTFOLIO SUMMARY", "ACCOUNT PORTFOLIO",
\u8ca1\u52d9\u7d44\u5408\u6458\u8981, \u8cec\u6236\u7d44\u5408) when there is no real account activity table with 存入 + 支出 as above.

COLUMN MAPPING (typical BEA ACCOUNT ACTIVITIES — map by HEADER, not by assuming column 1 is filled)
Left to right when present:
  Cur / Currency / 貨幣 → currency (3-letter code ONLY, e.g. HKD). Often BLANK on txn rows.
  Date / 日期 → transaction_date
  Bk Ref / 銀行備考 → keep inside description (do NOT treat as an amount or date)
  Transaction Details / 交易項目 → description
  Deposit / 存入 → deposit (money IN)
  Withdrawal / 支出 → withdrawal (money OUT)
  Balance / 結餘 → balance

CRITICAL — BLANK CUR COLUMN
• On Balance B/F / 承上結餘 the Cur cell may show HKD (or another code). Carry that currency
  for following rows in the same account section.
• On ordinary transaction rows Cur is usually EMPTY. NEVER shift Date into the currency field.
• currency must be a 3-letter code (HKD/USD/…) or null — NEVER a date, NEVER an amount,
  NEVER "2020-08-11 4,600.00".

DATES
• Printed dates are often DDMMMYY with no separators (05AUG20, 11AUG20) → YYYY-MM-DD.
• Also accept DD/MM/YYYY. Never invent dates from FRN reference digits.

CONTINUATION / WRAP LINES
• Lines under a txn with no date and no deposit/withdrawal (e.g. "TO FPS ID …", or a lone
  cheque date like 10AUG20) MERGE into the previous row's description — do not emit new rows.

SECTIONS & FOOTERS
• A page may have multiple accounts (HKD CURRENT ACCOUNT / STATEMENT SAVINGS ACCOUNT), each
  with its own Balance B/F and a "Total Transaction Amount / 交易總金額" footer.
• Include Balance B/F / 承上結餘 as an opening row (deposit=null, withdrawal=null, balance=printed).
• SKIP Total Transaction Amount / 交易總金額 / 交易筆數 — not transactions.

AMOUNTS
• Only one of Deposit / Withdrawal is filled per row; Balance is always filled when printed.
• Read printed amounts only; never compute from balance.

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

Numerical amounts in Deposit, Withdrawal, and Balance columns are handled separately.
DO NOT output any numbers from those columns.

Typical headers: Cur | Date | Bk Ref | Transaction Details | Deposit | Withdrawal | Balance.
Cur is often blank on txn rows — do not invent a currency in date_label.

For each transaction row that has either a withdrawal or a deposit amount on the page, output:
  "y_pct" — row top as percent of page height (0-100), from the description/particulars line.
  "description" — full particulars text; join wrapped lines with a space; include Bk Ref text
                  when printed next to the date (e.g. "1005 FPS TRANS WDL …"); verbatim.
  "date_label" — DDMMMYY (05AUG20), DD/MM/YYYY, DD-MM-YYYY, day+month label (e.g. 7 Nov), or "".
                 NEVER put a currency code here. NEVER put an amount here.
  "account_type" — short product/section label if visible, else "".

Rules:
1. One entry per row that has a printed withdrawal OR deposit amount.
2. Skip column headers and non-transaction banners.
3. If this page is only a portfolio/cover summary with no activity table (no 存入+支出 headers), return {"rows": []}.
4. Skip INFORMATION / 資料 footer legal blocks and any "Total Transaction Amount" / \u4ea4\u6613\u7e3d\u91d1\u984d subtotal lines.
5. For account_type, use short labels when visible: prefer "HKD CURRENT" or "HKD STATEMENT SAVINGS" to match the section title.
6. Merge wrapped particulars into the same row's description (do not emit a separate row for a continuation line).
7. No numeric amounts in JSON.
8. Top-to-bottom order.

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
(or Withdrawal/Debit), plus a balance-style column in context. A leading Cur/貨幣 column may be
blank on most rows — that is normal.
If this page is cover/portfolio only, INFORMATION / 資料 footer only, or has no such table,
output { "bank_id": "BEA", "transactions": [] } and stop.

--- STEP 2: WHEN A TABLE EXISTS ---
BOOKKEEPER_DRAFT_JSON has one entry per row (idx 0, 1, ...). You MUST output exactly the SAME
number of objects in "transactions", in the SAME order. Do not add or remove rows.

Section titles may include HKD CURRENT, HKD STATEMENT SAVINGS, etc. — use them only for alignment context.

--- B/F ROWS (balance only) ---
Opening rows (B/F BALANCE, 承上結餘, 承前, etc.) have blank deposit and withdrawal on the statement.
Keep deposit and withdrawal null in your output. Set balance to the printed opening balance for that row only.

--- READING BALANCE ---
For EACH object:
- deposit — ALWAYS null.
- withdrawal — ALWAYS null.
- balance — read from the Balance column for that row; null if the cell is blank.
- Do not compute balances by arithmetic.
- Do not put dates or amounts into any currency field.

The downstream merge only fills missing bookkeeper balances from your output; amounts stay with the bookkeeper.
""".strip()
