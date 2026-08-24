"""Build, validate, and post GL journals from reconciliation groups (HKD v1, deterministic)."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.gl_journal import GlJournal, GlJournalLine, GlJournalStatus
from app.models.reconciliation import (
    ChartOfAccountEntry,
    DecisionType,
    MatchType,
    ReconciliationGroup,
    ReconciliationMatch,
)
from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus

logger = logging.getLogger(__name__)

_DEFAULT_BANK_CODE = "1010"
_DEFAULT_AR_CODE = "1100"
_DEFAULT_AP_CODE = "2100"
_SUSPENSE_CODE = "1999"
_SUSPENSE_NAME_EN = "Reconciliation Suspense"

SOURCE_MODULE_APPROVE = "module_approve"
SOURCE_MANUAL = "manual"
SOURCE_RECON_MATCH = "recon_match"


def suspense_code_for_company() -> str:
    import os
    return (os.getenv("RECON_GL_SUSPENSE_CODE", _SUSPENSE_CODE) or _SUSPENSE_CODE).strip()


def _ensure_suspense_row(db: Session, company_id: str) -> None:
    code = suspense_code_for_company()
    exists = (
        db.query(ChartOfAccountEntry)
        .filter(ChartOfAccountEntry.company_id == company_id, ChartOfAccountEntry.code == code)
        .first()
    )
    if exists:
        return
    db.add(
        ChartOfAccountEntry(
            id=str(uuid.uuid4()),
            company_id=company_id,
            code=code,
            name_en=_SUSPENSE_NAME_EN,
            name_zh="對賬暫記",
            category_type="asset",
            allowed_modes=["AR", "AP", "BANK"],
            is_default=False,
        )
    )
    db.flush()


def _coa_exists(db: Session, company_id: str, code: str) -> bool:
    return (
        db.query(ChartOfAccountEntry)
        .filter(ChartOfAccountEntry.company_id == company_id, ChartOfAccountEntry.code == code)
        .first()
        is not None
    )


def _resolve_code(db: Session, company_id: str, category: str | None, default_code: str) -> str:
    """If account_category looks like a 4-digit code and exists in CoA, use it; else default."""
    if not category:
        return default_code
    cat = str(category).strip()
    if re.match(r"^\d{4}$", cat) and _coa_exists(db, company_id, cat):
        return cat
    return default_code


# Must stay in sync with OCR → bulk_ledger_doc_type ("AR"/"AP") and legacy doc_type strings.
_AP_DOC_TYPES = frozenset({"payment", "invoice", "bill", "ap", "cheque_payment"})


def _ledger_is_ap(txn: LedgerTransaction) -> bool:
    """Payables / expense-side OCR rows: invert GL debit/credit vs AR convention."""
    dt = (txn.doc_type or "").lower().strip()
    return dt in _AP_DOC_TYPES


def _ledger_default_code(txn: LedgerTransaction) -> str:
    return _DEFAULT_AP_CODE if _ledger_is_ap(txn) else _DEFAULT_AR_CODE


def _ledger_dr_credit(lt: LedgerTransaction, amt: float) -> tuple[float, float]:
    """Prefer stored dr_cr; else AR: amount ≥ 0 → credit; AP: opposite."""
    a = float(amt or 0)
    abs_amt = abs(a)
    if abs_amt < 1e-12:
        return 0.0, 0.0
    stored = (getattr(lt, "dr_cr", None) or "").strip().capitalize()
    if stored == "Dr":
        return abs_amt, 0.0
    if stored == "Cr":
        return 0.0, abs_amt
    positive_means_credit = a >= 0
    if _ledger_is_ap(lt):
        positive_means_credit = not positive_means_credit
    if positive_means_credit:
        return 0.0, abs_amt
    return abs_amt, 0.0


def _ledger_line_dict(db: Session, company_id: str, lt: LedgerTransaction) -> dict[str, Any]:
    """One GL line dict from a ledger transaction (debit/credit per AR vs AP)."""
    code = _resolve_code(db, company_id, lt.account_category, _ledger_default_code(lt))
    debit, credit = _ledger_dr_credit(lt, float(lt.amount or 0))
    memo = (lt.reference or lt.doc_id or "")[:200]
    return {
        "account_code": code,
        "debit": debit,
        "credit": credit,
        "memo": memo,
        "bank_txn_id": None,
        "ledger_txn_id": lt.id,
    }


def _ledger_clearing_line_dict(
    db: Session,
    company_id: str,
    lt: LedgerTransaction,
    *,
    bank_net_debit: bool,
) -> dict[str, Any]:
    """Ledger leg for bank↔ledger RECON clear: opposite of bank net; CoA code unchanged."""
    code = _resolve_code(db, company_id, lt.account_category, _ledger_default_code(lt))
    abs_amt = abs(float(lt.amount or 0))
    memo = (lt.reference or lt.doc_id or "")[:200]
    if bank_net_debit:
        debit, credit = 0.0, abs_amt
    else:
        debit, credit = abs_amt, 0.0
    return {
        "account_code": code,
        "debit": debit,
        "credit": credit,
        "memo": memo,
        "bank_txn_id": None,
        "ledger_txn_id": lt.id,
    }


def _next_voucher_no(db: Session, company_id: str) -> str:
    n = (
        db.query(GlJournal)
        .filter(GlJournal.company_id == company_id)
        .count()
    )
    return f"GL-{n + 1:06d}"


def _group_journal_date(bank_txns: list[BankTransaction], ledger_txns: list[LedgerTransaction]) -> datetime:
    for t in bank_txns:
        if t.bank_date:
            return t.bank_date
    for t in ledger_txns:
        if t.book_date:
            return t.book_date
    return datetime.now(timezone.utc)


_HKD_ALIASES = frozenset(
    {
        "HKD",
        "HK$",
        "HK",
        "HONG KONG DOLLAR",
        "HONGKONG DOLLAR",
        "HONG KONG DOLLARS",
        "港元",
        "港幣",
        "港币",
    }
)


def _norm_currency(raw: str | None) -> str:
    """Normalize currency labels so bank OCR aliases (e.g. 港元) match ISO HKD."""
    c = (raw or "").strip()
    if not c:
        return "HKD"
    if c in _HKD_ALIASES or c.upper() in _HKD_ALIASES:
        return "HKD"
    return c.upper()


def resolve_txns_currency(
    bank_txns: list[BankTransaction],
    ledger_txns: list[LedgerTransaction],
) -> str:
    """One journal = one currency. Raises if linked txns disagree."""
    currencies: set[str] = set()
    for t in bank_txns:
        currencies.add(_norm_currency(t.currency))
    for t in ledger_txns:
        currencies.add(_norm_currency(t.currency))
    if not currencies:
        return "HKD"
    if len(currencies) > 1:
        raise ValueError(f"Mixed currencies in one journal: {sorted(currencies)}")
    return next(iter(currencies))


def _status_str(raw: Any) -> str:
    if raw is None:
        return TransactionStatus.UNRECONCILED.value
    return raw.value if hasattr(raw, "value") else str(raw)


def _delete_journal_cascade(db: Session, journal: GlJournal) -> None:
    db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal.id).delete(synchronize_session=False)
    db.delete(journal)


def _module_journals_for_txn_ids(
    db: Session,
    company_id: str,
    bank_txn_ids: set[str],
    ledger_txn_ids: set[str],
) -> list[GlJournal]:
    """Journals with source=module_approve linked to any of the given txn ids (via lines)."""
    if not bank_txn_ids and not ledger_txn_ids:
        return []
    conds = []
    if bank_txn_ids:
        conds.append(GlJournalLine.bank_txn_id.in_(bank_txn_ids))
    if ledger_txn_ids:
        conds.append(GlJournalLine.ledger_txn_id.in_(ledger_txn_ids))
    line_filter = conds[0] if len(conds) == 1 else or_(*conds)
    jids = {
        r[0]
        for r in (
            db.query(GlJournalLine.journal_id)
            .filter(line_filter)
            .distinct()
            .all()
        )
        if r[0]
    }
    if not jids:
        return []
    return (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.id.in_(jids),
            GlJournal.source == SOURCE_MODULE_APPROVE,
            GlJournal.status != GlJournalStatus.VOIDED,
        )
        .all()
    )


def assert_module_journals_mergeable(
    db: Session,
    company_id: str,
    bank_txn_ids: set[str] | list[str],
    ledger_txn_ids: set[str] | list[str],
) -> None:
    """Raise if any linked module journal is posted (must unpost before match merge)."""
    journals = _module_journals_for_txn_ids(
        db, company_id, set(bank_txn_ids or []), set(ledger_txn_ids or [])
    )
    posted = [j for j in journals if j.status == GlJournalStatus.POSTED]
    if posted:
        vos = ", ".join(sorted({(j.voucher_no or j.id) for j in posted}))
        raise ValueError(
            f"Unpost module GL journal(s) before matching: {vos}"
        )


def assert_group_has_no_posted_journal(db: Session, company_id: str, group_id: str) -> None:
    posted = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.POSTED,
        )
        .first()
    )
    if posted:
        raise ValueError(
            f"Unpost the GL journal ({posted.voucher_no or posted.id}) before cancelling this match."
        )


def _derive_recon_status(db: Session, company_id: str, journal: GlJournal) -> str:
    lines = db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal.id).all()
    statuses: list[str] = []
    for ln in lines:
        if ln.bank_txn_id:
            bt = (
                db.query(BankTransaction)
                .filter(BankTransaction.id == ln.bank_txn_id, BankTransaction.company_id == company_id)
                .first()
            )
            if bt:
                statuses.append(_status_str(bt.status))
        if ln.ledger_txn_id:
            lt = (
                db.query(LedgerTransaction)
                .filter(LedgerTransaction.id == ln.ledger_txn_id, LedgerTransaction.company_id == company_id)
                .first()
            )
            if lt:
                statuses.append(_status_str(lt.status))
    if not statuses:
        return TransactionStatus.UNRECONCILED.value
    if all(s == TransactionStatus.MATCHED.value for s in statuses):
        return TransactionStatus.MATCHED.value
    if any(s == TransactionStatus.PARTIAL.value for s in statuses):
        return TransactionStatus.PARTIAL.value
    if any(s == TransactionStatus.MATCHED.value for s in statuses):
        return TransactionStatus.PARTIAL.value
    return TransactionStatus.UNRECONCILED.value


def _derive_module_tag(db: Session, company_id: str, journal: GlJournal) -> str | None:
    src = (journal.source or "").strip().lower()
    if src == SOURCE_MANUAL:
        return "MANUAL"
    if src == SOURCE_RECON_MATCH or journal.reconciliation_group_id:
        return "RECON"
    lines = db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal.id).all()
    for ln in lines:
        if ln.ledger_txn_id:
            lt = (
                db.query(LedgerTransaction)
                .filter(LedgerTransaction.id == ln.ledger_txn_id, LedgerTransaction.company_id == company_id)
                .first()
            )
            if lt and lt.module:
                return str(lt.module).strip().upper()
            if lt:
                return "AP" if _ledger_is_ap(lt) else "AR"
        if ln.bank_txn_id:
            return "BANK"
    return None


def _post_allowed(db: Session, company_id: str, journal: GlJournal) -> None:
    """Manual anytime; module/recon journals only when linked txns are matched (or journal has a group)."""
    src = (journal.source or "").strip().lower()
    if src == SOURCE_MANUAL:
        return
    if journal.reconciliation_group_id:
        recon = _derive_recon_status(db, company_id, journal)
        if recon in (TransactionStatus.MATCHED.value, TransactionStatus.PARTIAL.value):
            return
        # Group journals are created at match time; allow post even if status race.
        return
    if src == SOURCE_MODULE_APPROVE:
        raise ValueError("Cannot post unreconciled module journal until it is matched in RECON")
    raise ValueError("Cannot post this journal until it is linked to a reconciliation group")


def _build_lines_for_group(
    db: Session,
    company_id: str,
    group: ReconciliationGroup,
    bank_txns: list[BankTransaction],
    ledger_txns: list[LedgerTransaction],
) -> list[dict[str, Any]]:
    """Return list of line dicts: account_code, debit, credit, memo, bank_txn_id, ledger_txn_id."""
    lines: list[dict[str, Any]] = []

    banks_only = bank_txns and not ledger_txns
    ledgers_only = ledger_txns and not bank_txns

    # GL-only: cash line + auto-1999 (offset code stays on bank.account_category until approve).
    if (group.match_cardinality or "").strip() == "GL:1" and banks_only and len(bank_txns) == 1:
        bt = bank_txns[0]
        amt = float(bt.amount or 0)
        # Group stores abs total; recover magnitude if bank amount was wiped after match.
        if abs(amt) < 1e-9:
            fallback = abs(float(getattr(group, "total_bank_amount", 0) or 0))
            if fallback > 1e-9:
                amt = fallback
        memo = (bt.reference or "")[:200]
        if amt >= 0:
            lines.append(
                {
                    "account_code": _DEFAULT_BANK_CODE,
                    "debit": abs(amt),
                    "credit": 0.0,
                    "memo": memo,
                    "bank_txn_id": bt.id,
                    "ledger_txn_id": None,
                }
            )
        else:
            lines.append(
                {
                    "account_code": _DEFAULT_BANK_CODE,
                    "debit": 0.0,
                    "credit": abs(amt),
                    "memo": memo,
                    "bank_txn_id": bt.id,
                    "ledger_txn_id": None,
                }
            )
        return lines

    if banks_only and len(bank_txns) >= 2:
        # Same-side bank: inter-bank transfer
        for i, bt in enumerate(bank_txns):
            code = _resolve_code(db, company_id, bt.account_category, _DEFAULT_BANK_CODE)
            amt = float(bt.amount or 0)
            memo = (bt.reference or "")[:200]
            if amt >= 0:
                lines.append({"account_code": code, "debit": abs(amt), "credit": 0.0, "memo": memo, "bank_txn_id": bt.id, "ledger_txn_id": None})
            else:
                lines.append({"account_code": code, "debit": 0.0, "credit": abs(amt), "memo": memo, "bank_txn_id": bt.id, "ledger_txn_id": None})
        return lines

    if ledgers_only and len(ledger_txns) >= 2:
        for lt in ledger_txns:
            lines.append(_ledger_line_dict(db, company_id, lt))
        return lines

    # Cross-mode bank ↔ ledger (or one-sided fall-through for single bank/ledger)
    for bt in bank_txns:
        code = _resolve_code(db, company_id, bt.account_category, _DEFAULT_BANK_CODE)
        amt = float(bt.amount or 0)
        memo = (bt.reference or "")[:200]
        if amt >= 0:
            lines.append({"account_code": code, "debit": abs(amt), "credit": 0.0, "memo": memo, "bank_txn_id": bt.id, "ledger_txn_id": None})
        else:
            lines.append({"account_code": code, "debit": 0.0, "credit": abs(amt), "memo": memo, "bank_txn_id": bt.id, "ledger_txn_id": None})

    if bank_txns and ledger_txns:
        # Equal-match clearing: ledger opposite bank net so Dr = Cr without suspense.
        bank_net = 0.0
        for ln in lines:
            bank_net += float(ln.get("debit") or 0) - float(ln.get("credit") or 0)
        if abs(bank_net) < 1e-9:
            bank_net_debit = float(bank_txns[0].amount or 0) >= 0
        else:
            bank_net_debit = bank_net > 0
        for lt in ledger_txns:
            lines.append(
                _ledger_clearing_line_dict(db, company_id, lt, bank_net_debit=bank_net_debit)
            )
    else:
        for lt in ledger_txns:
            lines.append(_ledger_line_dict(db, company_id, lt))

    return lines


def _totals(lines: list[GlJournalLine] | list[dict[str, Any]]) -> tuple[float, float]:
    td = tc = 0.0
    for ln in lines:
        if isinstance(ln, dict):
            td += float(ln.get("debit") or 0)
            tc += float(ln.get("credit") or 0)
        else:
            td += float(ln.debit or 0)
            tc += float(ln.credit or 0)
    return td, tc


def _populate_journal_lines_from_line_data(
    db: Session,
    journal: GlJournal,
    line_data: list[dict[str, Any]],
) -> None:
    """Insert GlJournalLine rows from builder output and add suspense balancing line if needed."""
    suspense = suspense_code_for_company()
    journal.balancing_account_code = None
    for i, ld in enumerate(line_data, start=1):
        db.add(
            GlJournalLine(
                id=str(uuid.uuid4()),
                journal_id=journal.id,
                line_no=i,
                account_code=ld["account_code"],
                debit=ld["debit"],
                credit=ld["credit"],
                memo=ld.get("memo"),
                bank_txn_id=ld.get("bank_txn_id"),
                ledger_txn_id=ld.get("ledger_txn_id"),
            )
        )
    td, tc = _totals(line_data)
    if abs(td - tc) > 0.001:
        bal = abs(td - tc)
        if td > tc:
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=journal.id,
                    line_no=len(line_data) + 1,
                    account_code=suspense,
                    debit=0.0,
                    credit=bal,
                    memo="[系統] 待選擇平衡科目",
                    bank_txn_id=None,
                    ledger_txn_id=None,
                )
            )
        else:
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=journal.id,
                    line_no=len(line_data) + 1,
                    account_code=suspense,
                    debit=bal,
                    credit=0.0,
                    memo="[系統] 待選擇平衡科目",
                    bank_txn_id=None,
                    ledger_txn_id=None,
                )
            )
        journal.balancing_account_code = suspense


def prune_extra_drafts_for_group(db: Session, company_id: str, group_id: str) -> int:
    """If more than one DRAFT exists for a group, keep the newest and delete the rest.

    Prevents orphan duplicate drafts (e.g. double reverse clicks, old clients) from
    stacking and confusing the UI / list_posted.
    """
    drafts = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.DRAFT,
        )
        .order_by(GlJournal.created_at.desc(), GlJournal.id.desc())
        .all()
    )
    if len(drafts) <= 1:
        return 0
    n = 0
    for j in drafts[1:]:
        db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).delete()
        db.delete(j)
        n += 1
    if n:
        db.commit()
    return n


def ensure_draft_for_group(db: Session, company_id: str, group_id: str) -> GlJournal:
    """Create or return existing DRAFT journal for this reconciliation group."""
    _ensure_suspense_row(db, company_id)

    group = (
        db.query(ReconciliationGroup)
        .filter(ReconciliationGroup.id == group_id, ReconciliationGroup.company_id == company_id)
        .first()
    )
    if not group:
        raise ValueError("Reconciliation group not found")

    prune_extra_drafts_for_group(db, company_id, group_id)

    # Prefer an open draft (e.g. reversal-in-progress) over a posted journal.
    draft_row = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.DRAFT,
        )
        .order_by(GlJournal.created_at.desc())
        .first()
    )
    if draft_row:
        if (
            draft_row.reversal_of_journal_id is None
            and _primary_draft_stale_for_group(db, company_id, group_id, draft_row.id)
        ):
            try:
                return rebuild_primary_draft_for_group(db, company_id, group_id)
            except ValueError:
                pass
            draft_row = (
                db.query(GlJournal)
                .filter(
                    GlJournal.company_id == company_id,
                    GlJournal.reconciliation_group_id == group_id,
                    GlJournal.status == GlJournalStatus.DRAFT,
                )
                .order_by(GlJournal.created_at.desc())
                .first()
            )
        if draft_row:
            return draft_row

    posted_row = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.POSTED,
        )
        .order_by(GlJournal.posted_at.desc())
        .first()
    )
    if posted_row:
        return posted_row

    matches = (
        db.query(ReconciliationMatch)
        .filter(ReconciliationMatch.group_id == group_id, ReconciliationMatch.company_id == company_id)
        .all()
    )
    bank_ids = {m.bank_txn_id for m in matches if m.bank_txn_id}
    ledger_ids = {m.ledger_txn_id for m in matches if m.ledger_txn_id}

    bank_txns = db.query(BankTransaction).filter(BankTransaction.id.in_(bank_ids)).all() if bank_ids else []
    ledger_txns = db.query(LedgerTransaction).filter(LedgerTransaction.id.in_(ledger_ids)).all() if ledger_ids else []

    line_data = _build_lines_for_group(db, company_id, group, bank_txns, ledger_txns)
    jd = _group_journal_date(bank_txns, ledger_txns)
    currency = resolve_txns_currency(bank_txns, ledger_txns)

    j = GlJournal(
        id=str(uuid.uuid4()),
        company_id=company_id,
        reconciliation_group_id=group_id,
        status=GlJournalStatus.DRAFT,
        journal_date=jd,
        currency=currency,
        voucher_no=_next_voucher_no(db, company_id),
        narration=f"RECON group {group_id[:8]}",
        source=SOURCE_RECON_MATCH,
        balancing_account_code=None,
    )
    db.add(j)
    db.flush()

    _populate_journal_lines_from_line_data(db, j, line_data)

    db.commit()
    db.refresh(j)
    return j


def ensure_draft_for_txn(
    db: Session,
    company_id: str,
    *,
    bank_txn_id: str | None = None,
    ledger_txn_id: str | None = None,
) -> GlJournal:
    """Idempotent draft journal for one approved module txn (source=module_approve)."""
    bid = (bank_txn_id or "").strip() or None
    lid = (ledger_txn_id or "").strip() or None
    if bool(bid) == bool(lid):
        raise ValueError("Provide exactly one of bank_txn_id or ledger_txn_id")

    _ensure_suspense_row(db, company_id)

    bank_txns: list[BankTransaction] = []
    ledger_txns: list[LedgerTransaction] = []
    if bid:
        bt = (
            db.query(BankTransaction)
            .filter(BankTransaction.id == bid, BankTransaction.company_id == company_id)
            .first()
        )
        if not bt:
            raise ValueError("Bank transaction not found")
        bank_txns = [bt]
    else:
        lt = (
            db.query(LedgerTransaction)
            .filter(LedgerTransaction.id == lid, LedgerTransaction.company_id == company_id)
            .first()
        )
        if not lt:
            raise ValueError("Ledger transaction not found")
        ledger_txns = [lt]

    existing = _module_journals_for_txn_ids(
        db,
        company_id,
        {bid} if bid else set(),
        {lid} if lid else set(),
    )
    drafts = [
        j
        for j in existing
        if j.status == GlJournalStatus.DRAFT and not j.reconciliation_group_id
    ]
    if drafts:
        drafts.sort(key=lambda j: (j.created_at or datetime.min, j.id), reverse=True)
        return drafts[0]
    posted = [j for j in existing if j.status == GlJournalStatus.POSTED]
    if posted:
        return posted[0]

    # Synthetic group object for line builder (id unused for banks/ledgers-only paths).
    class _Synth:
        id = "module-txn"
        match_cardinality = None
        total_bank_amount = 0

    line_data = _build_lines_for_group(db, company_id, _Synth(), bank_txns, ledger_txns)
    jd = _group_journal_date(bank_txns, ledger_txns)
    currency = resolve_txns_currency(bank_txns, ledger_txns)
    ref = ""
    if bank_txns:
        ref = (bank_txns[0].reference or bank_txns[0].id)[:80]
    else:
        ref = (ledger_txns[0].reference or ledger_txns[0].doc_id or ledger_txns[0].id)[:80]

    j = GlJournal(
        id=str(uuid.uuid4()),
        company_id=company_id,
        reconciliation_group_id=None,
        status=GlJournalStatus.DRAFT,
        journal_date=jd,
        currency=currency,
        voucher_no=_next_voucher_no(db, company_id),
        narration=f"Module draft — {ref}",
        source=SOURCE_MODULE_APPROVE,
        balancing_account_code=None,
    )
    db.add(j)
    db.flush()
    _populate_journal_lines_from_line_data(db, j, line_data)
    db.commit()
    db.refresh(j)
    return j


def create_manual_journal(
    db: Session,
    company_id: str,
    *,
    journal_date: datetime,
    currency: str,
    narration: str | None,
    voucher_no: str | None,
    lines: list[dict[str, Any]],
) -> GlJournal:
    """User-typed adjusting journal (source=manual). Must be balanced; one currency."""
    _ensure_suspense_row(db, company_id)
    cur = _norm_currency(currency)
    if not lines or len(lines) < 2:
        raise ValueError("Manual journal requires at least 2 lines")
    cleaned: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        code = str(ln.get("account_code") or "").strip()
        if not code:
            raise ValueError(f"Line {i + 1}: account_code is required")
        if not _coa_exists(db, company_id, code):
            raise ValueError(f"Line {i + 1}: unknown account code {code}")
        dr = float(ln.get("debit") or 0)
        cr = float(ln.get("credit") or 0)
        if dr < 0 or cr < 0:
            raise ValueError(f"Line {i + 1}: debit and credit must be non-negative")
        if dr > 0.0001 and cr > 0.0001:
            raise ValueError(f"Line {i + 1}: cannot have both debit and credit")
        if dr < 0.0001 and cr < 0.0001:
            raise ValueError(f"Line {i + 1}: debit or credit is required")
        memo = ln.get("memo")
        cleaned.append(
            {
                "account_code": code,
                "debit": dr,
                "credit": cr,
                "memo": (str(memo).strip()[:500] if memo is not None else None),
                "bank_txn_id": None,
                "ledger_txn_id": None,
            }
        )
    td, tc = _totals(cleaned)
    if abs(td - tc) > 0.01:
        raise ValueError(f"Journal not balanced: debit {td:.2f} credit {tc:.2f}")

    vn = (voucher_no or "").strip() or _next_voucher_no(db, company_id)
    j = GlJournal(
        id=str(uuid.uuid4()),
        company_id=company_id,
        reconciliation_group_id=None,
        status=GlJournalStatus.DRAFT,
        journal_date=journal_date,
        currency=cur,
        voucher_no=vn,
        narration=(narration or "").strip() or None,
        source=SOURCE_MANUAL,
        balancing_account_code=None,
    )
    db.add(j)
    db.flush()
    for i, ld in enumerate(cleaned, start=1):
        db.add(
            GlJournalLine(
                id=str(uuid.uuid4()),
                journal_id=j.id,
                line_no=i,
                account_code=ld["account_code"],
                debit=ld["debit"],
                credit=ld["credit"],
                memo=ld.get("memo"),
                bank_txn_id=None,
                ledger_txn_id=None,
            )
        )
    db.commit()
    db.refresh(j)
    return j


def merge_module_drafts_into_group(db: Session, company_id: str, group_id: str) -> GlJournal:
    """Draft-only collapse: delete module_approve drafts for group txns, ensure group journal."""
    matches = (
        db.query(ReconciliationMatch)
        .filter(ReconciliationMatch.group_id == group_id, ReconciliationMatch.company_id == company_id)
        .all()
    )
    bank_ids = {m.bank_txn_id for m in matches if m.bank_txn_id}
    ledger_ids = {m.ledger_txn_id for m in matches if m.ledger_txn_id}

    bank_txns = db.query(BankTransaction).filter(BankTransaction.id.in_(bank_ids)).all() if bank_ids else []
    ledger_txns = (
        db.query(LedgerTransaction).filter(LedgerTransaction.id.in_(ledger_ids)).all() if ledger_ids else []
    )
    resolve_txns_currency(bank_txns, ledger_txns)

    assert_module_journals_mergeable(db, company_id, bank_ids, ledger_ids)

    module_js = _module_journals_for_txn_ids(db, company_id, bank_ids, ledger_ids)
    for j in module_js:
        if j.status == GlJournalStatus.DRAFT:
            _delete_journal_cascade(db, j)
    if module_js:
        db.commit()

    return ensure_draft_for_group(db, company_id, group_id)


def list_journals(
    db: Session,
    company_id: str,
    *,
    status: str | None = None,
    currency: str | None = None,
    source: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = db.query(GlJournal).filter(
        GlJournal.company_id == company_id,
        GlJournal.status != GlJournalStatus.VOIDED,
    )
    if status:
        st = status.strip().lower()
        try:
            q = q.filter(GlJournal.status == GlJournalStatus(st))
        except ValueError as exc:
            raise ValueError(f"Invalid status: {status}") from exc
    if currency:
        q = q.filter(GlJournal.currency == _norm_currency(currency))
    if source:
        q = q.filter(GlJournal.source == source.strip().lower())
    if date_from is not None:
        q = q.filter(GlJournal.journal_date >= date_from)
    if date_to is not None:
        q = q.filter(GlJournal.journal_date <= date_to)
    rows = (
        q.order_by(GlJournal.journal_date.desc(), GlJournal.created_at.desc())
        .limit(min(max(limit, 1), 1000))
        .all()
    )
    out: list[dict[str, Any]] = []
    for j in rows:
        d = journal_to_dict(db, j)
        d["recon_status"] = _derive_recon_status(db, company_id, j)
        d["module"] = _derive_module_tag(db, company_id, j)
        d["bank_txn_ids"] = sorted(
            {ln["bank_txn_id"] for ln in d["lines"] if ln.get("bank_txn_id")}
        )
        d["ledger_txn_ids"] = sorted(
            {ln["ledger_txn_id"] for ln in d["lines"] if ln.get("ledger_txn_id")}
        )
        out.append(d)
    return out


def draft_group_ids_with_primary_draft(
    db: Session,
    company_id: str,
    bank_txn_ids: set[str],
    ledger_txn_ids: set[str],
) -> list[str]:
    """Groups that have a non-reversal DRAFT journal and a match touching any of the given txn ids."""
    if not bank_txn_ids and not ledger_txn_ids:
        return []
    conds = []
    if bank_txn_ids:
        conds.append(ReconciliationMatch.bank_txn_id.in_(bank_txn_ids))
    if ledger_txn_ids:
        conds.append(ReconciliationMatch.ledger_txn_id.in_(ledger_txn_ids))
    match_filter = conds[0] if len(conds) == 1 else or_(*conds)

    g_rows = (
        db.query(ReconciliationMatch.group_id)
        .filter(ReconciliationMatch.company_id == company_id, match_filter)
        .distinct()
        .all()
    )
    group_ids = {r[0] for r in g_rows if r[0]}
    if not group_ids:
        return []

    d_rows = (
        db.query(GlJournal.reconciliation_group_id)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id.in_(group_ids),
            GlJournal.status == GlJournalStatus.DRAFT,
            GlJournal.reversal_of_journal_id.is_(None),
        )
        .distinct()
        .all()
    )
    return [r[0] for r in d_rows if r[0]]


def rebuild_primary_draft_for_group(db: Session, company_id: str, group_id: str) -> GlJournal:
    """Recompute lines for the primary (non-reversal) DRAFT journal from current txn account_category."""
    _ensure_suspense_row(db, company_id)
    prune_extra_drafts_for_group(db, company_id, group_id)

    j = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.DRAFT,
            GlJournal.reversal_of_journal_id.is_(None),
        )
        .order_by(GlJournal.created_at.desc())
        .first()
    )
    if not j:
        raise ValueError("No primary draft journal for this group")

    group = (
        db.query(ReconciliationGroup)
        .filter(ReconciliationGroup.id == group_id, ReconciliationGroup.company_id == company_id)
        .first()
    )
    if not group:
        raise ValueError("Reconciliation group not found")

    matches = (
        db.query(ReconciliationMatch)
        .filter(ReconciliationMatch.group_id == group_id, ReconciliationMatch.company_id == company_id)
        .all()
    )
    bank_ids = {m.bank_txn_id for m in matches if m.bank_txn_id}
    ledger_ids = {m.ledger_txn_id for m in matches if m.ledger_txn_id}

    bank_txns = db.query(BankTransaction).filter(BankTransaction.id.in_(bank_ids)).all() if bank_ids else []
    ledger_txns = db.query(LedgerTransaction).filter(LedgerTransaction.id.in_(ledger_ids)).all() if ledger_ids else []

    line_data = _build_lines_for_group(db, company_id, group, bank_txns, ledger_txns)

    db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).delete(synchronize_session=False)
    db.flush()
    _populate_journal_lines_from_line_data(db, j, line_data)

    db.commit()
    db.refresh(j)
    return j


def rebuild_module_approve_draft_for_txn(
    db: Session,
    company_id: str,
    *,
    bank_txn_id: str | None = None,
    ledger_txn_id: str | None = None,
) -> GlJournal | None:
    """Rebuild DRAFT source=module_approve lines from current txn account_category.

    Returns the rebuilt journal, or None when no eligible draft exists.
    Posted module journals are left untouched.
    """
    bid = (bank_txn_id or "").strip() or None
    lid = (ledger_txn_id or "").strip() or None
    if bool(bid) == bool(lid):
        raise ValueError("Provide exactly one of bank_txn_id or ledger_txn_id")

    _ensure_suspense_row(db, company_id)

    bank_txns: list[BankTransaction] = []
    ledger_txns: list[LedgerTransaction] = []
    if bid:
        bt = (
            db.query(BankTransaction)
            .filter(BankTransaction.id == bid, BankTransaction.company_id == company_id)
            .first()
        )
        if not bt:
            raise ValueError("Bank transaction not found")
        bank_txns = [bt]
    else:
        lt = (
            db.query(LedgerTransaction)
            .filter(LedgerTransaction.id == lid, LedgerTransaction.company_id == company_id)
            .first()
        )
        if not lt:
            raise ValueError("Ledger transaction not found")
        ledger_txns = [lt]

    existing = _module_journals_for_txn_ids(
        db,
        company_id,
        {bid} if bid else set(),
        {lid} if lid else set(),
    )
    drafts = [
        j
        for j in existing
        if j.status == GlJournalStatus.DRAFT and not j.reconciliation_group_id
    ]
    if not drafts:
        return None
    drafts.sort(key=lambda j: (j.created_at or datetime.min, j.id), reverse=True)
    j = drafts[0]

    class _Synth:
        id = "module-txn"
        match_cardinality = None
        total_bank_amount = 0

    line_data = _build_lines_for_group(db, company_id, _Synth(), bank_txns, ledger_txns)
    db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).delete(synchronize_session=False)
    db.flush()
    _populate_journal_lines_from_line_data(db, j, line_data)
    j.journal_date = _group_journal_date(bank_txns, ledger_txns)
    db.commit()
    db.refresh(j)
    return j


def rebuild_module_approve_drafts_for_txns(
    db: Session,
    company_id: str,
    bank_txn_ids: set[str],
    ledger_txn_ids: set[str],
) -> list[str]:
    """Rebuild unlocked module_approve DRAFT journals for the given txn ids. Returns journal ids."""
    rebuilt: list[str] = []
    for bid in sorted(bank_txn_ids or ()):
        try:
            j = rebuild_module_approve_draft_for_txn(
                db, company_id, bank_txn_id=bid
            )
        except ValueError:
            continue
        if j:
            rebuilt.append(j.id)
    for lid in sorted(ledger_txn_ids or ()):
        try:
            j = rebuild_module_approve_draft_for_txn(
                db, company_id, ledger_txn_id=lid
            )
        except ValueError:
            continue
        if j:
            rebuilt.append(j.id)
    return rebuilt


def posted_gl_locked_txn_ids(
    db: Session,
    company_id: str,
    bank_txn_ids: set[str] | None = None,
    ledger_txn_ids: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Return (locked_bank_ids, locked_ledger_ids) whose match group has a POSTED primary voucher."""
    bank_req = {t for t in (bank_txn_ids or set()) if t}
    led_req = {t for t in (ledger_txn_ids or set()) if t}
    if not bank_req and not led_req:
        return set(), set()

    conds = []
    if bank_req:
        conds.append(ReconciliationMatch.bank_txn_id.in_(bank_req))
    if led_req:
        conds.append(ReconciliationMatch.ledger_txn_id.in_(led_req))
    match_filter = conds[0] if len(conds) == 1 else or_(*conds)

    matches = (
        db.query(ReconciliationMatch)
        .filter(ReconciliationMatch.company_id == company_id, match_filter)
        .all()
    )
    group_ids = {m.group_id for m in matches if m.group_id}
    if not group_ids:
        return set(), set()

    posted_rows = (
        db.query(GlJournal.reconciliation_group_id)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id.in_(group_ids),
            GlJournal.status == GlJournalStatus.POSTED,
            GlJournal.reversal_of_journal_id.is_(None),
        )
        .distinct()
        .all()
    )
    posted_groups = {r[0] for r in posted_rows if r[0]}
    if not posted_groups:
        return set(), set()

    locked_bank: set[str] = set()
    locked_ledger: set[str] = set()
    for m in matches:
        if m.group_id not in posted_groups:
            continue
        if m.bank_txn_id and m.bank_txn_id in bank_req:
            locked_bank.add(m.bank_txn_id)
        if m.ledger_txn_id and m.ledger_txn_id in led_req:
            locked_ledger.add(m.ledger_txn_id)
    return locked_bank, locked_ledger


