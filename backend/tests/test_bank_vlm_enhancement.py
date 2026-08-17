"""BANK VLM: document type routing and TSV row parsing."""

from app.api.ocr import _document_type_for_enhancement
from app.services.ai_post_processor import AiPostProcessor


def test_document_type_for_bank_page():
    assert (
        _document_type_for_enhancement("BANK", "HSBC transaction list", page_num=2)
        == "bank_statement_page"
    )


def test_document_type_for_bank_single():
    assert _document_type_for_enhancement("BANK", "random text") == "bank_statement"


def test_document_type_non_bank_unchanged():
    assert _document_type_for_enhancement("AP", "invoice 123") == "invoice"


def test_parse_tsv_rows_preserve_keeps_deposit_withdrawal_columns():
    raw = (
        "No.\t憑證號\t類型\t存入\t提取\t原幣結餘\t幣別\t日期\t付款人\t收款人\t銀行\t備註\t信心度\n"
        "1\tREF1\t轉帳\t1000\t\t5000\tHKD\t2025-01-02\tA\tB\tHSBC\tmemo\t0.9\n"
    )
    rows = AiPostProcessor._parse_tsv_rows_preserve(raw)
    assert len(rows) == 1
    assert rows[0]["存入"] == "1000"
    assert rows[0]["提取"] == ""
    assert rows[0]["日期"] == "2025-01-02"
