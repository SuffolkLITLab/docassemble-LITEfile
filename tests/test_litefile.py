"""Generic transport tests that do not depend on a particular interview."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

PACKAGE = Path(__file__).parents[1]
MODULE = PACKAGE / "docassemble" / "LITEfile" / "litefile.py"
spec = importlib.util.spec_from_file_location("litefile_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
SHARED_YAML = PACKAGE / "docassemble" / "LITEfile" / "data/questions/litefile.yml"


def document(tmp_path, name="court_form"):
    pdf = tmp_path / f"{name}.pdf"
    pdf.write_bytes(b"%PDF-1.4\ngeneric synthetic test")
    file = Mock()
    file.path.return_value = str(pdf)
    return {"id": name, "file": file, "path": str(pdf), "form_name": "Court form"}


def test_source_id_is_uuid():
    import uuid

    assert str(uuid.UUID(adapter.litefile_source_id()))


def test_person_reads_only_available_fields_and_supports_custom_mapping():
    answers = {"people[0].name.first": "Sam", "people[0].email": "sam@example.org"}
    assert adapter.litefile_person("people[0]", known=answers.get) == {
        "first_name": "Sam",
        "email": "sam@example.org",
    }
    assert adapter.litefile_person(
        "people[0]", fields={"display_name": "name.first"}, known=answers.get
    ) == {"display_name": "Sam"}


def test_payload_flattens_people_hashes_pdfs_and_does_not_mutate_input(tmp_path):
    doc = document(tmp_path)
    facts = {
        "schema_version": 1,
        "jurisdiction": "example",
        "filer": {"first_name": "Sam"},
        "parties": [
            {"source": "people[0]", "semantic_role": "plaintiff", "person": {"first_name": "Sam"}}
        ],
        "known_filing_facts": {"answered": False, "zero": 0, "unknown": None},
    }
    original = json.loads(json.dumps(facts))
    payload = adapter.build_litefile_payload(facts, "stable-id", [doc], "https://interview.example/")
    assert facts == original
    assert payload["parties"] == [{"source": "people[0]", "semantic_role": "plaintiff", "first_name": "Sam"}]
    assert payload["known_filing_facts"] == {"answered": False, "zero": 0}
    assert payload["idempotency_key"] == payload["source_id"] == "stable-id"
    assert payload["return_url"] == "https://interview.example/"
    assert payload["documents"][0]["sha256"] == hashlib.sha256(Path(doc["path"]).read_bytes()).hexdigest()
    assert "file" not in payload["documents"][0] and "path" not in payload["documents"][0]


def test_handoff_keeps_retry_identity_and_keeps_source_secret_out_of_payload(tmp_path):
    doc = document(tmp_path)
    payload = adapter.build_litefile_payload({"jurisdiction": "example"}, "stable", [doc], "")
    response = Mock(status_code=201)
    response.json.return_value = {
        "draft_id": "42",
        "state": "needs_input",
        "continue_url": "https://litefile.example.org/handoff/claim/example-token/",
    }
    config = {"source": "example-source", "token": "secret", "base_url": "https://litefile.example.org"}
    with patch.object(adapter.requests, "post", return_value=response) as post:
        first = adapter.send_litefile_handoff(payload, [doc], config)
        second = adapter.send_litefile_handoff(payload, [doc], config)
    assert first == second and first["ok"]
    assert post.call_args_list[0].kwargs["data"] == post.call_args_list[1].kwargs["data"]
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert "secret" not in post.call_args.kwargs["data"]["payload"]
    assert post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"source": "s", "token": "t", "base_url": "http://litefile.example.org"},
        {"source": "s", "token": "t", "base_url": "not a url"},
    ],
)
def test_missing_or_insecure_configuration_fails_closed(config, tmp_path):
    result = adapter.send_litefile_handoff({}, [document(tmp_path)], config)
    assert not result["ok"]
    assert "configured" in result["message"]


def test_local_http_requires_explicit_development_flag(tmp_path):
    config = {
        "source": "example-source",
        "token": "secret",
        "base_url": "http://127.0.0.1:8000",
        "allow_insecure_local_development": True,
    }
    response = Mock(status_code=201)
    response.json.return_value = {
        "draft_id": "42",
        "state": "needs_input",
        "continue_url": "http://127.0.0.1:8000/handoff/claim/example-token/",
    }
    with patch.object(adapter.requests, "post", return_value=response):
        assert adapter.send_litefile_handoff({}, [document(tmp_path)], config)["ok"]


def test_correction_uses_separate_deterministic_idempotency_key(tmp_path):
    doc = document(tmp_path)
    payload = adapter.build_litefile_payload({"jurisdiction": "example"}, "stable", [doc], "")
    response = Mock(status_code=200)
    response.json.return_value = {"draft_id": "42", "continue_url": "https://litefile.example.org/handoff/drafts/42/"}
    config = {"source": "example-source", "token": "secret", "base_url": "https://litefile.example.org"}
    with patch.object(adapter.requests, "post", return_value=response) as post:
        adapter.send_litefile_handoff(payload, [doc], config, "correction-token")
        adapter.send_litefile_handoff(payload, [doc], config, "correction-token")
    sent = json.loads(post.call_args_list[0].kwargs["data"]["payload"])
    assert post.call_args_list[0].kwargs["data"] == post.call_args_list[1].kwargs["data"]
    assert post.call_args_list[0].args[0].endswith("/api/handoffs/v1/documents/")
    assert post.call_args_list[0].kwargs["headers"]["X-LITEFile-Correction"] == "correction-token"
    assert sent["idempotency_key"] != payload["idempotency_key"]
    assert sent["source_id"] == payload["source_id"]


def test_shared_yaml_has_no_interview_specific_document_mappings():
    blocks = [block for block in yaml.safe_load_all(SHARED_YAML.read_text()) if block]
    names = {block.get("variable name") for block in blocks}
    assert {"litefile_data", "litefile_document_map"}.isdisjoint(names)
    assert any(block.get("event") == "litefile_send" for block in blocks)
    assert any(block.get("event") == "litefile_upload" for block in blocks)