def partition_account_category_updates_by_posted_gl(
    db: Session,
    company_id: str,
    tuples: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Split updates into (allowed, blocked_by_posted_gl)."""
    if not tuples:
        return [], []
    bank_ids = {t[1] for t in tuples if t[0] == "bank" and t[1]}
    led_ids = {t[1] for t in tuples if t[0] == "ledger" and t[1]}
    locked_bank, locked_ledger = posted_gl_locked_txn_ids(
        db, company_id, bank_ids, led_ids
    )
    allowed: list[tuple[str, str, str]] = []
    blocked: list[tuple[str, str, str]] = []
    for src, tid, cat in tuples:
        if src == "bank" and tid in locked_bank:
            blocked.append((src, tid, cat))
        elif src == "ledger" and tid in locked_ledger:
            blocked.append((src, tid, cat))
        else:
            allowed.append((src, tid, cat))
    return allowed, blocked


def assert_account_category_updates_not_blocked_by_posted_gl(
    db: Session,
    company_id: str,
    tuples: list[tuple[str, str, str]],
    *,
    blocked_action: str = "change account code",
) -> None:
    """Reject OCR/category (or other txn field) writes when the match group's primary voucher is POSTED."""
    if not tuples:
        return
    bank_req = {t[1] for t in tuples if t[0] == "bank" and t[1]}
    led_req = {t[1] for t in tuples if t[0] == "ledger" and t[1]}
    if not bank_req and not led_req:
        return

    conds = []
    if bank_req:
        conds.append(ReconciliationMatch.bank_txn_id.in_(bank_req))
    if led_req:
        conds.append(ReconciliationMatch.ledger_txn_id.in_(led_req))
    match_filter = conds[0] if len(conds) == 1 else or_(*conds)

    matches = (
        db.query(ReconciliationMatch)
        .filter(ReconciliationMatch.company_id == company_id, match_filter)
        .all()
    )
    group_ids = {m.group_id for m in matches if m.group_id}
    if not group_ids:
        return

    posted_rows = (
        db.query(GlJournal.reconciliation_group_id)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id.in_(group_ids),
            GlJournal.status == GlJournalStatus.POSTED,
            GlJournal.reversal_of_journal_id.is_(None),
        )
        .distinct()
        .all()
    )
    posted_groups = {r[0] for r in posted_rows if r[0]}
    if not posted_groups:
        return

    locked_bank: set[str] = set()
    locked_ledger: set[str] = set()
    for m in matches:
        if m.group_id not in posted_groups:
            continue
        if m.bank_txn_id:
            locked_bank.add(m.bank_txn_id)
        if m.ledger_txn_id:
            locked_ledger.add(m.ledger_txn_id)

    blocked: list[str] = []
    for src, tid, _cat in tuples:
        if not tid:
            continue
        if src == "bank" and tid in locked_bank:
            blocked.append(f"bank:{tid}")
        elif src == "ledger" and tid in locked_ledger:
            blocked.append(f"ledger:{tid}")

    if blocked:
        preview = ", ".join(blocked[:8])
        more = f" (+{len(blocked) - 8} more)" if len(blocked) > 8 else ""
        raise ValueError(
            f"Cannot {blocked_action}: GL is already posted for this match ({preview}{more}). "
            "Unpost the journal in RECON to edit."
        )


