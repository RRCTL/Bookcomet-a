"""Tests for workflow folder/archive API helpers."""

from types import SimpleNamespace

from app.api.workflows import FolderPatchRequest, _run_out, _run_summary_out


class _FakeQuery:
    def __init__(self, rows=None, count=0):
        self._rows = rows or []
        self._count = count

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows

    def count(self):
        return self._count


class _FakeDb:
    def query(self, *_args):
        return _FakeQuery(count=0)


def test_run_summary_out_includes_folder_and_archive_fields():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="AP",
        title="Batch",
        run_status="draft",
        folder_id="folder-1",
        archived_at=None,
        processing_removed_at=None,
        created_at=None,
        updated_at=None,
    )
    summary = _run_summary_out(run, _FakeDb())  # type: ignore[arg-type]
    assert summary["folder_id"] == "folder-1"
    assert summary["archived_at"] is None
    assert summary["processing_removed_at"] is None


def test_run_out_includes_folder_and_archive_fields():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="AR",
        title="Batch",
        run_status="draft",
        graph_json={"nodes": [], "edges": []},
        node_states_json={},
        console_log_json=[],
        snapshot_message_id=None,
        folder_id=None,
        archived_at=None,
        processing_removed_at=None,
        created_at=None,
        updated_at=None,
    )
    out = _run_out(run, _FakeDb())  # type: ignore[arg-type]
    assert out["folder_id"] is None
    assert out["archived_at"] is None
    assert out["processing_removed_at"] is None
    assert out["files"] == []


def test_folder_patch_request_accepts_sort_order():
    body = FolderPatchRequest(name="Renamed", sort_order=3)
    assert body.name == "Renamed"
    assert body.sort_order == 3


def test_folder_patch_request_sort_order_optional():
    body = FolderPatchRequest(name="Only name")
    assert body.sort_order is None
