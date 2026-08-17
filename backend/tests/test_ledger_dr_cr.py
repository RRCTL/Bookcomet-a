"""Unit tests for ledger Dr/Cr normalization and GL side preference."""

from types import SimpleNamespace

from app.api.reconciliation import _normalize_ledger_amount_dr_cr
from app.services.gl_journal_service import _ledger_dr_credit


def test_normalize_explicit_cr_from_ap_module_edit():
    """AP manual edit to Credit side must win over AP→Dr default."""
    mag, side = _normalize_ledger_amount_dr_cr(
        889.0, module="AP", transaction_type="AP", explicit_dr_cr="Cr"
    )
    assert mag == 889.0
    assert side == "Cr"


def test_normalize_explicit_dr_cr_wins():
    mag, side = _normalize_ledger_amount_dr_cr(
        -200.0, module="AP", transaction_type="AP", explicit_dr_cr="Cr"
    )
    assert mag == 200.0
    assert side == "Cr"


def test_normalize_default_ap_dr_ar_cr():
    mag_ap, side_ap = _normalize_ledger_amount_dr_cr(
        100.0, module="AP", transaction_type="AP", explicit_dr_cr=None
    )
    mag_ar, side_ar = _normalize_ledger_amount_dr_cr(
        100.0, module="AR", transaction_type="AR", explicit_dr_cr=None
    )
    assert mag_ap == 100.0 and side_ap == "Dr"
    assert mag_ar == 100.0 and side_ar == "Cr"


def test_normalize_negative_amount_flips_default():
    mag, side = _normalize_ledger_amount_dr_cr(
        -50.0, module="AP", transaction_type="AP", explicit_dr_cr=None
    )
    assert mag == 50.0
    assert side == "Cr"


def test_gl_stored_cr_overrides_ap_heuristic():
    lt = SimpleNamespace(dr_cr="Cr", doc_type="ap")
    assert _ledger_dr_credit(lt, 100.0) == (0.0, 100.0)


def test_gl_stored_dr():
    lt = SimpleNamespace(dr_cr="Dr", doc_type="receipt")
    assert _ledger_dr_credit(lt, 50.0) == (50.0, 0.0)


def test_gl_null_dr_cr_keeps_heuristic():
    ap = SimpleNamespace(dr_cr=None, doc_type="ap")
    ar = SimpleNamespace(dr_cr=None, doc_type="receipt")
    assert _ledger_dr_credit(ap, 100.0) == (100.0, 0.0)
    assert _ledger_dr_credit(ar, 100.0) == (0.0, 100.0)