def bulk_set_transaction_account_categories(
    db: Session,
    company_id: str,
    updates: list[tuple[str, str, str]],
) -> tuple[int, set[str], set[str]]:
    """Apply (source, txn_id, account_category) tuples. Returns (count, bank_ids, ledger_ids) touched."""
    n = 0
    bank_touched: set[str] = set()
    ledger_touched: set[str] = set()
    for source, txn_id, category in updates:
        if not txn_id:
            continue
        cat = str(category).strip() if category is not None else ""
        if source == "bank":
            row = (
                db.query(BankTransaction)
                .filter(BankTransaction.id == txn_id, BankTransaction.company_id == company_id)
                .first()
            )
            if row:
                row.account_category = cat or None
                bank_touched.add(txn_id)
                n += 1
        elif source == "ledger":
            row = (
                db.query(LedgerTransaction)
                .filter(LedgerTransaction.id == txn_id, LedgerTransaction.company_id == company_id)
                .first()
            )
            if row:
                row.account_category = cat or None
                ledger_touched.add(txn_id)
                n += 1
    if n:
        db.commit()
    return n, bank_touched, ledger_touched


def bulk_set_ledger_doc_types(
    db: Session,
    company_id: str,
    updates: list[tuple[str, str]],
) -> tuple[int, set[str]]:
    """Set LedgerTransaction.doc_type from OCR AR/AP. Only AR and AP are accepted (stored uppercase)."""
    n = 0
    ledger_touched: set[str] = set()
    for txn_id, raw_dt in updates:
        tid = (txn_id or "").strip()
        if not tid:
            continue
        norm = (raw_dt or "").strip().upper()
        if norm not in ("AR", "AP"):
            continue
        row = (
            db.query(LedgerTransaction)
            .filter(LedgerTransaction.id == tid, LedgerTransaction.company_id == company_id)
            .first()
        )
        if not row:
            continue
        prev = (row.doc_type or "").strip().upper()
        if prev == norm:
            continue
        row.doc_type = norm
        ledger_touched.add(tid)
        n += 1
    if n:
        db.commit()
    return n, ledger_touched


