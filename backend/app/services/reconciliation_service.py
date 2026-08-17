from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import uuid

from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus
from app.models.reconciliation import ReconciliationGroup, ReconciliationMatch, ReconciliationAudit, MatchType, DecisionType
from app.services.decision_evidence import build_decision_evidence


class ReconciliationEngine:
    """Bank reconciliation matching engine"""
    
    def __init__(self):
        self.rules = [
            self._rule1_exact_match,
            self._rule2_date_proximity,
            self._rule3_amount_description,
            self._rule4_cheque_number,
        ]
    
    async def auto_match(
        self,
        bank_txns: List[BankTransaction],
        ledger_txns: List[LedgerTransaction],
        allow_long_window_low_confidence: bool = False,
        long_window_days: int = 60,
    ) -> List[Dict]:
        """Automatic matching with confidence scores"""
        matches = []
        bank_amount_counts = self._build_amount_counts(bank_txns)
        ledger_amount_counts = self._build_amount_counts(ledger_txns)
        
        for bank_txn in bank_txns:
            # PARTIAL bank txns (remainder splits) are also eligible for future matching
            if bank_txn.status not in (TransactionStatus.UNRECONCILED, TransactionStatus.PARTIAL):
                continue
            
            # Generate candidates
            candidates = self._generate_candidates(
                bank_txn,
                ledger_txns,
                allow_long_window_low_confidence=allow_long_window_low_confidence,
                long_window_days=long_window_days,
                bank_amount_counts=bank_amount_counts,
                ledger_amount_counts=ledger_amount_counts,
            )
            
            # Score candidates
            best_match = None
            best_score = 0.0
            best_rule = None
            
            for ledger_txn in candidates:
                for rule_idx, rule in enumerate(self.rules):
                    score, rule_type = rule(bank_txn, ledger_txn)
                    if score > best_score:
                        best_score = score
                        best_match = ledger_txn
                        best_rule = rule_type

                if allow_long_window_low_confidence:
                    score, rule_type = self._rule5_long_window_unique_amount(
                        bank_txn,
                        ledger_txn,
                        long_window_days=long_window_days,
                        bank_amount_counts=bank_amount_counts,
                        ledger_amount_counts=ledger_amount_counts,
                    )
                    if score > best_score:
                        best_score = score
                        best_match = ledger_txn
                        best_rule = rule_type
            
            if best_match and best_score >= 0.85:
                decision = DecisionType.AUTO if best_score >= 0.95 else DecisionType.MANUAL
                matches.append({
                    "bank_txn": bank_txn,
                    "ledger_txn": best_match,
                    "score": best_score,
                    "match_type": best_rule,
                    "decision": decision
                })
        
        return matches
    
    def _generate_candidates(
        self,
        bank_txn: BankTransaction,
        ledger_txns: List[LedgerTransaction],
        allow_long_window_low_confidence: bool = False,
        long_window_days: int = 60,
        bank_amount_counts: Optional[Dict[int, int]] = None,
        ledger_amount_counts: Optional[Dict[int, int]] = None,
    ) -> List[LedgerTransaction]:
        """Generate candidate ledger transactions for matching"""
        candidates = []
        
        for ledger_txn in ledger_txns:
            # Filter by currency and status — PARTIAL ledger txns are also eligible for future matching
            if ledger_txn.status not in (TransactionStatus.UNRECONCILED, TransactionStatus.PARTIAL):
                continue
            from app.services.gl_journal_service import _norm_currency

            if _norm_currency(ledger_txn.currency) != _norm_currency(bank_txn.currency):
                continue
            # Exact magnitude match (bank Cr is negative; ledger may be signed or abs + dr_cr).
            if not self._amounts_match(ledger_txn.amount, bank_txn.amount):
                continue
            # Filter by date window (±7 days)
            date_diff = abs((ledger_txn.book_date - bank_txn.bank_date).days)
            if date_diff > 7:
                if not allow_long_window_low_confidence or date_diff > long_window_days:
                    continue
                # Long-window candidate only allowed when amount is near-unique on both sides
                if not self._is_amount_near_unique(
                    bank_txn,
                    ledger_txn,
                    bank_amount_counts,
                    ledger_amount_counts,
                ):
                    continue
            
            candidates.append(ledger_txn)
        
        return candidates

    @staticmethod
    def _amount_abs(amount: float | None) -> float:
        return abs(float(amount or 0))

    @staticmethod
    def _amounts_match(a: float | None, b: float | None, tol: float = 0.01) -> bool:
        """Equal economic amount ignoring Dr/Cr sign (bank Cr negative, ledger may be signed)."""
        return abs(ReconciliationEngine._amount_abs(a) - ReconciliationEngine._amount_abs(b)) <= tol

    @staticmethod
    def _amount_key(amount: float) -> int:
        return int(round(abs(float(amount or 0)) * 100))

    def _build_amount_counts(self, txns: List[BankTransaction | LedgerTransaction]) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        for txn in txns:
            if getattr(txn, "status", None) != TransactionStatus.UNRECONCILED:
                continue
            key = self._amount_key(txn.amount)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _is_amount_near_unique(
        self,
        bank_txn: BankTransaction,
        ledger_txn: LedgerTransaction,
        bank_amount_counts: Optional[Dict[int, int]],
        ledger_amount_counts: Optional[Dict[int, int]],
    ) -> bool:
        bank_counts = bank_amount_counts or {}
        ledger_counts = ledger_amount_counts or {}
        bank_key = self._amount_key(bank_txn.amount)
        ledger_key = self._amount_key(ledger_txn.amount)
        bank_count = bank_counts.get(bank_key, 0)
        ledger_count = ledger_counts.get(ledger_key, 0)
        return bank_count <= 1 and ledger_count <= 1
    
    def _rule1_exact_match(
        self,
        bank_txn: BankTransaction,
        ledger_txn: LedgerTransaction
    ) -> Tuple[float, MatchType]:
        """Rule 1: Exact match (date + amount + cheque no)"""
        score = 0.0
        
        # Date match
        if bank_txn.bank_date.date() != ledger_txn.book_date.date():
            return (0.0, MatchType.RULE1)
        
        # Amount match (magnitude; ignore opposite Dr/Cr signs)
        if not self._amounts_match(bank_txn.amount, ledger_txn.amount):
            return (0.0, MatchType.RULE1)
        
        # Cheque number match (if available)
        if bank_txn.reference and ledger_txn.reference:
            if bank_txn.reference == ledger_txn.reference:
                score = 0.99
            else:
                return (0.0, MatchType.RULE1)
        else:
            score = 0.98  # No cheque number available
        
        return (score, MatchType.RULE1)
    
    def _rule2_date_proximity(
        self,
        bank_txn: BankTransaction,
        ledger_txn: LedgerTransaction
    ) -> Tuple[float, MatchType]:
        """Rule 2: Date proximity (±3 days + amount + cheque no)"""
        # Date proximity
        date_diff = abs((bank_txn.bank_date - ledger_txn.book_date).days)
        if date_diff > 3:
            return (0.0, MatchType.RULE2)
        
        # Amount match (magnitude; ignore opposite Dr/Cr signs)
        if not self._amounts_match(bank_txn.amount, ledger_txn.amount):
            return (0.0, MatchType.RULE2)
        
        # Cheque/reference handling:
        # - If both references are equal, give the strongest score.
        # - If references differ (common when both sides use auto-generated IDs),
        #   still allow date+amount matching with a slightly lower score.
        # - If either side is missing reference, also allow date+amount matching.
        if bank_txn.reference and ledger_txn.reference:
            if bank_txn.reference == ledger_txn.reference:
                score = 0.95
            else:
                if date_diff == 0:
                    score = 0.92
                elif date_diff <= 1:
                    score = 0.90
                else:
                    score = 0.88
        else:
            if date_diff == 0:
                score = 0.93
            elif date_diff <= 1:
                score = 0.91
            else:
                score = 0.89
        
        return (score, MatchType.RULE2)
    
    def _rule3_amount_description(
        self,
        bank_txn: BankTransaction,
        ledger_txn: LedgerTransaction
    ) -> Tuple[float, MatchType]:
        """Rule 3: Amount + description similarity"""
        # Date proximity
        date_diff = abs((bank_txn.bank_date - ledger_txn.book_date).days)
        if date_diff > 7:
            return (0.0, MatchType.RULE3)
        
        # Amount match (magnitude; ignore opposite Dr/Cr signs)
        if not self._amounts_match(bank_txn.amount, ledger_txn.amount):
            return (0.0, MatchType.RULE3)
        
        # Description similarity
        similarity = self._description_similarity(
            bank_txn.description_norm or bank_txn.description_raw,
            ledger_txn.reference or ""
        )
        
        if similarity >= 0.70:
            score = 0.85
        else:
            return (0.0, MatchType.RULE3)
        
        return (score, MatchType.RULE3)
    
    def _rule4_cheque_number(
        self,
        bank_txn: BankTransaction,
        ledger_txn: LedgerTransaction
    ) -> Tuple[float, MatchType]:
        """Rule 4: Cheque number match"""
        # Cheque number match
        if not (bank_txn.reference and ledger_txn.reference):
            return (0.0, MatchType.RULE4)
        
        if bank_txn.reference != ledger_txn.reference:
            return (0.0, MatchType.RULE4)
        
        # Date proximity
        date_diff = abs((bank_txn.bank_date - ledger_txn.book_date).days)
        if date_diff > 7:
            return (0.0, MatchType.RULE4)
        
        # Amount match (magnitude; ignore opposite Dr/Cr signs)
        if not self._amounts_match(bank_txn.amount, ledger_txn.amount):
            return (0.0, MatchType.RULE4)
        
        score = 0.98
        return (score, MatchType.RULE4)

    def _rule5_long_window_unique_amount(
        self,
        bank_txn: BankTransaction,
        ledger_txn: LedgerTransaction,
        long_window_days: int,
        bank_amount_counts: Optional[Dict[int, int]],
        ledger_amount_counts: Optional[Dict[int, int]],
    ) -> Tuple[float, MatchType]:
        """
        Rule 5: Long-window low-confidence match for selected sets.
        Only applies when amount is near-unique on BOTH sides.
        """
        if not self._amounts_match(bank_txn.amount, ledger_txn.amount):
            return (0.0, MatchType.RULE5)

        date_diff = abs((bank_txn.bank_date - ledger_txn.book_date).days)
        if date_diff <= 7 or date_diff > long_window_days:
            return (0.0, MatchType.RULE5)

        if not self._is_amount_near_unique(
            bank_txn,
            ledger_txn,
            bank_amount_counts,
            ledger_amount_counts,
        ):
            return (0.0, MatchType.RULE5)

        # Keep score below AUTO threshold; this must be manual review.
        if date_diff <= 30:
            return (0.87, MatchType.RULE5)
        return (0.86, MatchType.RULE5)
    
    @staticmethod
    def _description_similarity(desc1: str, desc2: str) -> float:
        """Calculate description similarity (Jaccard similarity)"""
        if not desc1 or not desc2:
            return 0.0
        
        set1 = set(desc1.lower().split())
        set2 = set(desc2.lower().split())
        
        intersection = set1.intersection(set2)
        union = set1.union(set2)
        
        if len(union) == 0:
            return 0.0
        
        return len(intersection) / len(union)
    
    async def manual_match(
        self,
        bank_txn_id: str,
        ledger_txn_id: str,
        company_id: str,
        user_id: str,
        trace_id: str,
        db
    ) -> ReconciliationMatch:
        """Manual matching by user"""
        match_id = str(uuid.uuid4())
        match = ReconciliationMatch(
            id=match_id,
            company_id=company_id,
            trace_id=trace_id,
            bank_txn_id=bank_txn_id,
            ledger_txn_id=ledger_txn_id,
            match_type=MatchType.MANUAL,
            score=1.0,
            decision=DecisionType.MANUAL,
            created_by=user_id
        )
        
        # Create audit log
        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="match",
            payload_json={
                "bank_txn_id": bank_txn_id,
                "ledger_txn_id": ledger_txn_id,
                "match_id": match_id,
                "decision_evidence": build_decision_evidence(
                    action="manual_match",
                    stage="reconciliation",
                    reason="user_selected_pair",
                    outcome="matched",
                    actor_user_id=user_id,
                    source="reconciliation_manual",
                    trace_id=trace_id,
                    matched_by="manual_selection",
                    metadata={"bank_txn_id": bank_txn_id, "ledger_txn_id": ledger_txn_id},
                ),
            },
            user_id=user_id
        )
        
        db.add(match)
        db.add(audit)
        db.commit()
        
        return match
    
    async def multi_manual_match(
        self,
        bank_txn_ids: List[str],
        ledger_txn_ids: List[str],
        company_id: str,
        user_id: str,
        trace_id: str,
        db,
    ) -> Dict:
        """
        Create a multi-to-multi manual match group.

        Supports cross-mode reconciliation (BANK vs AR/AP) and same-mode
        reconciliation (BANK vs BANK, AR vs AR, AR vs AP).

        For same-mode cases the frontend smart classifier assigns one task's
        transactions to bank_txn_ids and the other to ledger_txn_ids even if both
        come from the same DB table.  We detect this via fallback lookup:
          - bank_txn_ids: try BankTransaction first, then LedgerTransaction
          - ledger_txn_ids: try LedgerTransaction first, then BankTransaction

        ReconciliationMatch rows store each transaction in the FK column that
        corresponds to its actual DB table (bank_txn_id for BankTransaction,
        ledger_txn_id for LedgerTransaction).

        If abs(bank_total - ledger_total) > 0.01, the match is rejected — no virtual /
        remainder BankTransaction is created (only real BANK / AR / AP module rows).
        """
        eligible_statuses = [TransactionStatus.UNRECONCILED, TransactionStatus.PARTIAL]

        bank_txn_ids = [i for i in dict.fromkeys(bank_txn_ids or []) if i]
        ledger_txn_ids = [i for i in dict.fromkeys(ledger_txn_ids or []) if i]

        from app.services.gl_journal_service import assert_module_journals_mergeable

        assert_module_journals_mergeable(db, company_id, set(bank_txn_ids), set(ledger_txn_ids))

        # Each bank/ledger txn may only belong to one reconciliation group. Remove
        # these ids from any *existing* group (not only 0:n pending-bank) so the new
        # multi-match group is the sole owner and old draft GLs do not keep the same lines.
        if bank_txn_ids:
            bank_id_set = set(bank_txn_ids)
            bank_removals = (
                db.query(ReconciliationMatch.group_id, ReconciliationMatch.bank_txn_id)
                .join(ReconciliationGroup, ReconciliationMatch.group_id == ReconciliationGroup.id)
                .filter(
                    ReconciliationGroup.company_id == company_id,
                    ReconciliationMatch.company_id == company_id,
                    ReconciliationMatch.bank_txn_id.isnot(None),
                    ReconciliationMatch.bank_txn_id.in_(bank_id_set),
                )
                .all()
            )
            seen_bank = set()
            for gid, bid in bank_removals:
                if not gid or not bid or (gid, bid) in seen_bank:
                    continue
                seen_bank.add((gid, bid))
                await self.remove_group_member(
                    gid,
                    bid,
                    "bank",
                    company_id,
                    user_id,
                    trace_id,
                    "superseded_by_multi_match",
                    db,
                )

        if ledger_txn_ids:
            ledger_id_set = set(ledger_txn_ids)
            pending_removals = (
                db.query(ReconciliationMatch.group_id, ReconciliationMatch.ledger_txn_id)
                .join(ReconciliationGroup, ReconciliationMatch.group_id == ReconciliationGroup.id)
                .filter(
                    ReconciliationGroup.company_id == company_id,
                    ReconciliationMatch.company_id == company_id,
                    ReconciliationMatch.ledger_txn_id.isnot(None),
                    ReconciliationMatch.ledger_txn_id.in_(ledger_id_set),
                )
                .all()
            )
            seen_pairs = set()
            for gid, lid in pending_removals:
                if not gid or not lid or (gid, lid) in seen_pairs:
                    continue
                seen_pairs.add((gid, lid))
                await self.remove_group_member(
                    gid,
                    lid,
                    "ledger",
                    company_id,
                    user_id,
                    trace_id,
                    "superseded_by_multi_match",
                    db,
                )

        # ── Side A (nominally "bank side") ─────────────────────────────────────
        # Primary lookup: filter by eligible status so we don't re-match already-settled txns.
        bank_txns: List[BankTransaction] = db.query(BankTransaction).filter(
            BankTransaction.id.in_(bank_txn_ids),
            BankTransaction.company_id == company_id,
            BankTransaction.status.in_(eligible_statuses),
        ).all()

        # Fallback: IDs not found in BankTransaction may be LedgerTransaction IDs
        # (same-source matching: e.g. AR vs AR, first AR task classified as bank side).
        # No status filter here — the user explicitly selected these transactions for
        # re-matching (e.g. stale UI state after a browser refresh shows already-MATCHED
        # transactions as unmatched).
        found_bank_ids = {b.id for b in bank_txns}
        missing_bank_ids = [i for i in bank_txn_ids if i and i not in found_bank_ids]
        bank_side_ledger_txns: List[LedgerTransaction] = []
        if missing_bank_ids:
            bank_side_ledger_txns = db.query(LedgerTransaction).filter(
                LedgerTransaction.id.in_(missing_bank_ids),
                LedgerTransaction.company_id == company_id,
            ).all()

        # If primary lookup missed some IDs (status was MATCHED/EXCEPTION), include them
        # via a status-agnostic fallback so user intent is honoured.
        all_missing_bank = [i for i in missing_bank_ids
                            if i not in {l.id for l in bank_side_ledger_txns}]
        if all_missing_bank:
            extra = db.query(BankTransaction).filter(
                BankTransaction.id.in_(all_missing_bank),
                BankTransaction.company_id == company_id,
            ).all()
            bank_txns = bank_txns + extra  # type: ignore[operator]

        # ── Side B (nominally "ledger side") ───────────────────────────────────
        ledger_txns: List[LedgerTransaction] = db.query(LedgerTransaction).filter(
            LedgerTransaction.id.in_(ledger_txn_ids),
            LedgerTransaction.company_id == company_id,
            LedgerTransaction.status.in_(eligible_statuses),
        ).all()

        # Fallback: IDs not found in LedgerTransaction may be BankTransaction IDs
        # (same-source matching: e.g. BANK vs BANK, second BANK task is ledger side).
        # Also no status filter — allow re-matching of stale MATCHED transactions.
        found_ledger_ids = {l.id for l in ledger_txns}
        missing_ledger_ids = [i for i in ledger_txn_ids if i and i not in found_ledger_ids]
        ledger_side_bank_txns: List[BankTransaction] = []
        if missing_ledger_ids:
            ledger_side_bank_txns = db.query(BankTransaction).filter(
                BankTransaction.id.in_(missing_ledger_ids),
                BankTransaction.company_id == company_id,
            ).all()

        # Status-agnostic fallback for ledger side that is actually a LedgerTransaction
        all_missing_ledger = [i for i in missing_ledger_ids
                              if i not in {b.id for b in ledger_side_bank_txns}]
        if all_missing_ledger:
            extra = db.query(LedgerTransaction).filter(
                LedgerTransaction.id.in_(all_missing_ledger),
                LedgerTransaction.company_id == company_id,
            ).all()
            ledger_txns = ledger_txns + extra  # type: ignore[operator]

        # All side-A transactions (BankTransaction objects OR LedgerTransaction objects)
        all_bank_side = bank_txns + bank_side_ledger_txns  # type: ignore[operator]
        # All side-B transactions
        all_ledger_side = ledger_txns + ledger_side_bank_txns  # type: ignore[operator]

        if not all_bank_side or not all_ledger_side:
            raise ValueError("No valid transactions found for the given IDs")

        # Compare magnitudes: bank Cr/outflow is stored negative; ledger uses abs + dr_cr.
        total_bank = round(sum(abs(float(t.amount)) for t in all_bank_side), 2)
        total_ledger = round(sum(abs(float(t.amount)) for t in all_ledger_side), 2)
        difference = round(total_bank - total_ledger, 2)

        n_bank = len(all_bank_side)
        n_ledger = len(all_ledger_side)
        if n_bank == 1 and n_ledger == 1:
            cardinality = "1:1"
        elif n_bank == 1:
            cardinality = "1:N"
        elif n_ledger == 1:
            cardinality = "N:1"
        else:
            cardinality = "N:N"

        # Never invent a virtual / remainder bank row — only match real module transactions.
        if abs(difference) > 0.01:
            raise ValueError(
                f"Amounts do not match (bank {total_bank} vs ledger {total_ledger}, "
                f"difference {difference}). Match only equal totals from existing "
                "BANK / AR / AP transactions; no virtual remainder is created."
            )

        from app.services.gl_journal_service import resolve_txns_currency

        bank_for_ccy = [t for t in all_bank_side + all_ledger_side if isinstance(t, BankTransaction)]
        ledger_for_ccy = [t for t in all_bank_side + all_ledger_side if isinstance(t, LedgerTransaction)]
        resolve_txns_currency(bank_for_ccy, ledger_for_ccy)

        # Create group record
        group_id = str(uuid.uuid4())
        group = ReconciliationGroup(
            id=group_id,
            company_id=company_id,
            trace_id=trace_id,
            match_cardinality=cardinality,
            total_bank_amount=total_bank,
            total_ledger_amount=total_ledger,
            difference=difference,
            partial_remainder_txn_id=None,
            created_by=user_id,
        )
        db.add(group)

        # Determine match_type from cardinality
        cardinality_to_match_type = {
            "1:1": MatchType.MANUAL,
            "1:N": MatchType.ONE_MANY,
            "N:1": MatchType.MANY_ONE,
            "N:N": MatchType.MANY_MANY,
        }
        match_type = cardinality_to_match_type.get(cardinality, MatchType.MANUAL)

        # Create one ReconciliationMatch row per transaction, using the correct FK
        # column for each transaction's actual DB table.
        # For same-mode matches (e.g. AR vs AR): both sides store to ledger_txn_id.
        # For BANK vs BANK: both sides store to bank_txn_id.
        match_ids = []
        def _txn_fk(txn):
            """Return (bank_txn_id, ledger_txn_id) for the given transaction object."""
            if isinstance(txn, BankTransaction):
                return txn.id, None
            return None, txn.id

        for txn in all_bank_side:
            bid, lid = _txn_fk(txn)
            match_id = str(uuid.uuid4())
            match_ids.append(match_id)
            db.add(ReconciliationMatch(
                id=match_id,
                company_id=company_id,
                trace_id=trace_id,
                bank_txn_id=bid,
                ledger_txn_id=lid,
                group_id=group_id,
                match_type=match_type,
                score=1.0,
                decision=DecisionType.MANUAL,
                created_by=user_id,
            ))

        for txn in all_ledger_side:
            bid, lid = _txn_fk(txn)
            match_id = str(uuid.uuid4())
            match_ids.append(match_id)
            db.add(ReconciliationMatch(
                id=match_id,
                company_id=company_id,
                trace_id=trace_id,
                bank_txn_id=bid,
                ledger_txn_id=lid,
                group_id=group_id,
                match_type=match_type,
                score=1.0,
                decision=DecisionType.MANUAL,
                created_by=user_id,
            ))

        # Mark all transactions as MATCHED
        for txn in all_bank_side + all_ledger_side:
            txn.status = TransactionStatus.MATCHED

        all_bank_ids = [t.id for t in all_bank_side]
        all_ledger_ids = [t.id for t in all_ledger_side]

        # Audit entry
        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="multi_match",
            payload_json={
                "group_id": group_id,
                "match_cardinality": cardinality,
                "bank_txn_ids": all_bank_ids,
                "ledger_txn_ids": all_ledger_ids,
                "total_bank_amount": total_bank,
                "total_ledger_amount": total_ledger,
                "difference": difference,
                "partial_remainder_txn_id": None,
                "decision_evidence": build_decision_evidence(
                    action="multi_manual_match",
                    stage="reconciliation",
                    reason="user_selected_multi_group",
                    outcome="matched",
                    actor_user_id=user_id,
                    source="reconciliation_multi_manual",
                    trace_id=trace_id,
                    matched_by="manual_selection",
                    metadata={
                        "group_id": group_id,
                        "n_bank": n_bank,
                        "n_ledger": n_ledger,
                    },
                ),
            },
            user_id=user_id,
        )
        db.add(audit)
        db.commit()

        from app.services.gl_journal_service import (
            merge_module_drafts_into_group,
            prune_orphan_recon_draft_journals,
            rebuild_drafts_with_stale_gl_txn_refs,
        )

        prune_orphan_recon_draft_journals(db, company_id)
        rebuild_drafts_with_stale_gl_txn_refs(
            db,
            company_id,
            set(all_ledger_ids),
            set(all_bank_ids),
        )
        merge_module_drafts_into_group(db, company_id, group_id)

        return {
            "group_id": group_id,
            "match_cardinality": cardinality,
            "total_bank_amount": total_bank,
            "total_ledger_amount": total_ledger,
            "difference": difference,
            "partial_remainder_txn_id": None,
            "match_rows_created": len(match_ids),
        }

    async def clear_bank_transactions(
        self,
        bank_txn_ids: List[str],
        company_id: str,
        user_id: str,
        trace_id: str,
        db,
    ) -> Dict:
        """
        Mark selected bank transactions as cleared (MATCHED) without a ledger counterpart.
        Creates a reconciliation group with match_cardinality='N:0' and match rows
        with ledger_txn_id=None.
        """
        bank_txn_ids = [i for i in dict.fromkeys(bank_txn_ids or []) if i]
        if not bank_txn_ids:
            raise ValueError("At least one bank transaction ID is required")

        from app.services.gl_journal_service import assert_module_journals_mergeable

        assert_module_journals_mergeable(db, company_id, set(bank_txn_ids), set())

        bank_txns = db.query(BankTransaction).filter(
            BankTransaction.id.in_(bank_txn_ids),
            BankTransaction.company_id == company_id,
            BankTransaction.status.in_([TransactionStatus.UNRECONCILED, TransactionStatus.PARTIAL]),
        ).all()

        if not bank_txns:
            raise ValueError("No valid UNRECONCILED bank transactions found for the given IDs")

        total_bank = sum(float(b.amount) for b in bank_txns)
        group_id = str(uuid.uuid4())

        group = ReconciliationGroup(
            id=group_id,
            company_id=company_id,
            trace_id=trace_id,
            match_cardinality="N:0",
            total_bank_amount=total_bank,
            total_ledger_amount=0.0,
            difference=total_bank,
            created_by=user_id,
        )
        db.add(group)

        for b in bank_txns:
            db.add(ReconciliationMatch(
                id=str(uuid.uuid4()),
                company_id=company_id,
                trace_id=trace_id,
                bank_txn_id=b.id,
                ledger_txn_id=None,
                group_id=group_id,
                match_type=MatchType.MANUAL,
                score=1.0,
                decision=DecisionType.MANUAL,
                created_by=user_id,
            ))
            b.status = TransactionStatus.MATCHED

        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="clear_bank",
            payload_json={
                "group_id": group_id,
                "match_cardinality": "N:0",
                "bank_txn_ids": [b.id for b in bank_txns],
                "total_bank_amount": total_bank,
                "decision_evidence": build_decision_evidence(
                    action="clear_bank_transactions",
                    stage="reconciliation",
                    reason="user_marked_cleared",
                    outcome="matched",
                    actor_user_id=user_id,
                    source="reconciliation_clear_bank",
                    trace_id=trace_id,
                    matched_by="manual_selection",
                    metadata={"group_id": group_id, "n_bank": len(bank_txns)},
                ),
            },
            user_id=user_id,
        )
        db.add(audit)
        db.commit()

        from app.services.gl_journal_service import merge_module_drafts_into_group

        merge_module_drafts_into_group(db, company_id, group_id)

        return {
            "group_id": group_id,
            "match_cardinality": "N:0",
            "total_bank_amount": total_bank,
            "total_ledger_amount": 0.0,
            "difference": total_bank,
            "match_rows_created": len(bank_txns),
            "gl_only": False,
        }

    async def gl_only_match(
        self,
        bank_txn_ids: List[str],
        company_id: str,
        user_id: str,
        trace_id: str,
        db,
    ) -> Dict:
        """
        Bank + GL code match (no AR/AP ledger counterpart yet).

        Creates group cardinality 'GL:1'. Draft journal = cash (1010) + 1999 suspense;
        intended offset stays on BankTransaction.account_category until approve.
        """
        bank_txn_ids = [i for i in dict.fromkeys(bank_txn_ids or []) if i]
        if len(bank_txn_ids) != 1:
            raise ValueError("GL-only match requires exactly one bank transaction")

        from app.models.reconciliation import ChartOfAccountEntry
        from app.services.gl_journal_service import (
            assert_module_journals_mergeable,
            _resolve_code,
            merge_module_drafts_into_group,
        )

        assert_module_journals_mergeable(db, company_id, set(bank_txn_ids), set())

        bank_txns = db.query(BankTransaction).filter(
            BankTransaction.id.in_(bank_txn_ids),
            BankTransaction.company_id == company_id,
            BankTransaction.status.in_([TransactionStatus.UNRECONCILED, TransactionStatus.PARTIAL]),
        ).all()
        if not bank_txns:
            raise ValueError("No valid UNRECONCILED bank transactions found for the given IDs")

        bt = bank_txns[0]
        gl_raw = (bt.account_category or "").strip()
        if not gl_raw:
            raise ValueError("Bank transaction has no GL code; use clear-bank or set GL code first")
        gl_code = _resolve_code(db, company_id, gl_raw, "")
        if not gl_code:
            raise ValueError(f"GL code not found in chart of accounts: {gl_raw}")
        coa = (
            db.query(ChartOfAccountEntry)
            .filter(
                ChartOfAccountEntry.company_id == company_id,
                ChartOfAccountEntry.code == gl_code,
            )
            .first()
        )
        if not coa:
            raise ValueError(f"GL code not found in chart of accounts: {gl_code}")

        total_bank = round(abs(float(bt.amount or 0)), 2)
        group_id = str(uuid.uuid4())
        group = ReconciliationGroup(
            id=group_id,
            company_id=company_id,
            trace_id=trace_id,
            match_cardinality="GL:1",
            total_bank_amount=total_bank,
            total_ledger_amount=0.0,
            difference=total_bank,
            created_by=user_id,
        )
        db.add(group)
        db.add(
            ReconciliationMatch(
                id=str(uuid.uuid4()),
                company_id=company_id,
                trace_id=trace_id,
                bank_txn_id=bt.id,
                ledger_txn_id=None,
                group_id=group_id,
                match_type=MatchType.MANUAL,
                score=1.0,
                decision=DecisionType.MANUAL,
                created_by=user_id,
            )
        )
        bt.status = TransactionStatus.MATCHED
        # Normalize stored offset to resolved CoA code for approve.
        bt.account_category = gl_code

        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="gl_only_match",
            payload_json={
                "group_id": group_id,
                "match_cardinality": "GL:1",
                "bank_txn_ids": [bt.id],
                "gl_offset_code": gl_code,
                "total_bank_amount": total_bank,
                "decision_evidence": build_decision_evidence(
                    action="gl_only_match",
                    stage="reconciliation",
                    reason="bank_gl_code_match",
                    outcome="matched",
                    actor_user_id=user_id,
                    source="reconciliation_gl_only",
                    trace_id=trace_id,
                    matched_by="manual_or_ai",
                    metadata={"group_id": group_id, "gl_offset_code": gl_code},
                ),
            },
            user_id=user_id,
        )
        db.add(audit)
        db.commit()
        merge_module_drafts_into_group(db, company_id, group_id)
        return {
            "group_id": group_id,
            "match_cardinality": "GL:1",
            "total_bank_amount": total_bank,
            "total_ledger_amount": 0.0,
            "difference": total_bank,
            "match_rows_created": 1,
            "gl_only": True,
            "gl_offset_code": gl_code,
        }

    async def ledger_pending_bank_match(
        self,
        ledger_txn_ids: List[str],
        company_id: str,
        user_id: str,
        trace_id: str,
        db,
    ) -> Dict:
        """
        Match ledger (AR/AP) lines with no bank transaction yet.

        Creates group cardinality 0:N and ReconciliationMatch rows with bank_txn_id=None.
        GL ensure-draft builds ledger lines + suspense (暫記) until bank is loaded.
        """
        ledger_txn_ids = [i for i in dict.fromkeys(ledger_txn_ids or []) if i]
        if not ledger_txn_ids:
            raise ValueError("At least one ledger transaction ID is required")

        from app.services.gl_journal_service import assert_module_journals_mergeable

        assert_module_journals_mergeable(db, company_id, set(), set(ledger_txn_ids))

        ledger_txns = db.query(LedgerTransaction).filter(
            LedgerTransaction.id.in_(ledger_txn_ids),
            LedgerTransaction.company_id == company_id,
            LedgerTransaction.status.in_([TransactionStatus.UNRECONCILED, TransactionStatus.PARTIAL]),
        ).all()

        if not ledger_txns:
            raise ValueError("No valid UNRECONCILED/PARTIAL ledger transactions found for the given IDs")

        found_ids = {lt.id for lt in ledger_txns}
        missing = [i for i in ledger_txn_ids if i not in found_ids]
        if missing:
            raise ValueError(f"Ledger transaction IDs not found or not eligible: {missing[:5]}")

        total_ledger = sum(float(l.amount) for l in ledger_txns)
        group_id = str(uuid.uuid4())
        n = len(ledger_txns)
        cardinality = "0:1" if n == 1 else f"0:{n}"

        group = ReconciliationGroup(
            id=group_id,
            company_id=company_id,
            trace_id=trace_id,
            match_cardinality=cardinality,
            total_bank_amount=0.0,
            total_ledger_amount=total_ledger,
            difference=round(-total_ledger, 2),
            created_by=user_id,
        )
        db.add(group)

        for lt in ledger_txns:
            db.add(
                ReconciliationMatch(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    trace_id=trace_id,
                    bank_txn_id=None,
                    ledger_txn_id=lt.id,
                    group_id=group_id,
                    match_type=MatchType.MANUAL,
                    score=1.0,
                    decision=DecisionType.MANUAL,
                    created_by=user_id,
                )
            )
            lt.status = TransactionStatus.MATCHED

        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="ledger_pending_match",
            payload_json={
                "group_id": group_id,
                "match_cardinality": cardinality,
                "ledger_txn_ids": [lt.id for lt in ledger_txns],
                "total_ledger_amount": total_ledger,
                "decision_evidence": build_decision_evidence(
                    action="ledger_pending_bank_match",
                    stage="reconciliation",
                    reason="user_pending_bank_reconciliation",
                    outcome="matched",
                    actor_user_id=user_id,
                    source="reconciliation_ledger_pending",
                    trace_id=trace_id,
                    matched_by="manual_selection",
                    metadata={"group_id": group_id, "n_ledger": len(ledger_txns)},
                ),
            },
            user_id=user_id,
        )
        db.add(audit)
        db.commit()

        from app.services.gl_journal_service import merge_module_drafts_into_group

        merge_module_drafts_into_group(db, company_id, group_id)

        return {
            "group_id": group_id,
            "match_cardinality": cardinality,
            "total_bank_amount": 0.0,
            "total_ledger_amount": total_ledger,
            "difference": round(-total_ledger, 2),
            "match_rows_created": len(ledger_txns),
        }

    async def remove_group_member(
        self,
        group_id: str,
        txn_id: str,
        txn_type: str,
        company_id: str,
        user_id: str,
        trace_id: str,
        reason: str,
        db,
    ) -> Dict:
        """
        Remove a single transaction from a multi-match group.

        - Deletes all ReconciliationMatch rows where group_id matches AND the txn is referenced.
        - Returns the transaction to UNRECONCILED status.
        - If the group becomes empty (0 bank OR 0 ledger members), dissolves the group entirely
          and returns all remaining members to UNRECONCILED.
        """
        group = db.query(ReconciliationGroup).filter(
            ReconciliationGroup.id == group_id,
            ReconciliationGroup.company_id == company_id,
        ).first()
        if not group:
            raise ValueError(f"Reconciliation group not found: {group_id}")

        from app.services.gl_journal_service import assert_group_has_no_posted_journal

        assert_group_has_no_posted_journal(db, company_id, group_id)

        # Find and delete match rows referencing this transaction
        if txn_type == "bank":
            rows_to_delete = db.query(ReconciliationMatch).filter(
                ReconciliationMatch.group_id == group_id,
                ReconciliationMatch.bank_txn_id == txn_id,
            ).all()
        else:
            rows_to_delete = db.query(ReconciliationMatch).filter(
                ReconciliationMatch.group_id == group_id,
                ReconciliationMatch.ledger_txn_id == txn_id,
            ).all()

        for row in rows_to_delete:
            db.delete(row)

        # Return the removed transaction to UNRECONCILED
        if txn_type == "bank":
            txn = db.query(BankTransaction).filter(
                BankTransaction.id == txn_id,
                BankTransaction.company_id == company_id,
            ).first()
            if txn:
                txn.status = TransactionStatus.UNRECONCILED
        else:
            txn = db.query(LedgerTransaction).filter(
                LedgerTransaction.id == txn_id,
                LedgerTransaction.company_id == company_id,
            ).first()
            if txn:
                txn.status = TransactionStatus.UNRECONCILED

        # Check remaining group members
        remaining_rows = db.query(ReconciliationMatch).filter(
            ReconciliationMatch.group_id == group_id,
        ).all()

        remaining_bank_ids = {r.bank_txn_id for r in remaining_rows if r.bank_txn_id}
        remaining_ledger_ids = {r.ledger_txn_id for r in remaining_rows if r.ledger_txn_id}

        card = (group.match_cardinality or "").strip()
        if not remaining_rows:
            should_dissolve = True
        elif card in ("N:0", "GL:1"):
            # Bank-only groups (cleared bank or bank+GL): dissolve when no bank members left.
            should_dissolve = len(remaining_bank_ids) == 0
        elif card.startswith("0:"):
            # 0:N ledger-pending-bank groups (bank_txn_id is always null)
            should_dissolve = len(remaining_ledger_ids) == 0
        else:
            should_dissolve = (len(remaining_bank_ids) == 0 or len(remaining_ledger_ids) == 0)

        if should_dissolve:
            # Dissolve the entire group — return all remaining members to UNRECONCILED
            for row in remaining_rows:
                if row.bank_txn_id:
                    b = db.query(BankTransaction).filter(BankTransaction.id == row.bank_txn_id).first()
                    if b:
                        b.status = TransactionStatus.UNRECONCILED
                if row.ledger_txn_id:
                    l = db.query(LedgerTransaction).filter(LedgerTransaction.id == row.ledger_txn_id).first()
                    if l:
                        l.status = TransactionStatus.UNRECONCILED
                db.delete(row)
            # Also restore the partial remainder (if any) back to UNRECONCILED so it re-enters the unmatched pool
            if group.partial_remainder_txn_id:
                remainder = db.query(BankTransaction).filter(
                    BankTransaction.id == group.partial_remainder_txn_id
                ).first()
                if remainder and remainder.status == TransactionStatus.PARTIAL:
                    remainder.status = TransactionStatus.UNRECONCILED
            # Remove draft GL linked to this group so orphan vouchers don't linger in the UI
            from app.services.gl_journal_service import delete_draft_for_group
            delete_draft_for_group(db, company_id, group_id)
            db.delete(group)
            group_dissolved = True
            remaining_members = 0
        else:
            # Recalculate group totals
            bank_txns = db.query(BankTransaction).filter(
                BankTransaction.id.in_(remaining_bank_ids)
            ).all()
            ledger_txns = db.query(LedgerTransaction).filter(
                LedgerTransaction.id.in_(remaining_ledger_ids)
            ).all()
            new_bank_total = round(sum(abs(float(b.amount)) for b in bank_txns), 2)
            new_ledger_total = round(sum(abs(float(l.amount)) for l in ledger_txns), 2)
            new_diff = round(new_bank_total - new_ledger_total, 2)

            n_bank = len(remaining_bank_ids)
            n_ledger = len(remaining_ledger_ids)
            if card.startswith("0:") and n_bank == 0:
                new_cardinality = "0:1" if n_ledger == 1 else f"0:{n_ledger}"
            elif card == "N:0" or (n_ledger == 0 and n_bank > 0):
                new_cardinality = "N:0"
            elif n_bank == 1 and n_ledger == 1:
                new_cardinality = "1:1"
            elif n_bank == 1:
                new_cardinality = "1:N"
            elif n_ledger == 1:
                new_cardinality = "N:1"
            else:
                new_cardinality = "N:N"

            group.total_bank_amount = new_bank_total
            group.total_ledger_amount = new_ledger_total
            group.difference = new_diff
            group.match_cardinality = new_cardinality
            group_dissolved = False
            remaining_members = len(remaining_rows)
            db.flush()
            from app.services.gl_journal_service import rebuild_primary_draft_for_group

            try:
                rebuild_primary_draft_for_group(db, company_id, group_id)
            except ValueError:
                pass

        # Audit entry
        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="group_unmatch_member",
            payload_json={
                "group_id": group_id,
                "removed_txn_id": txn_id,
                "removed_txn_type": txn_type,
                "reason": reason,
                "group_dissolved": group_dissolved,
                "decision_evidence": build_decision_evidence(
                    action="group_unmatch_member",
                    stage="reconciliation",
                    reason=reason or "user_removed_group_member",
                    outcome="unmatched",
                    actor_user_id=user_id,
                    source="reconciliation_multi_manual",
                    trace_id=trace_id,
                    matched_by="manual_selection",
                    metadata={"group_id": group_id, "txn_id": txn_id, "txn_type": txn_type},
                ),
            },
            user_id=user_id,
        )
        db.add(audit)
        db.commit()

        return {
            "group_id": group_id,
            "group_dissolved": group_dissolved,
            "remaining_members": remaining_members,
        }

    async def dissolve_group(
        self,
        group_id: str,
        company_id: str,
        user_id: str,
        trace_id: str,
        reason: str,
        db,
    ) -> Dict:
        """Dissolve a recon group (incl. orphan groups with no match rows / 0 members)."""
        group = (
            db.query(ReconciliationGroup)
            .filter(
                ReconciliationGroup.id == group_id,
                ReconciliationGroup.company_id == company_id,
            )
            .first()
        )
        if not group:
            # Already gone — treat as success so UI cancel is idempotent.
            return {
                "group_id": group_id,
                "group_dissolved": True,
                "remaining_members": 0,
                "already_gone": True,
            }

        from app.services.gl_journal_service import assert_group_has_no_posted_journal, delete_draft_for_group

        assert_group_has_no_posted_journal(db, company_id, group_id)

        rows = (
            db.query(ReconciliationMatch)
            .filter(
                ReconciliationMatch.group_id == group_id,
                ReconciliationMatch.company_id == company_id,
            )
            .all()
        )
        restored_bank: list[str] = []
        restored_ledger: list[str] = []
        for row in rows:
            if row.bank_txn_id:
                b = (
                    db.query(BankTransaction)
                    .filter(
                        BankTransaction.id == row.bank_txn_id,
                        BankTransaction.company_id == company_id,
                    )
                    .first()
                )
                if b:
                    b.status = TransactionStatus.UNRECONCILED
                    restored_bank.append(b.id)
            if row.ledger_txn_id:
                lt = (
                    db.query(LedgerTransaction)
                    .filter(
                        LedgerTransaction.id == row.ledger_txn_id,
                        LedgerTransaction.company_id == company_id,
                    )
                    .first()
                )
                if lt:
                    lt.status = TransactionStatus.UNRECONCILED
                    restored_ledger.append(lt.id)
            db.delete(row)

        if group.partial_remainder_txn_id:
            remainder = (
                db.query(BankTransaction)
                .filter(BankTransaction.id == group.partial_remainder_txn_id)
                .first()
            )
            if remainder and remainder.status == TransactionStatus.PARTIAL:
                remainder.status = TransactionStatus.UNRECONCILED

        delete_draft_for_group(db, company_id, group_id)
        db.delete(group)

        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="dissolve_group",
            payload_json={
                "group_id": group_id,
                "reason": reason,
                "restored_bank_txn_ids": restored_bank,
                "restored_ledger_txn_ids": restored_ledger,
                "decision_evidence": build_decision_evidence(
                    action="dissolve_group",
                    stage="reconciliation",
                    reason=reason or "user_cancel_match",
                    outcome="unmatched",
                    actor_user_id=user_id,
                    source="reconciliation_dissolve_group",
                    trace_id=trace_id,
                    matched_by="manual_selection",
                    metadata={"group_id": group_id},
                ),
            },
            user_id=user_id,
        )
        db.add(audit)
        db.commit()
        return {
            "group_id": group_id,
            "group_dissolved": True,
            "remaining_members": 0,
            "restored_bank_txn_ids": restored_bank,
            "restored_ledger_txn_ids": restored_ledger,
        }

    async def unmatch(
        self,
        match_id: str,
        company_id: str,
        user_id: str,
        trace_id: str,
        reason: str,
        db
    ) -> None:
        """Unmatch a reconciliation"""
        match = db.query(ReconciliationMatch).filter(
            ReconciliationMatch.id == match_id,
            ReconciliationMatch.company_id == company_id,
        ).first()
        if not match:
            raise ValueError(f"Match not found: {match_id}")
        
        # Create audit log
        audit = ReconciliationAudit(
            id=str(uuid.uuid4()),
            company_id=company_id,
            trace_id=trace_id,
            action="unmatch",
            payload_json={
                "match_id": match_id,
                "reason": reason,
                "original_match": {
                    "bank_txn_id": match.bank_txn_id,
                    "ledger_txn_id": match.ledger_txn_id,
                    "match_type": match.match_type.value,
                    "score": match.score
                },
                "decision_evidence": build_decision_evidence(
                    action="manual_unmatch",
                    stage="reconciliation",
                    reason=reason or "user_requested_unmatch",
                    outcome="unmatched",
                    actor_user_id=user_id,
                    source="reconciliation_manual",
                    trace_id=match.trace_id or trace_id,
                    matched_by="manual_selection",
                    metadata={"match_id": match_id},
                ),
            },
            user_id=user_id
        )
        
        db.delete(match)
        db.add(audit)
        db.commit()


