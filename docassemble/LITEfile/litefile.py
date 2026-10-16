"""Send known interview facts and generated PDFs to LITEFile.

This module uses Docassemble's showifdef reader and no EFSP integration classes.
The interview declares its facts, person fields, and document hints in YAML.
"""

import hashlib
import json
import logging
import uuid
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import requests
from docassemble.base.functions import showifdef
from typing import Any


logger = logging.getLogger(__name__)


DEFAULT_LITEFILE_PERSON_FIELDS = {
    "first_name": "name.first",
    "middle_name": "name.middle",
    "last_name": "name.last",
    "suffix": "name.suffix",
    "address_line_1": "address.address",
    "address_line_2": "address.unit",
    "city": "address.city",
    "state": "address.state",
    "zip_code": "address.zip",
    "country": "address.country",
    "email": "email",
    "phone": "phone_number",
}


def litefile_source_id():
    """Generate a unique identifier for a LITEFile transfer.

    Returns:
        str: A UUID string suitable for use as a transfer source and
            idempotency identifier.
    """
    return str(uuid.uuid4())


def litefile_person(
    source: str, *, fields: dict[str, str] | None = None, known: Any = None
):
    """Read available fields for a person from the interview state.

    Empty and missing values are omitted from the returned mapping. The
    default field mapping matches the standard AssemblyLine ``ALIndividual``
    fields, but callers can provide a smaller or customized mapping.

    Args:
        source: Expression identifying the person, such as ``"users[0]"``.
        fields: Mapping from LITEFile field names to suffixes passed to the
            reader. If omitted, the standard AssemblyLine person fields are
            used.
        known: Optional replacement for ``showifdef``. This is useful for
            tests or callers that provide their own interview-state reader.

    Returns:
        dict[str, str]: The declared fields whose values are neither ``None``
        nor an empty string.
    """
    if known is None:
        known = showifdef
    if fields is None:
        fields = DEFAULT_LITEFILE_PERSON_FIELDS
    return {
        field: str(answer)
        for field, suffix in fields.items()
        if (answer := known(f"{source}.{suffix}")) not in (None, "")
    }


def build_litefile_payload(
    data: dict, source_id: str, documents: list, return_url: str
) -> dict:
    """Build the serialized payload for a LITEFile handoff.

    Person data nested under ``person`` is flattened into each party, null
    known filing facts are removed, and each document receives a SHA-256 hash
    calculated from its PDF bytes. The original input mapping is not mutated.

    Args:
        data: Facts and filing hints declared by the interview YAML.
        source_id: Stable identifier for this transfer and its retries.
        documents: Document metadata mappings. Each mapping must contain a
            ``file`` object with a ``path()`` method returning the PDF path.
        return_url: URL to which LITEFile should return the user.

    Returns:
        dict: A LITEFile payload containing transfer metadata, document
        manifests, and the supplied interview data.
    """
    from copy import deepcopy

    payload = deepcopy(data)
    # The YAML groups each person's selected attributes for readability.
    payload["parties"] = [
        {
            **{key: value for key, value in party.items() if key != "person"},
            **party["person"],
        }
        for party in payload.get("parties", [])
        if party.get("person")
    ]
    payload["known_filing_facts"] = {
        key: value
        for key, value in payload.get("known_filing_facts", {}).items()
        if value is not None
    }
    manifest = []
    for document in documents:
        with open(document["file"].path(), "rb") as pdf:
            digest = hashlib.sha256()
            for chunk in iter(lambda: pdf.read(65536), b""):
                digest.update(chunk)
        manifest.append(
            {
                **{
                    key: value
                    for key, value in document.items()
                    if key not in ("file", "path")
                },
                "sha256": digest.hexdigest(),
            }
        )
    payload.update(
        source_id=source_id,
        idempotency_key=source_id,
        documents=manifest,
        return_url=return_url,
    )
    return payload


def prepare_litefile_transfer(
    data: dict, bundle, document_map, *, source_id: str, return_url: str
) -> dict:
    """Prepare an AssemblyLine cache for the foreground interview to persist.

    This does not contact LITEFile. Save the returned cache before uploading it,
    so a failed upload can reuse exactly the same payload and PDF bytes.

    Args:
        data: Facts and filing hints declared by the interview YAML.
        bundle: AssemblyLine document bundle used to find enabled documents and
            generate their final PDFs.
        document_map: Mapping from document instance names to LITEFile
            document metadata.
        source_id: Stable identifier for this transfer and its retries.
        return_url: URL to which LITEFile should return the user.

    Returns:
        dict: A successful result containing ``cache``, ``documents``, and
        ``payload``; or an unsuccessful result with ``ok`` set to ``False``
        and a user-facing ``message`` when document preparation cannot proceed.
    """
    failure = {
        "ok": False,
        "message": "We could not prepare these forms. You can still download them.",
    }
    try:
        enabled = bundle.enabled_documents()
        ids = [str(document.instanceName) for document in enabled]
        if len(ids) != len(set(ids)):
            return failure
        if any(document_id not in document_map for document_id in ids):
            return {
                "ok": False,
                "message": "A court document is missing its LITEFile mapping. You can still download your forms.",
            }
        cache = bundle.get_cacheable_documents(
            key="final",
            pdf=True,
            docx=False,
            refresh=True,
            include_zip=False,
            include_full_pdf=False,
        )
        cached_documents = cache[0]
        if len(cached_documents) != len(ids):
            return {
                "ok": False,
                "message": "The court document list changed. Please try again.",
            }
        if any(
            not isinstance(item, dict) or "pdf" not in item for item in cached_documents
        ):
            return failure
        cache_ids = [
            item.get("id", item.get("instanceName")) for item in cached_documents
        ]
        if any(cache_id is not None for cache_id in cache_ids):
            if [
                str(cache_id) if cache_id is not None else None
                for cache_id in cache_ids
            ] != ids:
                return failure
        documents = [
            {**document_map[document_id], "id": document_id, "file": item["pdf"]}
            for document_id, item in zip(ids, cached_documents)
        ]
        return {
            "ok": True,
            "cache": cache,
            "documents": documents,
            "payload": build_litefile_payload(data, source_id, documents, return_url),
        }
    except (OSError, KeyError, TypeError, ValueError):
        logger.exception("Could not prepare the LITEFile document transfer")
        return failure