def _journal_linked_txn_ids(
    db: Session, journal_id: str
) -> tuple[set[str], set[str]]:
    lines = db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal_id).all()
    bank_ids = {ln.bank_txn_id for ln in lines if ln.bank_txn_id}
    ledger_ids = {ln.ledger_txn_id for ln in lines if ln.ledger_txn_id}
    return bank_ids, ledger_ids


def _linked_txns_are_reconciled(db: Session, company_id: str, journal: GlJournal) -> bool:
    """True if journal is a RECON group voucher or any linked txn is matched/partial."""
    if journal.reconciliation_group_id:
        return True
    bank_ids, ledger_ids = _journal_linked_txn_ids(db, journal.id)
    blocked = {TransactionStatus.MATCHED.value, TransactionStatus.PARTIAL.value}
    if bank_ids:
        for bt in db.query(BankTransaction).filter(
            BankTransaction.company_id == company_id,
            BankTransaction.id.in_(bank_ids),
        ):
            if _status_str(bt.status) in blocked:
                return True
    if ledger_ids:
        for lt in db.query(LedgerTransaction).filter(
            LedgerTransaction.company_id == company_id,
            LedgerTransaction.id.in_(ledger_ids),
        ):
            if _status_str(lt.status) in blocked:
                return True
    return False


