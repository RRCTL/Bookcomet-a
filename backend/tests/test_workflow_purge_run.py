from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.graph.workflow_service import WorkflowService
from app.models.transaction import TransactionStatus


def test_purge_run_deletes_unreconciled_ledger_and_run():
    run = SimpleNamespace(
        id="run-1",
        company_id="co-1",
        task_id="task-1",
        processing_mode="AP",
        node_states_json={
            "approved_payload": {
                "arapTransactions": [
                    {
                        "voucher_no": "V1",
                        "date": "2025-04-29",
                        "amount": "100",
                        "currency": "HKD",
                    }
                ]
            }
        },
    )
    task_file = SimpleNamespace(
        id="tf-1",
        task_id="task-1",
        storage_path="/tmp/x.pdf",
        deleted_at=None,
    )
    task = SimpleNamespace(id="task-1", deleted_at=None, file_count=1)
    ledger_row = SimpleNamespace(
        company_id="co-1",
        module="AP",
        doc_id="V1",
        book_date=datetime(2025, 4, 29),
        amount=100.0,
        status=TransactionStatus.UNRECONCILED,
    )

    db = MagicMock()
    deleted: dict[str, list] = {"ledger": [], "run": []}

    class _Chain:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_k):
            return self

        def all(self):
            return self._rows

        def delete(self, synchronize_session=False):
            del synchronize_session
            if self._rows is ledger_row:
                deleted["ledger"].append(ledger_row)
            return 1

        def first(self):
            return self._rows[0] if self._rows else None

    def query_side_effect(model):
        name = getattr(model, "__name__", str(model))
        if name == "TaskFile":
            return _Chain([task_file])
        if name == "WorkflowRunFile":
            return _Chain([])
        if name == "WorkflowPool2Package":
            return _Chain([])
        if name == "ChatTask":
            return _Chain([task])
        if name == "LedgerTransaction":
            return _Chain(ledger_row)
        return _Chain([])

    db.query.side_effect = query_side_effect

    def capture_delete(obj):
        if getattr(obj, "id", None) == "run-1":
            deleted["run"].append(obj)

    db.delete.side_effect = capture_delete

    with patch.object(WorkflowService, "_purge_run_storage_artifacts"):
        WorkflowService.purge_run(db, run)

    assert deleted["ledger"]
    assert deleted["run"]
    assert task.deleted_at is not None
    assert task.file_count == 0
    assert task_file.deleted_at is not None
    db.commit.assert_called_once()