def send_litefile_transfer(transfer: dict, *, config: dict, correction_token: str = ""):
    """Upload a previously saved transfer without regenerating its PDFs.

    Args:
        transfer: Result returned by ``prepare_litefile_transfer``.
        config: LITEFile connection configuration containing ``base_url``,
            ``source``, and ``token``.
        correction_token: Optional token for replacing documents in an
            existing handoff.

    Returns:
        dict: The successful handoff receipt or a recoverable failure result.
    """
    documents = [
        {**document, "path": document["file"].path()}
        for document in transfer["documents"]
    ]
    return send_litefile_handoff(
        transfer["payload"], documents, config, correction_token
    )


def send_litefile_handoff(payload, documents, config, correction_token=""):
    """Send a payload and its PDFs to LITEFile.

    Retries preserve the payload's idempotency key. A correction upload uses a
    distinct deterministic key derived from the payload and correction token.
    Invalid configuration, failed requests, malformed receipts, and
    unexpected continuation origins are returned as recoverable failures.

    Args:
        payload: Serialized LITEFile handoff payload.
        documents: Document mappings containing ``id`` and local PDF ``path``
            values.
        config: LITEFile connection configuration. HTTPS is required unless
            ``allow_insecure_local_development`` is explicitly true for an
            HTTP local-development URL.
        correction_token: Optional token for replacing documents in an
            existing handoff.

    Returns:
        dict: A result with ``ok`` set to ``True`` and handoff receipt fields
        on success, or ``ok`` set to ``False`` and a user-facing ``message``
        on failure.
    """
    base_url = str(config.get("base_url", "")).rstrip("/")
    source = config.get("source", "")
    token = config.get("token", "")
    parsed = urlsplit(base_url)
    # HTTP is an explicit local-development option, never the production default.
    if (
        not source
        or not token
        or not parsed.netloc
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and config.get("allow_insecure_local_development") is True
            )
        )
    ):
        return {
            "ok": False,
            "message": "Electronic filing is not configured. You can still download your forms.",
        }
    outgoing = dict(payload)
    headers = {"Authorization": f"Bearer {token}", "X-LITEFile-Source": source}
    endpoint = "/api/handoffs/v1/"
    if correction_token:
        endpoint += "documents/"
        headers["X-LITEFile-Correction"] = correction_token
        material = json.dumps(payload, sort_keys=True) + correction_token
        outgoing["idempotency_key"] = hashlib.sha256(material.encode()).hexdigest()
    try:
        with ExitStack() as stack:
            files = {
                doc["id"]: (
                    Path(doc["path"]).name,
                    stack.enter_context(open(doc["path"], "rb")),
                    "application/pdf",
                )
                for doc in documents
            }
            response = requests.post(
                base_url + endpoint,
                headers=headers,
                data={"payload": json.dumps(outgoing)},
                files=files,
                timeout=(10, 120),
                allow_redirects=False,
            )
        if response.status_code not in (200, 201):
            return {
                "ok": False,
                "message": "LITEFile could not receive these forms. Try again, or download them below. If you already sent this draft and changed a PDF, open LITEFile and use the return-to-interview link.",
            }
        receipt = response.json()
        if not isinstance(receipt, dict):
            raise ValueError("Malformed handoff receipt")
        continue_url = receipt.get("continue_url", "")
        if not isinstance(continue_url, str):
            raise ValueError("Malformed continuation URL")
        continuation = urlsplit(continue_url)
        if (continuation.scheme, continuation.netloc) != (parsed.scheme, parsed.netloc):
            raise ValueError("Unexpected continuation origin")
        if not receipt.get("draft_id") or not continuation.path.startswith("/handoff/"):
            raise ValueError("Incomplete handoff receipt")
        return {
            "ok": True,
            **{key: receipt[key] for key in ("draft_id", "state", "continue_url")},
        }
    except (requests.RequestException, OSError, ValueError, KeyError):
        return {
            "ok": False,
            "message": "We could not confirm the transfer. Try again to continue the same draft. Your forms are still available to download.",
        }