def _bank_amount_from_line(debit: float, credit: float) -> float:
    """Inverse of bank line build: amount ≥ 0 → debit, amount < 0 → credit."""
    dr = float(debit or 0)
    cr = float(credit or 0)
    if dr > 0.0005:
        return dr
    if cr > 0.0005:
        return -cr
    return 0.0


def _ledger_amount_and_dr_cr_from_line(
    lt: LedgerTransaction, debit: float, credit: float
) -> tuple[float, str | None]:
    """Map journal Dr/Cr back to ledger amount (+ optional dr_cr)."""
    dr = float(debit or 0)
    cr = float(credit or 0)
    if dr > 0.0005 and cr > 0.0005:
        raise ValueError("Ledger-linked line cannot have both debit and credit")
    if dr <= 0.0005 and cr <= 0.0005:
        return 0.0, getattr(lt, "dr_cr", None)

    # Prefer explicit Dr/Cr when the row already uses it (or always set from line side).
    if dr > 0.0005:
        return abs(dr), "Dr"
    return abs(cr), "Cr"


def sync_draft_journal_lines_to_transactions(db: Session, company_id: str, journal_id: str) -> dict[str, Any]:
    """Push draft journal lines to linked bank/ledger txns.

    Unreconciled module drafts: account + amount + date.
    Reconciled / RECON-group drafts: account_category only (amounts stay locked).
    """
    j = (
        db.query(GlJournal)
        .filter(GlJournal.id == journal_id, GlJournal.company_id == company_id)
        .first()
    )
    if not j:
        raise ValueError("Journal not found")
    if j.status != GlJournalStatus.DRAFT:
        raise ValueError("Only draft journals can sync to transactions")

    rewrite_fields = not _linked_txns_are_reconciled(db, company_id, j)

    lines = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == j.id)
        .order_by(GlJournalLine.line_no)
        .all()
    )
    bank_map: dict[str, str] = {}
    ledger_map: dict[str, str] = {}
    bank_updates: list[dict[str, Any]] = []
    ledger_updates: list[dict[str, Any]] = []
    jdate = j.journal_date

    for ln in lines:
        code = (ln.account_code or "").strip()
        if ln.bank_txn_id:
            bt = (
                db.query(BankTransaction)
                .filter(BankTransaction.id == ln.bank_txn_id, BankTransaction.company_id == company_id)
                .first()
            )
            if not bt:
                continue
            if code:
                bt.account_category = code
                bank_map[ln.bank_txn_id] = code
            if rewrite_fields:
                bt.amount = _bank_amount_from_line(float(ln.debit or 0), float(ln.credit or 0))
                if jdate is not None:
                    bt.bank_date = jdate
            bank_updates.append(
                {
                    "id": bt.id,
                    "account_category": bt.account_category,
                    "amount": float(bt.amount or 0),
                    "date": bt.bank_date.isoformat() if bt.bank_date else None,
                    "fields_rewritten": rewrite_fields,
                }
            )
        if ln.ledger_txn_id:
            lt = (
                db.query(LedgerTransaction)
                .filter(LedgerTransaction.id == ln.ledger_txn_id, LedgerTransaction.company_id == company_id)
                .first()
            )
            if not lt:
                continue
            if code:
                lt.account_category = code
                ledger_map[ln.ledger_txn_id] = code
            if rewrite_fields:
                amt, dr_cr = _ledger_amount_and_dr_cr_from_line(
                    lt, float(ln.debit or 0), float(ln.credit or 0)
                )
                lt.amount = amt
                if dr_cr is not None:
                    lt.dr_cr = dr_cr
                if jdate is not None:
                    lt.book_date = jdate
            ledger_updates.append(
                {
                    "id": lt.id,
                    "account_category": lt.account_category,
                    "amount": float(lt.amount or 0),
                    "dr_cr": lt.dr_cr,
                    "date": lt.book_date.isoformat() if lt.book_date else None,
                    "fields_rewritten": rewrite_fields,
                }
            )
    db.commit()
    return {
        "bank": bank_map,
        "ledger": ledger_map,
        "bank_updates": bank_updates,
        "ledger_updates": ledger_updates,
        "module_fields_rewritten": rewrite_fields,
    }


def journal_to_dict(db: Session, journal: GlJournal) -> dict[str, Any]:
    lines = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == journal.id)
        .order_by(GlJournalLine.line_no)
        .all()
    )
    td, tc = _totals(lines)
    balanced = abs(td - tc) < 0.01
    return {
        "id": journal.id,
        "company_id": journal.company_id,
        "reconciliation_group_id": journal.reconciliation_group_id,
        "status": journal.status.value if hasattr(journal.status, "value") else journal.status,
        "journal_date": journal.journal_date.isoformat() if journal.journal_date else None,
        "currency": journal.currency,
        "voucher_no": journal.voucher_no,
        "narration": journal.narration,
        "balancing_account_code": journal.balancing_account_code,
        "reversal_of_journal_id": journal.reversal_of_journal_id,
        "source": journal.source,
        "created_at": journal.created_at.isoformat() if journal.created_at else None,
        "posted_at": journal.posted_at.isoformat() if journal.posted_at else None,
        "posted_by": journal.posted_by,
        "lines": [
            {
                "id": ln.id,
                "line_no": ln.line_no,
                "account_code": ln.account_code,
                "debit": float(ln.debit or 0),
                "credit": float(ln.credit or 0),
                "memo": ln.memo,
                "bank_txn_id": ln.bank_txn_id,
                "ledger_txn_id": ln.ledger_txn_id,
            }
            for ln in lines
        ],
        "total_debit": td,
        "total_credit": tc,
        "balanced": balanced,
    }


