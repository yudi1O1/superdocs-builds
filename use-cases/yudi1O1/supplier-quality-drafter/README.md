# Supplier-Quality Doc Drafter (PPAP / 8D / FMEA)

Built for the SuperDocs engineer task (Task 2, assigned build). Credit: **Uddeshya** (GitHub [@yudi1O1](https://github.com/yudi1O1)).

A drafting tool for supplier-quality engineers in automotive/manufacturing. It takes structured,
engineer-supplied data — failure modes, ratings, corrective actions, PPAP and 8D narrative fields —
and drafts the corresponding document **onto the customer's own template**, using the SuperDocs API.

Its centerpiece is the FMEA (Failure Mode and Effects Analysis) table: function, failure mode, effect,
Severity, Occurrence, Detection, and the computed Risk Priority Number (RPN = S×O×D). It also drafts
the narrative sections of a PPAP (Production Part Approval Process) submission and an 8D
(problem-solving / corrective-action) report.

## The one rule this tool is built around

> Numbers the engineer did not supply are asked for, never invented.

Every Severity/Occurrence/Detection rating and every RPN in the final document is computed in plain
Python from numbers the engineer typed in — **never by the model**. If a rating is missing, the tool
refuses to draft and tells you exactly which field is missing, in which failure mode, instead of
silently guessing a plausible-looking number. See [`validate.py`](supplier_quality_drafter/validate.py)
and [`render.py`](supplier_quality_drafter/render.py) — `render.py` renders the RPN table as a literal
HTML block with numbers already filled in, and the chat instruction explicitly tells the AI not to
recalculate or alter any number in it, only to fit it into the template's structure and formatting.

Try it yourself: `examples/sample_input.yaml` has one failure mode (`FM-03`) with a deliberately
missing `detection` rating.

```bash
python -m supplier_quality_drafter check examples/sample_input.yaml
```

```
[BLOCKING] failure_mode FM-03: detection was not supplied. Ask the engineer for a 1-10 detection
rating instead of drafting a number for it.

1 blocking finding(s) — fix these before drafting.
```

Fill in the rating and it passes clean.

## What "strong" looks like here, and how this build demonstrates it

The task card names two bars:

1. **"The severity-occurrence-detection table is arithmetically consistent (priority values derive
   from their factors) and every action row traces to a named failure mode."**
   Enforced structurally, not by convention:
   - `validate.py` refuses to draft if an action's `failure_mode_id` doesn't match a real failure mode
     (`test_action_must_reference_existing_failure_mode`).
   - `render.py` computes RPN as `severity * occurrence * detection` in Python and never lets the model
     touch that arithmetic (`test_rpn_is_arithmetically_consistent_in_rendered_table`).
   - A failure mode with a high RPN (≥100) and no linked action gets flagged as a warning, so a risky
     row can't silently fall through with no corrective action.

2. **"The same inputs re-drafted onto a second customer template change presentation only."**
   `templates/customer_template_a.html` (Acme Automotive Systems' report format) and
   `templates/customer_template_b.html` (Meridian Powertrain Co.'s, a differently structured,
   differently styled template) are both synthetic, fictional-customer templates included in this repo.
   `scripts/demo_two_templates.py` drafts the *same* `examples/sample_input.yaml` onto both and asserts
   that every failure-mode ID, action ID, and numeric value (S/O/D/RPN) appears identically in both
   exports — only the surrounding structure and formatting differ. This script needs a live API key
   (it spends real operations); the same logic's *control flow* is covered without a key in
   `tests/test_workflow.py`.

## How it uses the SuperDocs API — the four-call contract

Per document, one session, four calls:

1. **Upload** — `POST /v1/documents/upload`: the customer's own template becomes the active document.
2. **Chat** — `POST /v1/chat/async` with `approval_mode: "ask_every_time"`: sends one instruction plus
   a literal, pre-computed content block (the FMEA table, PPAP narrative, or 8D narrative — see above).
3. **Approve** — `POST /v1/chat/{session_id}/approve`: every proposed change is surfaced to a human
   before it lands (see [Human-in-the-loop](#human-in-the-loop-gate) below).
4. **Export** — `POST /v1/documents/export`: the approved result, as `.docx`/`.pdf`/`.md`/etc.

See [`client.py`](supplier_quality_drafter/client.py) for the thin REST wrapper and
[`workflow.py`](supplier_quality_drafter/workflow.py) for the orchestration.

### Human-in-the-loop gate

Every draft runs in `approval_mode: "ask_every_time"` — the AI's proposed changes are never applied
sight-unseen. `python -m supplier_quality_drafter draft` (without `--auto-approve`) prints each proposed
change (old/new HTML, the AI's stated reason) and prompts before approving it. `--auto-approve` skips
the prompt for CI/demo runs — it still goes through the same approve endpoint, it just answers yes to
everything automatically. Rejecting a change doesn't discard the rest of the batch (the `changes[]`
array carries per-change decisions).

## Setup

```bash
cd supplier-quality-drafter
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
export SUPERDOCS_API_KEY=sk_your_key_here            # get one at use.superdocs.app -> Settings -> API Keys
```

## Usage

**1. Validate structured input without touching the network:**

```bash
python -m supplier_quality_drafter check examples/sample_input.yaml
```

**2. Draft the document onto a customer template (interactive approval):**

```bash
python -m supplier_quality_drafter draft examples/sample_input.yaml \
  --template templates/customer_template_a.html \
  --session-id acme-ba204-fmea \
  --out out/acme-ba204.docx
```

**3. Same thing, non-interactively (for a demo run):**

```bash
python -m supplier_quality_drafter draft examples/sample_input.yaml \
  --template templates/customer_template_a.html \
  --session-id acme-ba204-fmea \
  --out out/acme-ba204.docx \
  --auto-approve
```

**4. Prove content parity across two customer templates (spends real operations):**

```bash
python scripts/demo_two_templates.py
```

## Input format

See [`examples/sample_input.yaml`](examples/sample_input.yaml) for a fully worked, synthetic example
(fictional supplier, fictional customer — safe to commit and demo with). `document_type` is one of
`fmea`, `ppap`, `8d`, or `combined`. Failure modes, actions, PPAP fields, and 8D fields map directly
to the dataclasses in [`models.py`](supplier_quality_drafter/models.py).

## Live demo run

This is genuine output from a real SuperDocs API call (`python -m supplier_quality_drafter draft`,
`session-id demo-acme-ba204-v1`, template A, `docx` export — 38.8 KB file produced), not a mockup.
The RPN column below was computed in `render.py`, not by the model — compare against `S × O × D` and
you'll see it checks out for every row:

| ID | Severity (S) | Occurrence (O) | Detection (D) | RPN (S×O×D) |
| --- | --- | --- | --- | --- |
| FM-01 | 7 | 4 | 3 | **84** |
| FM-02 | 9 | 2 | 4 | **72** |
| FM-03 | 3 | 5 | 6 | **90** |

`scripts/demo_two_templates.py`, run live against templates A and B with this same input, confirmed:

```
OK: all 27 data-derived facts (IDs, S/O/D ratings, computed RPNs, dates, narrative fields) appear
verbatim in both outputs. The two documents differ only in the surrounding template structure and
wording — content parity holds.
```

A real bug this live run caught and fixed: the CLI crashed on Windows (`UnicodeEncodeError`) when the
AI's response text contained an emoji the default `cp1252` console codepage couldn't encode — the
`.docx` had already exported successfully by that point, only the summary print failed. Fixed by
reconfiguring stdout/stderr to UTF-8 at CLI startup (see `cli.py`). Worth flagging as a general
integration note for anyone building a SuperDocs CLI for Windows: the AI's `response` text is
UTF-8/emoji-capable and your output stream needs to be too.

## Tests (no live key required)

```bash
pip install -r requirements.txt   # includes pytest
python -m pytest tests/ -q
```

21 tests, all offline:

- `test_validate.py` — the "never invent a number" gate: missing ratings are blocking findings, not
  silently defaulted; out-of-range ratings are rejected; actions must trace to a real failure mode;
  duplicate failure-mode IDs are caught; a high-RPN row with no linked action produces a warning.
- `test_render.py` — RPN arithmetic in the rendered table matches `S × O × D` exactly; a missing
  rating renders as `TBD`, never as a plausible-looking number; free-text fields are HTML-escaped
  (a failure-mode description containing `<script>` cannot inject markup into the instruction sent to
  the API — see *Content safety* below).
- `test_workflow.py` — the four-call contract, the human-in-the-loop approval loop (including multiple
  rounds of `awaiting_approval` and a custom callback that rejects a change), and that
  `draft_document` refuses to make **any** network call when validation fails.

Every network-touching call is behind a duck-typed `SuperDocsClient` interface, so `test_workflow.py`
exercises the full orchestration logic against a `FakeClient` — no mocking library, no live key,
no cost.

## Content safety: this tool doesn't take orders from the documents it edits

Free-text fields (failure-mode descriptions, effects, narrative sections) come from the engineer's own
YAML input, not from an untrusted document — but they're still escaped before being embedded in the
HTML content block (`test_html_is_escaped_to_avoid_injection_from_free_text_fields`), so a stray `<` or
`&` in someone's free-text description can't be misread as markup or, in a future version that pulls
fields from an uploaded source document, as an embedded instruction.

## Known limitations (logged honestly)

- **Template matching is structural, not by ID.** The SuperDocs API doesn't expose a `template_id`
  parameter on `/v1/chat`; it searches your uploaded template library by relevance. To get deterministic
  "use exactly this customer's template" behavior, this tool uploads the customer's template as the
  *active document* (`/v1/documents/upload`) and asks the AI to populate it in place, rather than
  relying on template search. This is why `templates/upload` isn't in the four-call path here — the
  template *is* the starting document for the session.
- **PDF/DOCX customer templates work too**, but the two included demo templates are `.html` for
  readability in a PR diff. `SuperDocsClient.upload_document` takes any of DOCX/DOC/ODT/PDF/TXT/HTML/MD/RTF.
- **No retry/resume logic for a killed process.** This build's card scopes it to `chat, templates,
  export` and doesn't ask for crash-resumability (that requirement belongs to Task 1's agentic system,
  not this build). `poll_job` has a timeout and raises cleanly on `failed`/timeout rather than hanging.
- **`--auto-approve` is for CI/demo runs only.** It still goes through the real approve endpoint (it's
  not a bypass), but production use of this tool should default to interactive review, per the task's
  human-in-the-loop framing.

## Architecture

```
YAML input --> models.py (typed) --> validate.py (blocking gate, no network)
                                            |
                                            v (only if clean)
                              render.py (compute RPN, build HTML content block)
                                            |
                                            v
   customer template --> upload --> chat (async, ask_every_time) --> approve (human gate) --> export
                                            ^
                                    workflow.py orchestrates all four calls
```
