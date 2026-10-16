# Adapting an interview for LITEfile

Include `docassemble.LITEfile:litefile.yml` after AssemblyLine and display `${ litefile_continue_button }`
on the interview's download screen. All interview-specific customization lives
in the host interview’s own YAML settings. The shared actions and Python transport helper come from
[docassemble.LITEfile](https://github.com/SuffolkLITLab/docassemble-LITEfile),
declared as a dependency of the host package. RFA is one example: its local
`litefile.yml` includes the shared YAML and contains only RFA settings. Keep
form-specific settings in the host package; do not copy shared modules or events.

## Adapt the YAML to another interview

The two data blocks show what leaves the interview:

- `litefile_data` uses Docassemble's `data from code` syntax for case facts,
  the filer, parties, representation facts, and semantic classification hints.
  Change the jurisdiction, intent, variable paths, and party roles here.
  `litefile_person(source)` uses the normal AssemblyLine `ALIndividual` and
  Docassemble `Individual` name, address, email, and phone attributes. An
  adaptation only supplies `fields={...}` when its object differs. Tests or
  other callers can override the reader with `known=...`.
  `showifdef()` reads only existing answers, so missing facts do not trigger
  filing questions in the legal interview. Its alternative value preserves
  `False` and zero while allowing unknown facts to be omitted.
- `litefile_document_map` maps ALDocument variable names to stable document
  IDs, lead/supporting roles, form names, filing-type hints, document-type hints,
  and filing-component hints. Only enabled documents in `al_court_bundle` are
  sent. The RFA next-steps document is outside that court bundle.

For example, the service form's customization is ordinary YAML:

```yaml
RFAserviceinfo:
  role: supporting
  form_name: Protection Order Service Info
  filing_type_name_hints:
    - 'Protection Order Service Information DPS #132'
  document_type_name_hints: []
  filing_component_name_hints:
    - Lead Document
```

Hints are readable names, not Tyler codes. Empty lists leave the choice to the
filer. LITEFile resolves live metadata and remains responsible for optional
services, fees, payment, validation, and submission. A `supporting` PDF's filing
component can still be `Lead Document`: those describe different concepts.

When names vary by filing location, populate `litefile_filing_hint_overrides`.
County hints replace general hints, then a matching court replaces the county's
values field by field. Document entries use the same stable IDs as
`litefile_document_map`:

```yaml
variable name: litefile_filing_hint_overrides
data:
  counties:
    Cook:
      case_type_name_hints:
        - County-specific case type
      documents:
        complaint:
          filing_type_name_hints:
            - County-specific complaint
  courts:
    First Municipal District:
      documents:
        complaint:
          filing_component_name_hints:
            - Court-specific lead document
```

Keys are compared as normalized names. A county key may include or omit the
word `County`. General hints remain in effect for fields a matching override
does not mention.

The adult/minor RFA case-type condition is visible in `litefile_data`.
No Python code chooses Vermont courts, RFA forms, party roles, or case types.
The reusable `litefile_send` and `litefile_upload` events use only generic
`litefile_*` variables. Authors normally edit the data blocks
in their host interview and leave the shared package's events unchanged. The default
`litefile_bundle` is `al_court_bundle` and the default download event is
`al_download`; the RFA adaptation overrides the latter in a later block.

To customize defaults, add blocks **after** the include. Docassemble's later
blocks take precedence, including for data blocks and events:

```yaml
include:
  - docassemble.LITEfile:litefile.yml
---
code: |
  litefile_bundle = my_court_bundle
---
code: |
  litefile_download_event = "my_interview_download"
```

Replace `litefile_data` and `litefile_document_map` with later data blocks for
your interview. You may similarly override `litefile_send` for a custom flow,
but ordinary adaptations do not need to. No extra module import is needed.

## Configure the servers

In Docassemble's server configuration:

```yaml
litefile:
  enabled: true
  base_url: https://litefile.example.org
  source: court-interviews
  token: <a server-to-server secret>
```

This is one configuration per Docassemble server, shared by its interviews.
Production and staging set their own endpoint and credentials. There is no
per-interview nesting. The source name identifies the sending application;
filing intent and jurisdiction belong in the interview data.

If an interview needs a different top-level server configuration, add:

```yaml
code: |
  litefile_config_name = "alternate_filing"
```

Its default is `"litefile"`. The background upload reads the selected server
configuration directly, so credentials are not copied into interview variables
or background-action arguments. Existing servers should move the old
`litefile.rfa` contents up one level to `litefile`.

Configure the matching source in LITEFile's `LITEFILE_HANDOFF_SOURCES`, allowing
the jurisdiction and this interview server's HTTPS return origin. See
[LITEFile's handoff contract](https://litefile-docs.suffolklitlab.org/docs/partners-courts/interview-integration).
The transfer saves an editable draft; it does not file anything with the court.

## Background transfer and retries

The default upload uses Docassemble's
[BackgroundAction](https://docassemble.org/docs/objects.html#BackgroundAction)
with its waiting spinner. The foreground event prepares the AssemblyLine cache;
`BackgroundAction`'s initial wait persists that cache before the upload worker
reads it. Background events read the saved interview state directly, keeping
PII, credentials, and correction tokens out of logged background-job arguments.
An initial routing block consumes the result before the host interview's
mandatory blocks run, then displays the ordinary success/retry screen.
Authors do not need to write polling JavaScript or copy background event code.

Do not call `litefile_send` as a background action itself: it prepares the cache
and coordinates the upload object. Invoke it with `url_action('litefile_send')`
or use the included button template.


The helper uses AssemblyLine's `get_cacheable_documents()` and retains its
cache, including its `DAFile` handles, with the payload for each transfer.
There are no separate frozen PDF copies. Retries reuse the same cache, payload,
source identity, and hashes; file paths are resolved again from the handles.
This avoids timestamp changes from regenerating a PDF on each retry.

For PDF corrections, start from LITEFile's **Return to my interview to correct a
PDF** button. Edit the answers, then choose **Continue in LITEFile**. A new scoped
correction token creates a new AssemblyLine cache and transfers replacement
PDFs into the same editable LITEFile draft. Retries of that transfer reuse its
cache. Other filing choices remain in LITEFile. Cached files follow the normal
Docassemble session file-retention lifecycle.

## Verify an adaptation

Use a Python environment with Docassemble installed and run
`python -I -m pytest -q` from the support package checkout. Isolated mode avoids the local
legacy namespace package shadowing the installed Docassemble package.
The tests read the shared YAML and a captured RFA settings example, cover
missing facts and adult/minor hints, and verify cache reuse and correction
behavior. The fixture is not a live check of the RFA package; its runtime
form-specific settings remain in that repository. Also exercise the installed interview, because document
enabling and attachment generation depend on the interview's own logic.

For isolated local testing, both configurations can explicitly enable
`allow_insecure_local_development: true`; the receiver also requires Django
debug mode. Production uses HTTPS. Keep source secrets and test session links
out of Git.
