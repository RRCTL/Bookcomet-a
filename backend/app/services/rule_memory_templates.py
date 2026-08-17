"""
Rule Memory Starter Templates
==============================
Provides default Markdown rule memory content for new Hong Kong SME companies.
The General template covers most SMEs across retail, professional services,
trading, and F&B in Hong Kong.
"""
from __future__ import annotations

_HK_GENERAL_TEMPLATES: dict[str, str] = {
    "AR": """# AR Rules Memory — {company_name}

## AI Behaviour Instructions
*(Hints for the OCR image reader — vendor-specific layout knowledge)*
- Invoice numbers are typically labelled "Invoice No.", "Inv No.", "單號", or "發票號碼"
- Treat "Invoice Date", "Date", "日期" as the document date field
- For Hong Kong invoices, amounts may appear in HKD, USD, or RMB — always note the currency symbol
- "Subtotal", "Sub-total", "小計", and "Net Amount" all refer to the pre-tax amount
- "Total", "Grand Total", "總計", "應收金額" refer to the total payable amount

## Document Defaults
*(Lowest priority: fills empty fields when no other rule matches)*
- Currency: HKD
- Tax Code: ST
- Transaction Type: income

## Keyword Rules
*(Medium priority: applied when keyword is found anywhere in the document)*
- "consulting", "consultancy", "顧問", "諮詢" → Account: 4003
- "service fee", "service charge", "服務費" → Account: 4003
- "sales", "goods", "merchandise", "貨物", "商品" → Account: 4001
- "rental", "rent", "lease", "租金", "租賃" → Account: 4010
- "freight", "delivery", "shipping", "運費", "送貨" → Account: 5010

## Vendor-Specific Rules
*(Highest priority: applied only when this vendor is matched)*
*(Format: - Vendor Name → Account: 4001, Tax: ST)*
""",

    "AP": """# AP Rules Memory — {company_name}

## AI Behaviour Instructions
*(Hints for the OCR image reader — vendor-specific layout knowledge)*
- Receipt/invoice numbers labelled "Receipt No.", "Rec No.", "收據號碼", "Invoice No." are the document reference
- Supplier names may appear at the top or as a stamp — prioritise the clearly printed name
- For receipts, "Total", "Total Amount", "總金額", "應付金額" is the payable amount
- "GST", "VAT", "Tax Amount", "稅額" refers to the tax portion of the amount
- PO references may appear as "PO No.", "Purchase Order", "採購單號"

## Document Defaults
*(Lowest priority: fills empty fields when no other rule matches)*
- Currency: HKD
- Tax Code: ST
- Transaction Type: expense

## Keyword Rules
*(Medium priority: applied when keyword is found anywhere in the document)*
- "office supplies", "stationery", "文具", "辦公用品" → Account: 6010
- "utilities", "electricity", "water", "電費", "水費", "煤氣" → Account: 6020
- "rental", "rent", "lease", "租金", "租賃" → Account: 6030
- "insurance", "保險" → Account: 6040
- "telephone", "internet", "phone", "電話", "寬頻" → Account: 6050
- "meals", "entertainment", "food", "餐飲", "飯局", "娛樂" → Account: 6060
- "professional fees", "legal", "audit", "律師", "核數", "會計" → Account: 6070
- "repairs", "maintenance", "維修", "保養" → Account: 6080
- "freight", "delivery", "courier", "運費", "快遞" → Account: 6090
- "advertising", "marketing", "廣告", "推廣" → Account: 6100

## Vendor-Specific Rules
*(Highest priority: applied only when this vendor is matched)*
*(Format: - Vendor Name → Account: 6010, Tax: ST)*
""",

    "BANK": """# BANK Rules Memory — {company_name}

## AI Behaviour Instructions
*(Hints for the OCR image reader — bank statement layout knowledge)*
- Hong Kong bank statements typically have columns: Date, Description/Particulars, Debit, Credit, Balance
- HSBC: transaction reference appears in the "Particulars" column after the transaction description
- Bank of China (BOC): dates are in DD/MM/YYYY format, balance column is on the far right
- Standard Chartered: uses "Dr" (debit) and "Cr" (credit) notations
- Hang Seng: may show running balance in a separate "Balance" column
- ATM withdrawals often show as "ATM WDL", "CASH WDL", or "提款"
- FPS transfers show as "FPS", "快速支付", "轉數快"
- Autopay charges show as "AUTOPAY", "自動轉賬", "代繳"

## Document Defaults
*(Lowest priority: fills empty fields when no other rule matches)*
- Currency: HKD
- Tax Code: EX

## Keyword Rules
*(Medium priority: applied when keyword is found anywhere in the transaction description)*
- "salary", "payroll", "wages", "薪金", "薪酬" → Account: 6200
- "rent", "rental", "租金" → Account: 6030
- "utilities", "elec", "water", "電費", "水費" → Account: 6020
- "insurance", "insur", "保險" → Account: 6040
- "transfer in", "fps credit", "fps incoming", "入數", "轉入" → Transaction Type: receipt
- "transfer out", "fps debit", "fps outgoing", "出數", "轉出" → Transaction Type: payment
- "cheque", "chq", "支票" → Transaction Type: cheque
- "atm", "cash withdrawal", "提款" → Transaction Type: cash_withdrawal
- "interest", "利息" → Account: 7010
- "bank charge", "service charge", "手續費", "銀行費用" → Account: 6110

## Vendor-Specific Rules
*(Highest priority: applied only when this vendor is matched)*
*(Format: - Vendor Name → Account: 6200, Transaction Type: payment)*
""",

    "OTHER": """# OTHER Rules Memory — {company_name}

## AI Behaviour Instructions
*(Hints for processing asset and liability documents)*
- Loan agreements typically include: Lender Name, Loan Amount, Interest Rate, Tenor, Start Date, Maturity Date
- Fixed asset invoices show: Supplier, Asset Description, Purchase Amount, Purchase Date
- Hong Kong stamp duty certificates reference the property address and transaction date
- Hire purchase agreements should be treated as finance leases — classify as Fixed Asset
- Equipment invoices with value > HKD 10,000 should be capitalised as Fixed Assets

## Document Defaults
*(Lowest priority: fills empty fields when no other rule matches)*
- Currency: HKD
- Depreciation Method: straight_line

## Keyword Rules
*(Medium priority: applied when keyword is found anywhere in the document)*
- "bank loan", "term loan", "overdraft", "銀行貸款", "定期貸款" → Transaction Type: bank_loan
- "hire purchase", "hp agreement", "分期付款" → Transaction Type: hire_purchase
- "mortgage", "物業貸款", "按揭" → Transaction Type: mortgage
- "computer", "laptop", "server", "電腦", "伺服器" → Asset Type: computer_equipment
- "vehicle", "car", "van", "truck", "汽車", "貨車" → Asset Type: motor_vehicle
- "furniture", "fixture", "furnishing", "傢俬", "裝修" → Asset Type: furniture_and_fixtures
- "machinery", "equipment", "機器", "設備" → Asset Type: plant_and_machinery

## Vendor-Specific Rules
*(Highest priority: applied only when this vendor is matched)*
*(Format: - Bank/Vendor Name → Transaction Type: bank_loan, Currency: HKD)*
""",
}


def get_starter_template(mode: str, company_name: str = "") -> str:
    """
    Return the HK General SME starter template for the given mode.
    Substitutes company_name into the header if provided.
    """
    template = _HK_GENERAL_TEMPLATES.get(mode, "")
    if not template:
        # Fallback to a minimal blank structure
        from app.services.rule_memory_parser import build_empty_md
        return build_empty_md(mode, company_name)
    return template.format(company_name=company_name or "My Company")


def get_ai_generated_template(
    mode: str,
    company_name: str,
    business_description: str,
    llm_reply: str,
) -> str:
    """
    Merge the AI-generated custom rules with the HK base template.
    The LLM is expected to return additional rule lines in MD format;
    this function prepends the base template sections and appends the custom ones.
    """
    base = get_starter_template(mode, company_name)
    if not llm_reply or not llm_reply.strip():
        return base
    # Append any lines from llm_reply that look like rule lines (bullet points)
    custom_lines = [
        line for line in llm_reply.splitlines()
        if line.strip().startswith("- ") and "→" in line
    ]
    if not custom_lines:
        return base
    return base + "\n".join(custom_lines) + "\n"
