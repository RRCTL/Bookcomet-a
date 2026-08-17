import json
from pathlib import Path

from app.services.pool2_storage import Pool2Storage, content_hash_json


def test_content_hash_json_stable():
    a = content_hash_json({"b": 2, "a": 1})
    b = content_hash_json({"a": 1, "b": 2})
    assert a == b


def test_save_node_and_final_package(tmp_path: Path):
    store = Pool2Storage(root=tmp_path)
    payload = {"merged_ocr": [{"id_number": "1"}]}
    cid, path = store.save_node_output("co-1", "run-1", "vlm", payload)
    assert cid
    assert Path(path).is_file()
    loaded = store.load_node_output(path)
    assert loaded == payload

    manifest = {
        "run_id": "run-1",
        "approved_payload": {"arapTransactions": [{"id_number": "1"}]},
    }
    pkg_id, pkg_path = store.save_final_package("co-1", "AP", "run-1", manifest)
    assert pkg_id
    raw = json.loads(Path(pkg_path).read_text(encoding="utf-8"))
    assert raw["approved_payload"]["arapTransactions"][0]["id_number"] == "1"
