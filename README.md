# Docassemble support for LITEfile

Reusable interview actions and Python helpers for sending generated court PDFs
and known interview answers to [LITEfile](https://github.com/SuffolkLITLab/LITEFile).
A successful handoff saves a draft. The filer still signs in to LITEfile,
checks every document and filing detail, completes fees, and submits to court.

This is an include package, not a standalone legal interview. Install it on a
Docassemble server alongside AssemblyLine. Court forms, questions, jurisdiction,
party roles, and document mappings stay in the interview that uses it.

## Install

Install this GitHub repository with Docassemble's package manager, or add
`docassemble.LITEfile @ git+https://github.com/SuffolkLITLab/docassemble-LITEfile.git@v0.1.0`
to your interview package's dependencies. AssemblyLine and requests are declared
dependencies. The package was scaffolded with `dacreate LITEfile`.

## Include in an interview

After your AssemblyLine include, include the shared actions:

```yaml
include:
  - docassemble.LITEfile:litefile.yml
---
code: |
  litefile_download_event = "my_download_screen"
---
variable name: litefile_data
data from code:
  schema_version: 1
  jurisdiction: '"vermont"'
  filing_intent: '"relief_from_abuse"'
  case_type_name_hints: '["Relief from Abuse"]'
  case_category_name_hints: '["Family"]'
  case:
    existing_case: 'False'
    court_name: showifdef("trial_court.name")
  filer: litefile_person("users[0]")
  parties:
    - source: '"users[0]"'
      person: litefile_person("users[0]")
      semantic_role: '"plaintiff"'
      case_side_hint: '"plaintiff"'
      is_self: 'True'
      is_filing_party: 'True'
    - source: '"other_parties[0]"'
      person: litefile_person("other_parties[0]")
      semantic_role: '"defendant"'
      case_side_hint: '"defendant"'
---
variable name: litefile_document_map
data:
  my_complaint:
    role: lead
    form_name: Complaint
    filing_type_name_hints: [Initial Filing]
    document_type_name_hints: []
    filing_component_name_hints: [Lead Document]
```

This example's Vermont names belong to that example, not the shared package.
Replace them for your jurisdiction and interview. Add every enabled court
PDF to the map, using its stable ALDocument variable name. A missing mapping
fails the handoff instead of silently omitting a court document. Exclude
instructions from the court bundle. No numeric court codes belong here:
they can change without warning and differ between staging and production.
LITEfile resolves names from the target environment's live catalog.

Display `${ litefile_continue_button }` in your download screen's subquestion.
The button is hidden unless the selected server configuration enables it.
The default bundle is `al_court_bundle` and the default return event is
`al_download`. Override `litefile_bundle` and `litefile_download_event` in later
code blocks if needed. Later blocks can also override the empty
`litefile_filing_hint_overrides` county/court mapping or select another flat
server configuration with `litefile_config_name = "another_litefile_config"`.

`litefile_person("users[0]")` reads existing AssemblyLine person attributes
without gathering missing answers. An optional `fields` mapping adapts custom
person objects. Unknown filing details remain for LITEfile to ask the filer.

## Configure the servers

In the Docassemble server configuration (not an interview or Git repository):

```yaml
litefile:
  enabled: true
  base_url: https://YOUR-LITEFILE-HOST
  source: your-interview-server
  token: YOUR-SOURCE-SECRET
```

The base URL is the LITEfile application, not the EFSP API. Configure the same
source and secret on that LITEfile server:

```text
LITEFILE_HANDOFF_SOURCES={"your-interview-server":{"token":"YOUR-SOURCE-SECRET","jurisdictions":["vermont"],"return_origins":["https://YOUR-DOCASSEMBLE-HOST"]}}
```

Keep existing source entries when adding one. Reload web and background workers
after configuration changes. Use separate staging and production credentials.
The default `litefile_config_name` is `litefile`; older nested `litefile.rfa`
settings must be flattened. For local testing only, both sides can explicitly
allow `allow_insecure_local_development: true`; the receiver also requires DEBUG.
Never enable that exception on a hosted server.

LITEfile needs private S3 storage reachable by the EFSP and its PDF preparation
service. Local actual-document tests must set `EFSP_TEST_DOCUMENT_URL=""` so the
development sample PDF does not replace each outgoing document. Review each
prepared PDF before submitting. A handoff receipt is not a court filing, and
`submitted` is not clerk acceptance.

## Transfer behavior

The shared `litefile_send` action prepares the final enabled PDFs in the
foreground, persists AssemblyLine's document cache, and uploads in a background
action. Retrying the same handoff reuses its identity, manifest, and bytes.
Server credentials are read inside the worker and never saved in interview
variables or passed as task arguments. A correction token in the return URL
selects a separate cached transfer and the receiver's document-replacement API.

The transport helper validates receipt origins, preserves stable document IDs,
and shows recoverable errors without hiding the original downloads. Do not
replace court documents with a test sample to demonstrate successful delivery.

## Development and attribution

Tests exercise the transport and shared YAML without a running court service.
Run `python -m pytest -q` in an environment with Docassemble base, requests,
PyYAML, and pytest installed. Run the complete suite with
`python -I -m pytest -q`. All LITEfile transport and workflow tests live here,
including regressions using a captured RFA settings fixture. That fixture is
an example, not a live test of the RFA package. Runtime form settings remain
in the host interview.

See [Adapting an interview for LITEfile](docs/interview-integration.md) for
field mappings, court-specific hints, background actions, and correction flows.

Extracted from [RFApackage PR #426](https://github.com/LSVermont/docassemble-RFApackage/pull/426).
The original MIT attribution is preserved in LICENSE. No court account,
source token, or server configuration is included in this repository.