def _renumber_journal_lines(db: Session, journal_id: str) -> None:
    lines = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == journal_id)
        .order_by(GlJournalLine.line_no)
        .all()
    )
    for i, ln in enumerate(lines, start=1):
        ln.line_no = i


def _apply_line_amount_patch(patch: dict[str, Any], ln: GlJournalLine) -> None:
    """Enforce single-sided lines: only debit or credit may be non-zero."""
    dr = float(ln.debit or 0)
    cr = float(ln.credit or 0)
    if "debit" in patch:
        dr = float(patch["debit"] or 0)
    if "credit" in patch:
        cr = float(patch["credit"] or 0)
    if dr > 0.0001 and cr > 0.0001:
        raise ValueError("Each line may have only debit or credit, not both")
    if dr > 0.0001:
        ln.debit = dr
        ln.credit = 0.0
    elif cr > 0.0001:
        ln.credit = cr
        ln.debit = 0.0
    else:
        ln.debit = 0.0
        ln.credit = 0.0


def _virtual_dr_cr_after_patch(dr: float, cr: float, patch: dict[str, Any]) -> tuple[float, float]:
    """Apply debit/credit patch rules to numeric dr/cr (single-sided line)."""
    if "debit" in patch:
        dr = float(patch["debit"] or 0)
    if "credit" in patch:
        cr = float(patch["credit"] or 0)
    if dr > 0.0001 and cr > 0.0001:
        raise ValueError("Each line may have only debit or credit, not both")
    if dr > 0.0001:
        return dr, 0.0
    if cr > 0.0001:
        return 0.0, cr
    return 0.0, 0.0


def preview_journal_totals_after_patch(
    db: Session,
    journal_id: str,
    deleted_line_ids: list[str] | None,
    lines_patch: list[dict[str, Any]] | None,
) -> tuple[float, float]:
    """
    Simulate DELETE + line patches without committing. Returns (total_debit, total_credit).
    Raises ValueError on invalid single-line debit/credit.
    """
    lines_orm = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == journal_id)
        .order_by(GlJournalLine.line_no)
        .all()
    )
    del_set = {str(x).strip() for x in (deleted_line_ids or []) if x}
    virtual: list[dict[str, Any]] = []
    for ln in lines_orm:
        if ln.id in del_set:
            continue
        virtual.append({
            "debit": float(ln.debit or 0),
            "credit": float(ln.credit or 0),
            "_id": ln.id,
        })
    lid_to_idx = {v["_id"]: i for i, v in enumerate(virtual)}

    if lines_patch:
        for patch in lines_patch:
            lid = patch.get("id") or None
            if lid and lid in lid_to_idx:
                idx = lid_to_idx[lid]
                v = virtual[idx]
                dr, cr = _virtual_dr_cr_after_patch(float(v["debit"]), float(v["credit"]), patch)
                v["debit"], v["credit"] = dr, cr
            elif not lid and patch.get("account_code"):
                dr, cr = _virtual_dr_cr_after_patch(0.0, 0.0, patch)
                virtual.append({"debit": dr, "credit": cr, "_id": f"__new_{len(virtual)}__"})

    return _totals(virtual)


def update_draft(
    db: Session,
    company_id: str,
    journal_id: str,
    journal_date: datetime | None,
    lines_patch: list[dict[str, Any]] | None,
    balancing_account_code: str | None,
    deleted_line_ids: list[str] | None = None,
) -> GlJournal:
    j = (
        db.query(GlJournal)
        .filter(GlJournal.id == journal_id, GlJournal.company_id == company_id)
        .first()
    )
    if not j:
        raise ValueError("Journal not found")
    if j.status != GlJournalStatus.DRAFT:
        raise ValueError("Only draft journals can be edited")

    if journal_date:
        j.journal_date = journal_date

    if balancing_account_code is not None:
        j.balancing_account_code = balancing_account_code or None
        # Replace suspense placeholder lines with user's balancing account
        susp = suspense_code_for_company()
        if balancing_account_code and _coa_exists(db, company_id, balancing_account_code):
            for ln in db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).all():
                if ln.account_code == susp and ln.memo and "待選擇平衡科目" in (ln.memo or ""):
                    ln.account_code = balancing_account_code

    if deleted_line_ids:
        for did in deleted_line_ids:
            if not did:
                continue
            ln = (
                db.query(GlJournalLine)
                .filter(GlJournalLine.id == did, GlJournalLine.journal_id == j.id)
                .first()
            )
            if ln:
                db.delete(ln)
        db.flush()

    if lines_patch is not None:
        running_max = (
            db.query(func.max(GlJournalLine.line_no)).filter(GlJournalLine.journal_id == j.id).scalar() or 0
        )
        for patch in lines_patch:
            lid = patch.get("id") or None
            if lid:
                ln = (
                    db.query(GlJournalLine)
                    .filter(GlJournalLine.id == lid, GlJournalLine.journal_id == j.id)
                    .first()
                )
                if not ln:
                    continue
                if "account_code" in patch and patch["account_code"]:
                    code = str(patch["account_code"]).strip()
                    if _coa_exists(db, company_id, code):
                        ln.account_code = code
                if "debit" in patch or "credit" in patch:
                    _apply_line_amount_patch(patch, ln)
                if "memo" in patch:
                    ln.memo = patch["memo"]
                continue

            # New line (no id)
            code = str(patch.get("account_code") or "").strip()
            if not code or not _coa_exists(db, company_id, code):
                raise ValueError("New line requires a valid account_code")
            running_max += 1
            new_ln = GlJournalLine(
                id=str(uuid.uuid4()),
                journal_id=j.id,
                line_no=running_max,
                account_code=code,
                debit=0.0,
                credit=0.0,
                memo=patch.get("memo"),
            )
            if "debit" in patch or "credit" in patch:
                _apply_line_amount_patch(patch, new_ln)
            db.add(new_ln)

    _renumber_journal_lines(db, j.id)

    db.commit()
    db.refresh(j)
    return j


def _coa_entry(db: Session, company_id: str, code: str) -> ChartOfAccountEntry | None:
    return (
        db.query(ChartOfAccountEntry)
        .filter(ChartOfAccountEntry.company_id == company_id, ChartOfAccountEntry.code == code)
        .first()
    )


def _is_bank_cash_coa(db: Session, company_id: str, code: str) -> bool:
    """True when offset CoA is a bank/cash asset (inter-bank transfer path)."""
    if not code or code == suspense_code_for_company():
        return False
    entry = _coa_entry(db, company_id, code)
    if not entry:
        return False
    modes = entry.allowed_modes or []
    if isinstance(modes, str):
        modes = [modes]
    return entry.category_type == "asset" and "BANK" in [str(m).upper() for m in modes]


def _module_for_coa(entry: ChartOfAccountEntry) -> str:
    modes = entry.allowed_modes or []
    if isinstance(modes, str):
        modes = [modes]
    modes_u = {str(m).upper() for m in modes}
    if "AP" in modes_u and "AR" not in modes_u:
        return "AP"
    if "AR" in modes_u and "AP" not in modes_u:
        return "AR"
    ct = (entry.category_type or "").lower()
    if ct in ("expense", "bank_fee", "interest_paid", "cogs", "liability"):
        return "AP"
    if ct in ("revenue", "other_income"):
        return "AR"
    if "AP" in modes_u:
        return "AP"
    return "AR"


