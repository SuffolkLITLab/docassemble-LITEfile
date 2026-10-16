"""Shared adapter regressions using a captured RFA example, without a server.

The fixture is not the live RFA configuration or a runtime package dependency.
"""

import hashlib
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
import pytest
import importlib.util

PACKAGE = Path(__file__).parents[1]
MODULE = PACKAGE / "docassemble/LITEfile/litefile.py"
spec = importlib.util.spec_from_file_location("litefile_example_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

YAML = PACKAGE / "tests/fixtures/rfa_settings.yml"
SHARED_YAML = Path(adapter.__file__).parent / "data/questions/litefile.yml"
BLOCKS = {
    block.get("variable name"): block
    for block in [*yaml.safe_load_all(SHARED_YAML.read_text()), *yaml.safe_load_all(YAML.read_text())]
    if block
}


def declared_data(answers=None):
    answers = answers or {}

    def reader(path, alternative=""):
        return answers.get(path, alternative)

    namespace = {
        "showifdef": reader,
        "litefile_person": partial(adapter.litefile_person, known=reader),
        "litefile_filing_hint_overrides": BLOCKS["litefile_filing_hint_overrides"][
            "data"
        ],
    }

    def evaluate(node):
        if isinstance(node, dict):
            return {key: evaluate(value) for key, value in node.items()}
        if isinstance(node, list):
            return [evaluate(value) for value in node]
        return eval(str(node), namespace)

    return evaluate(BLOCKS["litefile_data"]["data from code"])


def bundle(tmp_path):
    pdf = tmp_path / "complaint.pdf"
    pdf.write_bytes(b"%PDF-1.4\nsynthetic test")
    file = Mock()
    file.path.return_value = str(pdf)
    return [
        {
            **BLOCKS["litefile_document_map"]["data"]["RFAcomplaint"],
            "id": "RFAcomplaint",
            "path": str(pdf),
            "file": file,
        }
    ]


def config():
    return {
        "source": "rfa",
        "token": "secret",
        "base_url": "https://litefile.example.org",
    }


def test_missing_answers_never_trigger_interview_resolution(tmp_path):
    docs = bundle(tmp_path)
    payload = adapter.build_litefile_payload(declared_data(), "stable", docs, "")
    assert payload["filer"] == {}
    assert payload["parties"] == []
    assert (
        payload["documents"][0]["sha256"]
        == hashlib.sha256(Path(docs[0]["path"]).read_bytes()).hexdigest()
    )
    assert not any(key.endswith("_code") for key in payload)


def test_minor_uses_distinct_semantic_case_type_and_reuses_known_contact(tmp_path):
    answers = {
        "who_needs_protection": "order_obo_child",
        "users[0].name.first": "Taylor",
        "users[0].email": "test@example.org",
        "user_selected_county": "Chittenden",
    }
    payload = adapter.build_litefile_payload(
        declared_data(answers), "stable", bundle(tmp_path), ""
    )
    assert payload["case_type_name_hints"] == ["Relief from Abuse on Behalf of a Minor"]
    assert payload["filer"]["email"] == "test@example.org"
    assert payload["case"]["county"] == "Chittenden"


def test_retries_keep_key_and_source_secret_stays_in_header(tmp_path):
    docs = bundle(tmp_path)
    payload = adapter.build_litefile_payload(declared_data(), "stable", docs, "")
    response = Mock(status_code=201)
    response.json.return_value = {
        "draft_id": "42",
        "state": "needs_input",
        "continue_url": "https://litefile.example.org/handoff/claim/token/",
    }
    with patch.object(adapter.requests, "post", return_value=response) as post:
        first = adapter.send_litefile_handoff(payload, docs, config())
        second = adapter.send_litefile_handoff(payload, docs, config())
    assert first == second
    assert first["ok"]
    assert (
        post.call_args_list[0].kwargs["data"] == post.call_args_list[1].kwargs["data"]
    )
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert "secret" not in post.call_args.kwargs["data"]["payload"]
    assert post.call_args.kwargs["allow_redirects"] is False


def test_unexpected_continuation_origin_and_network_errors_are_recoverable(tmp_path):
    docs = bundle(tmp_path)
    payload = adapter.build_litefile_payload(declared_data(), "stable", docs, "")
    response = Mock(status_code=201)
    response.json.return_value = {
        "draft_id": "42",
        "state": "needs_input",
        "continue_url": "https://untrusted.example/handoff/claim/token/",
    }
    with patch.object(adapter.requests, "post", return_value=response):
        assert not adapter.send_litefile_handoff(payload, docs, config())["ok"]
    with patch.object(adapter.requests, "post", side_effect=adapter.requests.Timeout()):
        result = adapter.send_litefile_handoff(payload, docs, config())
    assert not result["ok"]
    assert "same draft" in result["message"]


def test_malformed_json_receipts_are_recoverable(tmp_path):
    docs = bundle(tmp_path)
    payload = adapter.build_litefile_payload(declared_data(), "stable", docs, "")
    response = Mock(status_code=201)
    response.json.return_value = []
    with patch.object(adapter.requests, "post", return_value=response):
        result = adapter.send_litefile_handoff(payload, docs, config())
    assert not result["ok"]
    assert "same draft" in result["message"]


def test_document_replacement_has_stable_distinct_idempotency_key(tmp_path):
    import json

    docs = bundle(tmp_path)
    payload = adapter.build_litefile_payload(declared_data(), "stable", docs, "")
    response = Mock(status_code=200)
    response.json.return_value = {
        "draft_id": "42",
        "state": "needs_input",
        "continue_url": "https://litefile.example.org/handoff/drafts/42/",
    }
    with patch.object(adapter.requests, "post", return_value=response) as post:
        adapter.send_litefile_handoff(payload, docs, config(), "correction-token")
        adapter.send_litefile_handoff(payload, docs, config(), "correction-token")
    requests = post.call_args_list
    assert requests[0].args[0].endswith("/api/handoffs/v1/documents/")
    assert requests[0].kwargs["data"] == requests[1].kwargs["data"]
    sent = json.loads(requests[0].kwargs["data"]["payload"])
    assert sent["idempotency_key"] != payload["idempotency_key"]
    assert sent["source_id"] == payload["source_id"]


def test_declarative_hints_and_unknown_facts_are_transmitted(tmp_path):
    data = declared_data(
        {
            "users[0].name.first": "Taylor",
            "users[0].is_form_filler": False,
            "children.target_number": 0,
        }
    )
    docs = bundle(tmp_path)
    payload = adapter.build_litefile_payload(data, "stable", docs, "")
    assert payload["filing_type_name_hints"] == ["Initial Filing"]
    assert payload["case_category_name_hints"] == ["Family"]
    assert payload["case_type_name_hints"] == ["Relief from Abuse"]
    assert payload["documents"][0]["filing_component_name_hints"] == ["Lead Document"]
    assert payload["documents"][0]["filing_type_name_hints"] == ["Initial Filing"]
    assert payload["parties"][0]["semantic_role"] == "plaintiff"
    assert payload["parties"][0]["first_name"] == "Taylor"
    assert payload["known_filing_facts"] == {
        "users[0].is_form_filler": False,
        "children.target_number": 0,
    }
    assert "file" not in payload["documents"][0]
    assert "path" not in payload["documents"][0]


def test_declarative_county_and_court_hint_overrides_are_transmitted(tmp_path):
    data = declared_data()
    data["filing_hint_overrides"] = {
        "counties": {
            "Cook": {
                "case_type_name_hints": ["County-specific case type"],
                "documents": {
                    "RFAcomplaint": {
                        "filing_type_name_hints": ["County-specific complaint"]
                    }
                },
            }
        },
        "courts": {
            "First Municipal District": {
                "documents": {
                    "RFAcomplaint": {
                        "filing_component_name_hints": ["Court-specific lead"]
                    }
                }
            }
        },
    }
    payload = adapter.build_litefile_payload(data, "stable", bundle(tmp_path), "")
    assert payload["filing_hint_overrides"] == data["filing_hint_overrides"]


def test_prepared_cache_can_be_uploaded_repeatedly_without_regeneration(tmp_path):
    documents = bundle(tmp_path)
    al_bundle = Mock()
    al_bundle.enabled_documents.return_value = [Mock(instanceName="RFAcomplaint")]
    al_bundle.get_cacheable_documents.return_value = (
        [{"pdf": documents[0]["file"]}],
        None,
        None,
    )
    mapping = BLOCKS["litefile_document_map"]["data"]
    with patch.object(
        adapter, "send_litefile_handoff", return_value={"ok": True}
    ) as send:
        transfer = adapter.prepare_litefile_transfer(
            declared_data(), al_bundle, mapping, source_id="stable", return_url=""
        )
        send.assert_not_called()
        adapter.send_litefile_transfer(transfer, config=config())
        first = send.call_args.args[0]
        adapter.send_litefile_transfer(transfer, config=config())
        assert send.call_args.args[0] == first
        assert al_bundle.get_cacheable_documents.call_count == 1
        corrected = adapter.prepare_litefile_transfer(
            declared_data({"user_selected_county": "Changed"}),
            al_bundle,
            mapping,
            source_id="stable",
            return_url="",
        )
        adapter.send_litefile_transfer(
            corrected, config=config(), correction_token="correction"
        )
        assert al_bundle.get_cacheable_documents.call_count == 2
        assert send.call_args.args[0]["case"]["county"] == "Changed"
        assert send.call_args.args[0]["source_id"] == "stable"
    al_bundle.get_cacheable_documents.assert_called_with(
        key="final",
        pdf=True,
        docx=False,
        refresh=True,
        include_zip=False,
        include_full_pdf=False,
    )


def test_unmapped_enabled_document_does_not_get_guessed_metadata():
    al_bundle = Mock()
    al_bundle.enabled_documents.return_value = [Mock(instanceName="not_declared")]
    result = adapter.prepare_litefile_transfer(
        declared_data(), al_bundle, {}, source_id="stable", return_url=""
    )
    assert not result["ok"]
    al_bundle.get_cacheable_documents.assert_not_called()


def test_mismatched_cache_document_ids_are_recoverable():
    al_bundle = Mock()
    al_bundle.enabled_documents.return_value = [
        Mock(instanceName="RFAcomplaint"),
        Mock(instanceName="RFAaffidavit"),
    ]
    al_bundle.get_cacheable_documents.return_value = (
        [
            {"id": "RFAaffidavit", "pdf": Mock()},
            {"id": "RFAcomplaint", "pdf": Mock()},
        ],
        None,
        None,
    )
    mapping = BLOCKS["litefile_document_map"]["data"]
    result = adapter.prepare_litefile_transfer(
        declared_data(), al_bundle, mapping, source_id="stable", return_url=""
    )
    assert not result["ok"]
    assert "prepare" in result["message"]


def test_invalid_cached_document_is_recoverable():
    al_bundle = Mock()
    al_bundle.enabled_documents.return_value = [Mock(instanceName="RFAcomplaint")]
    al_bundle.get_cacheable_documents.return_value = ([{}], None, None)
    mapping = BLOCKS["litefile_document_map"]["data"]
    result = adapter.prepare_litefile_transfer(
        declared_data(), al_bundle, mapping, source_id="stable", return_url=""
    )
    assert not result["ok"]
    assert "prepare" in result["message"]


def test_document_resolution_exceptions_reach_docassemble():
    class DocumentResolutionNeeded(IndexError):
        pass

    al_bundle = Mock()
    al_bundle.enabled_documents.return_value = [Mock(instanceName="RFAcomplaint")]
    al_bundle.get_cacheable_documents.side_effect = DocumentResolutionNeeded()
    mapping = BLOCKS["litefile_document_map"]["data"]

    with pytest.raises(DocumentResolutionNeeded):
        adapter.prepare_litefile_transfer(
            declared_data(), al_bundle, mapping, source_id="stable", return_url=""
        )


def test_person_defaults_to_showifdef_and_accepts_keyword_override():
    fields = {"first_name": "name.first"}
    with patch.object(adapter, "showifdef", return_value="Taylor") as reader:
        assert adapter.litefile_person("users[0]", fields=fields) == {
            "first_name": "Taylor"
        }
        assert adapter.litefile_person("users[0]", fields=fields, known=None) == {
            "first_name": "Taylor"
        }
        assert reader.call_count == 2
        reader.assert_called_with("users[0].name.first")
        assert adapter.litefile_person("users[0]", fields=fields, known={}.get) == {}
        assert reader.call_count == 2


def test_person_default_fields_match_assemblyline_individual():
    answers = {
        "users[0].name.first": "Taylor",
        "users[0].name.last": "Example",
        "users[0].address.address": "100 Main Street",
        "users[0].phone_number": "802-555-0123",
    }
    assert adapter.litefile_person("users[0]", known=answers.get) == {
        "first_name": "Taylor",
        "last_name": "Example",
        "address_line_1": "100 Main Street",
        "phone": "802-555-0123",
    }


def test_background_flow_saves_cache_before_upload_and_reuses_it_on_retry():
    blocks = [*yaml.safe_load_all(SHARED_YAML.read_text()), *yaml.safe_load_all(YAML.read_text())]
    events = {
        block["event"]: block["code"]
        for block in blocks
        if block and "event" in block and "code" in block
    }
    pending_upload = next(
        block["code"]
        for block in blocks
        if block
        and block.get("initial") is True
        and "litefile_upload_pending" in block.get("code", "")
    )

    class Waiting(Exception):
        pass

    class ResultScreen(Exception):
        pass

    transfer = {"ok": True, "payload": {"source_id": "stable"}}
    result = {"ok": True, "draft_id": "42"}
    background = Mock()
    background.run.side_effect = [Waiting(), result, Waiting(), result]
    context = {
        "litefile_background": background,
        "reconsider": Mock(),
        "url_args": {},
        "litefile_transfers": {},
        "litefile_data": {},
        "litefile_transfer_id": "stable",
        "litefile_bundle": Mock(),
        "litefile_document_map": {},
        "litefile_config_name": "litefile",
        "litefile_upload_pending": False,
        "litefile_background_failure": {"ok": False},
        "interview_url": lambda: "https://interview.example/",
        "prepare_litefile_transfer": Mock(return_value=transfer),
        "force_ask": Mock(side_effect=ResultScreen),
    }
    with pytest.raises(Waiting):
        exec(events["litefile_send"], context)
    assert context["litefile_transfers"]["initial"] is transfer
    assert context["litefile_upload_pending"] is True
    context["prepare_litefile_transfer"].assert_called_once()
    with pytest.raises(ResultScreen):
        exec(pending_upload, context)
    assert context["litefile_result"] is result
    assert context["litefile_upload_pending"] is False
    with pytest.raises(Waiting):
        exec(events["litefile_send"], context)
    with pytest.raises(ResultScreen):
        exec(pending_upload, context)
    assert [call.args for call in background.run.call_args_list] == [
        ("litefile_upload",),
        ("litefile_upload",),
        ("litefile_upload",),
        ("litefile_upload",),
    ]


def test_upload_reads_selected_flat_server_configuration():
    event = next(
        block["code"]
        for block in [*yaml.safe_load_all(SHARED_YAML.read_text()), *yaml.safe_load_all(YAML.read_text())]
        if block and block.get("event") == "litefile_upload"
    )
    for name in ("litefile", "alternate_filing"):
        selected = config()
        context = {
            "litefile_transfer_key": "initial",
            "litefile_config_name": name,
            "url_args": {},
            "litefile_transfers": {"initial": {"payload": {}}},
            "get_config": Mock(return_value=selected),
            "send_litefile_transfer": Mock(return_value={"ok": True}),
            "background_response": Mock(),
        }
        exec(event, context)
        context["get_config"].assert_called_once_with(name, {})
        assert context["send_litefile_transfer"].call_args.kwargs["config"] is selected
