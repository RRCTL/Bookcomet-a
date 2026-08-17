"""Tests for GET /api/workflows/runs summary shape."""

from types import SimpleNamespace

from app.api.workflows import _run_summary_out


class _FakeQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return [
            SimpleNamespace(
                task_file_id="a",
                file_status="ok",
                uploaded_at=None,
                created_at=None,
                upload_batch_id=None,
                batch_committed_at=None,
            ),
            SimpleNamespace(
                task_file_id="b",
                file_status="pending",
                uploaded_at=None,
                created_at=None,
                upload_batch_id=None,
                batch_committed_at=None,
            ),
        ]


class _FakeDb:
    def query(self, *_args):
        return _FakeQuery()


def test_run_summary_out_omits_heavy_fields():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="default",
        processing_mode="AP",
        title="Test",
        run_status="draft",
        folder_id=None,
        archived_at=None,
        processing_removed_at=None,
        created_at=None,
        updated_at=None,
    )
    summary = _run_summary_out(run, _FakeDb())  # type: ignore[arg-type]
    assert summary["id"] == "run-1"
    assert summary["file_count"] == 2
    assert "graph_json" not in summary
    assert "node_states_json" not in summary
    assert "console_log_json" not in summary
    assert "files" not in summary