def finalize_gl_only_before_post(
    db: Session,
    company_id: str,
    journal: GlJournal,
    *,
    confirm_bank_create: bool = False,
) -> None:
    """Replace 1999 with bank GL offset; create AP/AR ledger or counterpart bank txn."""
    if not journal.reconciliation_group_id:
        return
    group = (
        db.query(ReconciliationGroup)
        .filter(
            ReconciliationGroup.id == journal.reconciliation_group_id,
            ReconciliationGroup.company_id == company_id,
        )
        .first()
    )
    if not group or (group.match_cardinality or "").strip() != "GL:1":
        return

    matches = (
        db.query(ReconciliationMatch)
        .filter(
            ReconciliationMatch.group_id == group.id,
            ReconciliationMatch.company_id == company_id,
        )
        .all()
    )
    bank_ids = [m.bank_txn_id for m in matches if m.bank_txn_id]
    if len(bank_ids) != 1:
        raise ValueError("GL-only group must have exactly one bank transaction")
    bt = (
        db.query(BankTransaction)
        .filter(BankTransaction.id == bank_ids[0], BankTransaction.company_id == company_id)
        .first()
    )
    if not bt:
        raise ValueError("Bank transaction not found for GL-only group")
    offset = (bt.account_category or "").strip()
    if not offset or not _coa_exists(db, company_id, offset):
        raise ValueError("Bank GL offset code is missing or unknown")
    entry = _coa_entry(db, company_id, offset)
    if not entry:
        raise ValueError(f"Unknown GL offset code: {offset}")

    amt = abs(float(bt.amount or 0))
    susp = suspense_code_for_company()

    if _is_bank_cash_coa(db, company_id, offset):
        if not confirm_bank_create:
            raise ValueError(
                "CONFIRM_CREATE_BANK: Approving this GL-only match will create a new "
                f"bank transaction for CoA {offset}. Confirm to continue."
            )
        # Opposite amount: this bank Cr → new bank Dr (and vice versa).
        new_amt = -float(bt.amount or 0)
        new_bt = BankTransaction(
            id=str(uuid.uuid4()),
            company_id=company_id,
            account_id=offset,
            bank_date=bt.bank_date,
            amount=new_amt,
            currency=bt.currency or "HKD",
            description_raw=f"Bank GL match from {bt.reference or bt.id}",
            description_norm=f"bank gl match from {bt.reference or bt.id}".lower(),
            account_category=offset,
            reference=f"GL-{bt.reference or bt.id[:8]}",
            import_batch_id=bt.import_batch_id,
            status=TransactionStatus.MATCHED,
        )
        db.add(new_bt)
        db.add(
            ReconciliationMatch(
                id=str(uuid.uuid4()),
                company_id=company_id,
                trace_id=group.trace_id,
                bank_txn_id=new_bt.id,
                ledger_txn_id=None,
                group_id=group.id,
                match_type=MatchType.MANUAL,
                score=1.0,
                decision=DecisionType.MANUAL,
                created_by=group.created_by,
            )
        )
        group.match_cardinality = "1:1"
        # Rebuild: source cash (1010) + counterpart bank CoA (no suspense).
        db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal.id).delete(
            synchronize_session=False
        )
        src_amt = float(bt.amount or 0)
        line_data = []
        if src_amt >= 0:
            line_data.append(
                {
                    "account_code": _DEFAULT_BANK_CODE,
                    "debit": abs(src_amt),
                    "credit": 0.0,
                    "memo": (bt.reference or "")[:200],
                    "bank_txn_id": bt.id,
                    "ledger_txn_id": None,
                }
            )
            line_data.append(
                {
                    "account_code": offset,
                    "debit": 0.0,
                    "credit": abs(src_amt),
                    "memo": (new_bt.reference or "")[:200],
                    "bank_txn_id": new_bt.id,
                    "ledger_txn_id": None,
                }
            )
        else:
            line_data.append(
                {
                    "account_code": _DEFAULT_BANK_CODE,
                    "debit": 0.0,
                    "credit": abs(src_amt),
                    "memo": (bt.reference or "")[:200],
                    "bank_txn_id": bt.id,
                    "ledger_txn_id": None,
                }
            )
            line_data.append(
                {
                    "account_code": offset,
                    "debit": abs(src_amt),
                    "credit": 0.0,
                    "memo": (new_bt.reference or "")[:200],
                    "bank_txn_id": new_bt.id,
                    "ledger_txn_id": None,
                }
            )
        _populate_journal_lines_from_line_data(db, journal, line_data)
        journal.balancing_account_code = None
        db.flush()
        return
    else:
        module = _module_for_coa(entry)
        # Withdrawal (negative bank): AP expense Dr; deposit: AR income Cr.
        if module == "AP":
            dr_cr = "Dr" if float(bt.amount or 0) < 0 else "Cr"
        else:
            dr_cr = "Cr" if float(bt.amount or 0) >= 0 else "Dr"
        lt = LedgerTransaction(
            id=str(uuid.uuid4()),
            company_id=company_id,
            module=module,
            doc_type=module,
            doc_id=f"GL-{bt.reference or bt.id[:8]}",
            book_date=bt.bank_date,
            amount=amt,
            currency=bt.currency or "HKD",
            counterparty=bt.description_raw or "",
            account_category=offset,
            reference=f"Bank GL match · {bt.reference or bt.id[:8]}",
            import_batch_id=bt.import_batch_id,
            dr_cr=dr_cr,
            status=TransactionStatus.MATCHED,
        )
        db.add(lt)
        db.add(
            ReconciliationMatch(
                id=str(uuid.uuid4()),
                company_id=company_id,
                trace_id=group.trace_id,
                bank_txn_id=None,
                ledger_txn_id=lt.id,
                group_id=group.id,
                match_type=MatchType.MANUAL,
                score=1.0,
                decision=DecisionType.MANUAL,
                created_by=group.created_by,
            )
        )
        group.total_ledger_amount = amt
        group.difference = round(float(group.total_bank_amount or 0) - amt, 2)
        group.match_cardinality = "1:1"

    # Replace suspense line with offset CoA (linked to created ledger txn).
    lines = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == journal.id)
        .order_by(GlJournalLine.line_no)
        .all()
    )
    replaced = False
    for ln in lines:
        if ln.account_code == susp:
            ln.account_code = offset
            ln.memo = (bt.reference or "Bank GL match")[:200]
            ln.ledger_txn_id = lt.id
            replaced = True
    if not replaced:
        # No suspense line — add offset opposite cash.
        cash = next((ln for ln in lines if ln.bank_txn_id == bt.id), None)
        if cash and float(cash.debit or 0) > 0:
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=journal.id,
                    line_no=len(lines) + 1,
                    account_code=offset,
                    debit=0.0,
                    credit=float(cash.debit or 0),
                    memo=(bt.reference or "")[:200],
                    bank_txn_id=None,
                    ledger_txn_id=lt.id,
                )
            )
        elif cash:
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=journal.id,
                    line_no=len(lines) + 1,
                    account_code=offset,
                    debit=float(cash.credit or 0),
                    credit=0.0,
                    memo=(bt.reference or "")[:200],
                    bank_txn_id=None,
                    ledger_txn_id=lt.id,
                )
            )
    journal.balancing_account_code = None
    db.flush()


def post_journal(
    db: Session,
    company_id: str,
    journal_id: str,
    user_id: str,
    *,
    confirm_bank_create: bool = False,
) -> GlJournal:
    j = (
        db.query(GlJournal)
        .filter(GlJournal.id == journal_id, GlJournal.company_id == company_id)
        .first()
    )
    if not j:
        raise ValueError("Journal not found")
    if j.status != GlJournalStatus.DRAFT:
        raise ValueError("Journal already posted or voided")

    finalize_gl_only_before_post(
        db, company_id, j, confirm_bank_create=confirm_bank_create
    )
    db.refresh(j)

    lines = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == j.id)
        .order_by(GlJournalLine.line_no)
        .all()
    )
    td, tc = _totals(lines)
    if abs(td - tc) > 0.01:
        raise ValueError(f"Journal not balanced: debit {td:.2f} credit {tc:.2f}")

    susp = suspense_code_for_company()
    for ln in lines:
        if ln.account_code == susp and ln.memo and "待選擇平衡科目" in (ln.memo or "") and not j.balancing_account_code:
            raise ValueError("Select balancing account before posting (clear suspense placeholder)")

    for ln in lines:
        if not _coa_exists(db, company_id, ln.account_code):
            raise ValueError(f"Unknown account code: {ln.account_code}")

    _post_allowed(db, company_id, j)

    j.status = GlJournalStatus.POSTED
    j.posted_at = datetime.now(timezone.utc)
    j.posted_by = user_id
    db.commit()
    db.refresh(j)
    return j


def unpost_journal_to_draft(
    db: Session,
    company_id: str,
    journal_id: str,
    user_id: str,
) -> GlJournal:
    """Revert POSTED → DRAFT for the same voucher (no extra GL document, no suspense reversal).

    Blocks if any other journal (draft or posted) exists with reversal_of_journal_id = this id.
    """
    j = (
        db.query(GlJournal)
        .filter(GlJournal.id == journal_id, GlJournal.company_id == company_id)
        .first()
    )
    if not j:
        raise ValueError("Journal not found")
    if j.status != GlJournalStatus.POSTED:
        raise ValueError("Only posted journals can be unposted to draft")

    dependent = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reversal_of_journal_id == journal_id,
        )
        .first()
    )
    if dependent:
        raise ValueError(
            "Cannot unpost: another voucher reverses this one (draft or posted). "
            "Remove or complete that reversal voucher first."
        )

    j.status = GlJournalStatus.DRAFT
    j.posted_at = None
    j.posted_by = None
    db.commit()
    db.refresh(j)
    logger.info("GL unpost_to_draft journal=%s company=%s user=%s", journal_id, company_id, user_id)
    return j


def create_reversal_draft(
    db: Session,
    company_id: str,
    posted_journal_id: str,
    user_id: str,
) -> GlJournal:
    """Unmatch reversal draft: offset each original line through suspense (HKD v1)."""
    _ensure_suspense_row(db, company_id)
    orig = (
        db.query(GlJournal)
        .filter(GlJournal.id == posted_journal_id, GlJournal.company_id == company_id)
        .first()
    )
    if not orig:
        raise ValueError("Original journal not found")
    if orig.status != GlJournalStatus.POSTED:
        raise ValueError("Can only reverse posted journals")

    # Allow reversing a posted reversal (undo path): same guards as below prevent duplicate
    # posted reversals and reuse one draft per target voucher.

    posted_rev = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reversal_of_journal_id == posted_journal_id,
            GlJournal.status == GlJournalStatus.POSTED,
        )
        .first()
    )
    if posted_rev:
        raise ValueError(
            "A posted reversal already exists for this voucher (only one per voucher). "
            "Refresh: your screen may show an older voucher—use Cancel approval on the latest "
            "posted voucher for this match group, or restart the API if the message is outdated."
        )

    existing_draft = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reversal_of_journal_id == posted_journal_id,
            GlJournal.status == GlJournalStatus.DRAFT,
        )
        .order_by(GlJournal.created_at.desc())
        .first()
    )
    if existing_draft:
        db.refresh(existing_draft)
        return existing_draft

    susp = suspense_code_for_company()
    old_lines = (
        db.query(GlJournalLine)
        .filter(GlJournalLine.journal_id == orig.id)
        .order_by(GlJournalLine.line_no)
        .all()
    )

    rev = GlJournal(
        id=str(uuid.uuid4()),
        company_id=company_id,
        reconciliation_group_id=orig.reconciliation_group_id,
        status=GlJournalStatus.DRAFT,
        journal_date=datetime.now(timezone.utc),
        currency=_norm_currency(orig.currency),
        voucher_no=_next_voucher_no(db, company_id),
        narration=(
            f"Reversal of reversal — was {orig.voucher_no}"
            if orig.reversal_of_journal_id
            else f"Reversal / unmatch — was {orig.voucher_no}"
        ),
        source="recon_unmatch",
        reversal_of_journal_id=orig.id,
        balancing_account_code=None,
    )
    db.add(rev)
    db.flush()

    line_no = 1
    for ln in old_lines:
        if float(ln.debit or 0) > 0.001:
            amt = float(ln.debit)
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=rev.id,
                    line_no=line_no,
                    account_code=ln.account_code,
                    debit=0.0,
                    credit=amt,
                    memo=f"Unmatch rev (was Dr {amt})",
                    bank_txn_id=ln.bank_txn_id,
                    ledger_txn_id=ln.ledger_txn_id,
                )
            )
            line_no += 1
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=rev.id,
                    line_no=line_no,
                    account_code=susp,
                    debit=amt,
                    credit=0.0,
                    memo=f"Suspense offset — {ln.account_code}",
                    bank_txn_id=None,
                    ledger_txn_id=None,
                )
            )
            line_no += 1
        elif float(ln.credit or 0) > 0.001:
            amt = float(ln.credit)
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=rev.id,
                    line_no=line_no,
                    account_code=ln.account_code,
                    debit=amt,
                    credit=0.0,
                    memo=f"Unmatch rev (was Cr {amt})",
                    bank_txn_id=ln.bank_txn_id,
                    ledger_txn_id=ln.ledger_txn_id,
                )
            )
            line_no += 1
            db.add(
                GlJournalLine(
                    id=str(uuid.uuid4()),
                    journal_id=rev.id,
                    line_no=line_no,
                    account_code=susp,
                    debit=0.0,
                    credit=amt,
                    memo=f"Suspense offset — {ln.account_code}",
                    bank_txn_id=None,
                    ledger_txn_id=None,
                )
            )
            line_no += 1

    db.commit()
    db.refresh(rev)
    return rev


