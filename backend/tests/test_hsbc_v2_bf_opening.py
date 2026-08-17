"""HSBC V2: brought-forward (B/F) opening rows from prescan balances."""

from app.services.bank_statement_parser import BankStatementParser


def test_hsbc_v2_bf_opening_by_section_two_accounts():
    cur = "HSBC Business Direct HKD Current"
    sav = "HSBC Business Direct HKD Savings"
    sections = [
        {"y": 100.0, "header": cur},
        {"y": 400.0, "header": sav},
    ]
    amounts = [
        {"y": 200.0, "col": "Cr", "amount": 276.0},
        {"y": 500.0, "col": "Dr", "amount": 50.0},
    ]
    balances = [
        {"y": 150.0, "amount": 5033.70},
        {"y": 250.0, "amount": 5309.70},
        {"y": 450.0, "amount": 116110.79},
        {"y": 550.0, "amount": 116060.79},
    ]

    def section_for_y(y: float) -> str:
        chosen = cur
        for s in sections:
            if s["y"] <= y:
                chosen = s["header"]
            else:
                break
        return chosen

    def date_for_y(y: float) -> str:
        return "21 Sep"

    def label_to_date(label: str) -> str:
        return "2022-09-21" if label else ""

    bf = BankStatementParser._hsbc_v2_bf_opening_by_section(
        sections, amounts, balances, section_for_y, date_for_y, label_to_date
    )
    assert bf[cur]["balance"] == 5033.70
    assert bf[sav]["balance"] == 116110.79
    assert bf[cur]["deposit"] is None and bf[cur]["withdrawal"] is None
    assert bf[sav]["deposit"] is None and bf[sav]["withdrawal"] is None
    assert "B/F BALANCE" in bf[cur]["description"]


def test_hsbc_v2_bf_first_section_bf_between_title_and_column_header():
    """Real HSBC layout: account title y < column-header y; B/F balance sits between."""
    cur = "HSBC Business Direct HKD Current"
    sections = [{"y": 265.65, "header": cur}]
    amounts = [{"y": 321.45, "col": "Cr", "amount": 276.0}]
    balances = [{"y": 278.0, "amount": 5033.70}]

    def section_for_y(y: float) -> str:
        return cur

    bf = BankStatementParser._hsbc_v2_bf_opening_by_section(
        sections,
        amounts,
        balances,
        section_for_y,
        lambda _y: "21 Sep",
        lambda label: "2022-09-21" if label else "",
        header_y=285.45,
    )
    assert bf[cur]["balance"] == 5033.70


def test_hsbc_v2_bf_when_opening_balance_above_section_title():
    """B/F line y can be above the 'HKD Savings' title on continuation pages."""
    cur = "HSBC Business Direct HKD Current"
    sav = "HSBC Business Direct HKD Savings"
    sections = [
        {"y": 100.0, "header": cur},
        {"y": 380.0, "header": sav},
    ]
    amounts = [
        {"y": 200.0, "col": "Cr", "amount": 276.0},
        {"y": 500.0, "col": "Dr", "amount": 50.0},
    ]
    balances = [
        {"y": 150.0, "amount": 5033.70},
        {"y": 250.0, "amount": 5309.70},
        {"y": 340.0, "amount": 116110.79},
        {"y": 550.0, "amount": 116060.79},
    ]

    def section_for_y(y: float) -> str:
        chosen = cur
        for s in sections:
            if s["y"] <= y:
                chosen = s["header"]
            else:
                break
        return chosen

    bf = BankStatementParser._hsbc_v2_bf_opening_by_section(
        sections,
        amounts,
        balances,
        section_for_y,
        lambda _y: "21 Sep",
        lambda label: "2022-09-21" if label else "",
        header_y=80.0,
    )
    assert bf[sav]["balance"] == 116110.79


def test_hsbc_v2_bf_relaxed_band_when_bf_above_y_lo():
    """B/F balance y may fall just below min(title, baseline)-2; relax using header_y."""
    cur = "HSBC Business Direct HKD Current"
    sections = [{"y": 265.65, "header": cur}]
    amounts = [{"y": 321.45, "col": "Cr", "amount": 444.0}]
    balances = [{"y": 255.0, "amount": 7959.35}]

    def section_for_y(y: float) -> str:
        return cur

    bf = BankStatementParser._hsbc_v2_bf_opening_by_section(
        sections,
        amounts,
        balances,
        section_for_y,
        lambda _y: "21 Nov",
        lambda label: "2022-11-21" if label else "",
        header_y=285.45,
    )
    assert bf[cur]["balance"] == 7959.35


def test_extract_transactions_keeps_bf_when_filter_disabled():
    parser = BankStatementParser()
    ai = {
        "transactions": [
            {
                "description": "B/F BALANCE 承前轉結",
                "balance": 100.0,
                "account_type": "HSBC Business Direct HKD Current",
                "currency": "HKD",
            },
            {
                "description": "PAYMENT",
                "balance": 150.0,
                "deposit": 50.0,
                "account_type": "HSBC Business Direct HKD Current",
                "currency": "HKD",
            },
        ]
    }
    out = parser._extract_transactions_from_ai_response(
        ai, filter_balance_anchor_rows=False
    )
    assert len(out) == 2
    assert any(
        str(t.get("description", "")).find("B/F") >= 0 for t in out
    )
