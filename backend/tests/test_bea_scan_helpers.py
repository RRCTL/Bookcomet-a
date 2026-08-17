"""Unit tests for BEA statement prescan helpers and post-filters."""
from __future__ import annotations

from app.bank_prompts.bea import BEA_AR_MANAGER_PROMPT_PREFIX
from app.services.bank_statement_parser import BankStatementParser


def test_bea_is_total_transaction_summary():
    assert BankStatementParser._bea_is_total_transaction_summary("Total Transaction Amount")
    assert BankStatementParser._bea_is_total_transaction_summary("TOTAL TRANSACTION AMOUNT")
    assert BankStatementParser._bea_is_total_transaction_summary("\u4ea4\u6613\u7e3d\u91d1\u984d")
    assert BankStatementParser._bea_is_total_transaction_summary(
        "\u4ea4\u6613\u7b46\u6578 NO.OF TRANSACTION"
    )
    assert BankStatementParser._bea_is_total_transaction_summary("No. Of Transaction")
    assert BankStatementParser._bea_is_total_transaction_summary("NO.OF TRANSACTION")
    assert not BankStatementParser._bea_is_total_transaction_summary("ATM CASH WITHDRAWAL")
    assert not BankStatementParser._bea_is_total_transaction_summary("")


def test_bea_normalise_account_header_statement_savings():
    h = BankStatementParser._bea_normalise_account_header(
        "STATEMENT SAVINGS ACCOUNT (STATEMENT)"
    )
    assert "SAVINGS" in h.upper()


def test_bea_portfolio_only_line_vs_activity_title():
    assert BankStatementParser._bea_is_portfolio_only_line("PORTFOLIO SUMMARY")
    assert BankStatementParser._bea_is_portfolio_only_line("ACCOUNT PORTFOLIO")
    assert not BankStatementParser._bea_is_portfolio_only_line(
        "STATEMENT SAVINGS ACCOUNT (STATEMENT)"
    )


def test_bea_cover_like_portfolio_page():
    assert BankStatementParser._bea_is_cover_like_portfolio_page(
        "PORTFOLIO SUMMARY\nSome text"
    )
    assert not BankStatementParser._bea_is_cover_like_portfolio_page(
        "PORTFOLIO SUMMARY\nHKD CURRENT ACCOUNT\nmore"
    )


def test_bea_find_information_footer_y():
    class _P:
        def get_text(self, mode: str):
            if mode == "blocks":
                return [
                    (50.0, 750.0, 200.0, 770.0, "INFORMATION\nlegal", 0, 0),
                ]
            return ""

    y = BankStatementParser._bea_find_information_footer_y(_P())
    assert y == 750.0


def test_bea_prescan_drops_amounts_below_information(monkeypatch):
    """Amounts at/after INFORMATION heading y are removed from prescan."""
    words = []
    # Header row y ~ 100
    for label, cx in [
        ("\u5b58\u5165", 400.0),
        ("\u652f\u51fa", 480.0),
        ("\u7d50\u9918", 550.0),
    ]:
        words.append((cx - 20, 100.0, cx + 20, 112.0, label))
    # Data amount in table
    words.append((400.0, 200.0, 440.0, 212.0, "1,000.00"))
    # Footer amount (should be dropped)
    words.append((400.0, 760.0, 440.0, 772.0, "99.00"))

    class _Page:
        rect = type("R", (), {"width": 595.0, "height": 842.0})()

        def get_text(self, mode: str):
            if mode == "words":
                return words
            if mode == "text":
                return "PORTFOLIO\n"
            if mode == "blocks":
                return [
                    (50.0, 720.0, 120.0, 735.0, "INFORMATION", 0, 0),
                ]
            return None

    def _no_cover(_t: str) -> bool:
        return False

    monkeypatch.setattr(
        BankStatementParser,
        "_bea_is_cover_like_portfolio_page",
        staticmethod(_no_cover),
    )
    ps = BankStatementParser._bea_prescan_amounts(_Page())
    assert not ps["no_table"]
    assert len(ps["amounts"]) == 1
    assert ps["amounts"][0]["amount"] == 1000.0


def test_bea_post_filter_drops_subtotal_and_forward_fills_account():
    txns = [
        {"description": "Payee A", "account_type": "HKD CURRENT", "_page": 1},
        {
            "description": "Total Transaction Amount",
            "account_type": "",
            "_page": 1,
        },
        {"description": "Payee B", "account_type": "", "_page": 1},
    ]
    out = BankStatementParser._bea_post_filter_transactions(txns)
    assert len(out) == 2
    assert out[1]["account_type"] == "HKD CURRENT"


def test_bea_ar_manager_prompt_export():
    assert "BEA" in BEA_AR_MANAGER_PROMPT_PREFIX
    assert "BOOKKEEPER_DRAFT_JSON" in BEA_AR_MANAGER_PROMPT_PREFIX