def rebuild_drafts_with_stale_gl_txn_refs(
    db: Session,
    company_id: str,
    ledger_ids: set[str],
    bank_ids: set[str],
) -> int:
    """
    Rebuild primary DRAFT journals for groups whose lines still reference a bank or
    ledger txn that is no longer part of that group's ReconciliationMatch rows.

    Fixes duplicate amounts across vouchers when a txn moved to a new group but an
    old group's draft was not rebuilt (e.g. prior supersede only targeted 0:n groups).
    """
    if not ledger_ids and not bank_ids:
        return 0

    dirty_gids: set[str] = set()

    def group_has_ledger_match(gid: str, lid: str) -> bool:
        return (
            db.query(ReconciliationMatch.id)
            .filter(
                ReconciliationMatch.company_id == company_id,
                ReconciliationMatch.group_id == gid,
                ReconciliationMatch.ledger_txn_id == lid,
            )
            .first()
            is not None
        )

    def group_has_bank_match(gid: str, bid: str) -> bool:
        return (
            db.query(ReconciliationMatch.id)
            .filter(
                ReconciliationMatch.company_id == company_id,
                ReconciliationMatch.group_id == gid,
                ReconciliationMatch.bank_txn_id == bid,
            )
            .first()
            is not None
        )

    for lid in ledger_ids:
        if not lid:
            continue
        g_rows = (
            db.query(GlJournal.reconciliation_group_id)
            .join(GlJournalLine, GlJournalLine.journal_id == GlJournal.id)
            .filter(
                GlJournal.company_id == company_id,
                GlJournal.status == GlJournalStatus.DRAFT,
                GlJournal.reversal_of_journal_id.is_(None),
                GlJournal.reconciliation_group_id.isnot(None),
                GlJournalLine.ledger_txn_id == lid,
            )
            .distinct()
            .all()
        )
        for (gid,) in g_rows:
            if gid and not group_has_ledger_match(gid, lid):
                dirty_gids.add(gid)

    for bid in bank_ids:
        if not bid:
            continue
        g_rows = (
            db.query(GlJournal.reconciliation_group_id)
            .join(GlJournalLine, GlJournalLine.journal_id == GlJournal.id)
            .filter(
                GlJournal.company_id == company_id,
                GlJournal.status == GlJournalStatus.DRAFT,
                GlJournal.reversal_of_journal_id.is_(None),
                GlJournal.reconciliation_group_id.isnot(None),
                GlJournalLine.bank_txn_id == bid,
            )
            .distinct()
            .all()
        )
        for (gid,) in g_rows:
            if gid and not group_has_bank_match(gid, bid):
                dirty_gids.add(gid)

    n = 0
    for gid in dirty_gids:
        try:
            rebuild_primary_draft_for_group(db, company_id, gid)
            n += 1
        except ValueError:
            pass
    return n


def delete_draft_for_group(db: Session, company_id: str, group_id: str) -> int:
    """Remove draft journals linked to a group (e.g. on unmatch before post)."""
    drafts = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.DRAFT,
        )
        .all()
    )
    n = 0
    for j in drafts:
        db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).delete()
        db.delete(j)
        n += 1
    if n:
        db.commit()
    return n


def prune_orphan_recon_draft_journals(db: Session, company_id: str) -> int:
    """
    Delete DRAFT journals (and lines) whose reconciliation_group_id points at a
    deleted or missing group. Cleans up stale vouchers after supersede/unmatch races.
    """
    valid = {
        r[0]
        for r in db.query(ReconciliationGroup.id).filter(ReconciliationGroup.company_id == company_id)
    }
    q = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.status == GlJournalStatus.DRAFT,
            GlJournal.reconciliation_group_id.isnot(None),
        )
    )
    if valid:
        q = q.filter(~GlJournal.reconciliation_group_id.in_(valid))
    orphans = q.all()
    n = 0
    for j in orphans:
        db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).delete(synchronize_session=False)
        db.delete(j)
        n += 1
    if n:
        db.commit()
    return n


def list_posted(db: Session, company_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        db.query(GlJournal)
        .filter(GlJournal.company_id == company_id, GlJournal.status == GlJournalStatus.POSTED)
        .order_by(GlJournal.posted_at.desc())
        .limit(limit)
        .all()
    )
    return [journal_to_dict(db, j) for j in rows]


def list_for_report(
    db: Session,
    company_id: str,
    date_from: datetime,
    date_to: datetime,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Draft + posted journals (HKD) with journal_date in [date_from, date_to], for financial reports.
    Excludes voided. Orphan journals (no reconciliation_group_id) are included.
    """
    rows = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.status.in_([GlJournalStatus.DRAFT, GlJournalStatus.POSTED]),
            GlJournal.currency == "HKD",
            GlJournal.journal_date >= date_from,
            GlJournal.journal_date <= date_to,
        )
        .order_by(GlJournal.journal_date.asc(), GlJournal.created_at.asc())
        .limit(min(limit, 1000))
        .all()
    )
    return [journal_to_dict(db, j) for j in rows]


def _primary_draft_misaligned_with_group(
    db: Session, company_id: str, group_id: str, journal_id: str
) -> bool:
    """True if any line points at a bank/ledger txn not in this group's match rows."""
    lines = db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal_id).all()
    for ln in lines:
        lid = (ln.ledger_txn_id or "").strip()
        if lid:
            ok = (
                db.query(ReconciliationMatch.id)
                .filter(
                    ReconciliationMatch.company_id == company_id,
                    ReconciliationMatch.group_id == group_id,
                    ReconciliationMatch.ledger_txn_id == lid,
                )
                .first()
            )
            if not ok:
                return True
        bid = (ln.bank_txn_id or "").strip()
        if bid:
            ok = (
                db.query(ReconciliationMatch.id)
                .filter(
                    ReconciliationMatch.company_id == company_id,
                    ReconciliationMatch.group_id == group_id,
                    ReconciliationMatch.bank_txn_id == bid,
                )
                .first()
            )
            if not ok:
                return True
    return False


def _primary_draft_stale_for_group(
    db: Session, company_id: str, group_id: str, journal_id: str
) -> bool:
    """True when draft must be rebuilt from current group members (empty/zero or misaligned)."""
    if _primary_draft_misaligned_with_group(db, company_id, group_id, journal_id):
        return True

    matches = (
        db.query(ReconciliationMatch)
        .filter(
            ReconciliationMatch.group_id == group_id,
            ReconciliationMatch.company_id == company_id,
        )
        .all()
    )
    bank_ids = {m.bank_txn_id for m in matches if m.bank_txn_id}
    ledger_ids = {m.ledger_txn_id for m in matches if m.ledger_txn_id}
    if not bank_ids and not ledger_ids:
        return False

    lines = db.query(GlJournalLine).filter(GlJournalLine.journal_id == journal_id).all()
    td, tc = _totals(lines)
    has_movement = abs(td) > 0.005 or abs(tc) > 0.005
    if not lines or not has_movement:
        return True

    group = (
        db.query(ReconciliationGroup)
        .filter(ReconciliationGroup.id == group_id, ReconciliationGroup.company_id == company_id)
        .first()
    )
    if group and (group.match_cardinality or "").strip() == "GL:1":
        if not any((ln.bank_txn_id or "").strip() for ln in lines):
            return True
        if abs(float(group.total_bank_amount or 0)) > 0.005 and not has_movement:
            return True
    return False


def get_by_group(db: Session, company_id: str, group_id: str) -> dict[str, Any] | None:
    prune_extra_drafts_for_group(db, company_id, group_id)

    draft = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
            GlJournal.status == GlJournalStatus.DRAFT,
        )
        .order_by(GlJournal.created_at.desc())
        .first()
    )
    if draft:
        if (
            draft.reversal_of_journal_id is None
            and _primary_draft_stale_for_group(db, company_id, group_id, draft.id)
        ):
            try:
                rebuild_primary_draft_for_group(db, company_id, group_id)
            except ValueError:
                pass
            draft = (
                db.query(GlJournal)
                .filter(
                    GlJournal.company_id == company_id,
                    GlJournal.reconciliation_group_id == group_id,
                    GlJournal.status == GlJournalStatus.DRAFT,
                )
                .order_by(GlJournal.created_at.desc())
                .first()
            )
        if draft:
            return journal_to_dict(db, draft)
    j = (
        db.query(GlJournal)
        .filter(
            GlJournal.company_id == company_id,
            GlJournal.reconciliation_group_id == group_id,
        )
        .order_by(GlJournal.created_at.desc())
        .first()
    )
    if not j:
        return None
    return journal_to_dict(db, j)