# -- Loan Installment Matching -------------------------------------------------

def match_bank_vs_loan_installments(
    company_id: str,
    bank_txns: list,
    db,
    date_window_days: int = 5,
    amount_tolerance_pct: float = 0.01,
) -> list[dict]:
    """
    Match BankTransactions against LoanInstallments for RECON integration.

    Matching rules:
    1. Bank transaction description contains loan/interest/repayment keywords.
    2. Amount matches installment total_payment or interest_portion within tolerance.
    3. Date within date_window_days of installment due_date.

    Returns list of match dicts with bank_txn_id, installment_id, loan_id,
    match_type, confidence, amount, due_date, label.
    """
    from app.models.other import LoanInstallment

    _LOAN_KEYWORDS = [
        "loan", "mortgage", "repayment", "installment", "instalment",
        "hp payment", "hire purchase", "interest", "principal",
        "??", "??", "??", "??", "??",
    ]

    def _amount_close(a: float, b: float) -> bool:
        if b == 0:
            return False
        return abs(a - b) / abs(b) <= amount_tolerance_pct

    installments = (
        db.query(LoanInstallment)
        .filter(
            LoanInstallment.company_id == company_id,
            LoanInstallment.status == "pending",
        )
        .all()
    )
    if not installments:
        return []

    matches = []

    for bank_txn in bank_txns:
        desc = (getattr(bank_txn, "description", "") or "").lower()
        ref = (getattr(bank_txn, "reference", "") or "").lower()
        has_keyword = any(k in desc or k in ref for k in _LOAN_KEYWORDS)
        if not has_keyword:
            continue

        bank_amount = abs(float(getattr(bank_txn, "amount", 0) or 0))
        bank_date = getattr(bank_txn, "transaction_date", None) or getattr(bank_txn, "date", None)

        for inst in installments:
            if bank_date and inst.due_date:
                try:
                    delta = abs((bank_date - inst.due_date).days)
                    if delta > date_window_days:
                        continue
                except Exception:
                    pass

            if _amount_close(bank_amount, inst.total_payment):
                matches.append({
                    "bank_txn_id": bank_txn.id,
                    "installment_id": inst.id,
                    "loan_id": inst.loan_id,
                    "match_type": "loan_total_payment",
                    "confidence": 0.92,
                    "amount": bank_amount,
                    "due_date": inst.due_date.isoformat() if inst.due_date else None,
                    "label": "Loan Repayment (Principal + Interest)",
                })
                inst.bank_txn_id_principal = bank_txn.id
                inst.bank_txn_id_interest = bank_txn.id
                inst.status = "paid"
                break
            elif _amount_close(bank_amount, inst.interest_portion):
                matches.append({
                    "bank_txn_id": bank_txn.id,
                    "installment_id": inst.id,
                    "loan_id": inst.loan_id,
                    "match_type": "loan_interest_only",
                    "confidence": 0.85,
                    "amount": bank_amount,
                    "due_date": inst.due_date.isoformat() if inst.due_date else None,
                    "label": "Loan Interest Payment",
                })
                inst.bank_txn_id_interest = bank_txn.id
                break

    try:
        db.commit()
    except Exception:
        pass

    return matches
