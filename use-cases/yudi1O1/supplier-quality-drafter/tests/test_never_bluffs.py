"""Regression tests for a real bug, caught by a real run.

SuperDocs returned a *completed* job whose response read "0 of 4 asked could be
completed". The document was never populated. The drafter printed
"Drafted: out/verify-final.docx", and would have recorded it in the idempotency
ledger as finished work — so the next run would have happily reused a document
that was never drafted.

A success message must only ever mean the output is genuinely in the state it
claims. These tests hold that line: the exact payload from that run is replayed
here, and the drafter must refuse to call it a success.
"""
from __future__ import annotations

import zipfile

import pytest

from supplier_quality_drafter.ledger import RunLedger
from supplier_quality_drafter.models import ActionItem, DraftRequest, FailureMode
from supplier_quality_drafter.verify import document_was_modified, expected_facts, verify_export
from supplier_quality_drafter.workflow import DraftNotApplied, DraftUnverified, draft_document

TEMPLATE_HTML = "<div data-chunk-id='c1'><h1>Acme</h1><p>[FMEA table goes here]</p></div>"

#: Verbatim shape of the response that exposed the bug.
REAL_NOOP_RESPONSE = (
    "⚠️ 0 of 4 asked could be completed.\n"
    "• Not done yet — Populate FMEA section\n"
    "• Failed: an edit — no matching sections were found to act on\n"
    "Notes: I wasn't able to make that change — none of the requested operations could be "
    "applied, and the document was not modified."
)


def _request() -> DraftRequest:
    return DraftRequest(
        document_type="fmea",
        item_or_process="Widget",
        customer_name="Customer Co",
        engineer_name="Engineer",
        failure_modes=[
            FailureMode(id="FM-01", function="f", failure_mode="fm", effect="e",
                        severity=7, occurrence=4, detection=3)
        ],
        actions=[ActionItem(id="AC-01", failure_mode_id="FM-01", recommended_action="fix",
                            target_date="2026-09-30")],
    )


class NoOpClient:
    """Replays the real 'completed but nothing happened' payload."""

    def __init__(self, succeed_on_attempt: int | None = None):
        self.calls: list[str] = []
        self.attempt = 0
        self.succeed_on_attempt = succeed_on_attempt
        self.usage = type("U", (), {"calls": 0, "summary": staticmethod(lambda: "usage")})()

    def upload_document(self, file_path, session_id, open_mode=None):
        self.calls.append("upload_document")
        return {"html": TEMPLATE_HTML}

    def chat_async(self, message, session_id, document_html=None, **extra):
        self.calls.append("chat_async")
        self.attempt += 1
        return {"job_id": f"job-{self.attempt}"}

    def poll_job(self, job_id, poll_interval=3.0, max_wait=900.0, on_poll=None):
        self.calls.append("poll_job")
        if self.succeed_on_attempt is not None and self.attempt >= self.succeed_on_attempt:
            return {
                "status": "completed",
                "result": {
                    "response": "Populated the FMEA table.",
                    "document_changes": {
                        "updated_html": TEMPLATE_HTML.replace(
                            "[FMEA table goes here]",
                            "<table><tr><td>FM-01</td><td>7</td><td>4</td><td>3</td><td>84</td></tr>"
                            "<tr><td>AC-01</td><td>2026-09-30</td></tr></table>",
                        )
                    },
                },
            }
        return {"status": "completed", "result": {"response": REAL_NOOP_RESPONSE}}

    def approve(self, *a, **k):
        self.calls.append("approve")
        return {}

    def continue_job(self, *a, **k):
        self.calls.append("continue_job")
        return {}

    def export_document(self, out_path, session_id=None, html=None, format="docx", options=None):
        self.calls.append("export_document")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("# Report\n\n| FM-01 | 7 | 4 | 3 | 84 |\n| AC-01 | 2026-09-30 |\n")
        return out_path


# --- the bug itself -----------------------------------------------------------

def test_a_completed_job_that_changed_nothing_is_not_reported_as_success(tmp_path):
    template = tmp_path / "t.html"
    template.write_text(TEMPLATE_HTML, encoding="utf-8")
    out = tmp_path / "out.md"
    client = NoOpClient()

    with pytest.raises(DraftNotApplied) as exc:
        draft_document(client, _request(), template_path=str(template), session_id="s1",
                       export_path=str(out), export_format="markdown")

    assert "changed nothing" in str(exc.value)
    assert "0 of 4" in str(exc.value)          # surfaces what SuperDocs actually said
    assert "export_document" not in client.calls  # never exported a document that was never drafted
    assert not out.exists()


def test_a_no_op_draft_is_never_recorded_in_the_ledger(tmp_path):
    """The severe half of the bug: recording a phantom draft would make the next
    run 'skip' and hand back a document that never existed."""
    template = tmp_path / "t.html"
    template.write_text(TEMPLATE_HTML, encoding="utf-8")
    out = tmp_path / "out.md"
    ledger = RunLedger(str(tmp_path / "ledger.json"))

    with pytest.raises(DraftNotApplied):
        draft_document(NoOpClient(), _request(), template_path=str(template), session_id="s1",
                       export_path=str(out), export_format="markdown", ledger=ledger)

    assert not (tmp_path / "ledger.json").exists() or ledger._load() == {}


def test_a_cold_session_that_settles_on_retry_succeeds(tmp_path):
    """The documented warm-up behavior: 'send it again and it settles.'"""
    template = tmp_path / "t.html"
    template.write_text(TEMPLATE_HTML, encoding="utf-8")
    out = tmp_path / "out.md"
    client = NoOpClient(succeed_on_attempt=2)

    result = draft_document(client, _request(), template_path=str(template), session_id="s1",
                            export_path=str(out), export_format="markdown")
    assert result.attempts == 2
    assert client.calls.count("chat_async") == 2
    assert out.exists()


def test_export_that_lacks_the_engineers_facts_is_not_called_a_success(tmp_path):
    """'Exported' is not 'correct'. If the file doesn't contain the data, say so."""
    template = tmp_path / "t.html"
    template.write_text(TEMPLATE_HTML, encoding="utf-8")
    out = tmp_path / "out.md"

    class EmptyExportClient(NoOpClient):
        def export_document(self, out_path, session_id=None, html=None, format="docx", options=None):
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("# Report\n\nnothing useful here\n")
            return out_path

    with pytest.raises(DraftUnverified) as exc:
        draft_document(EmptyExportClient(succeed_on_attempt=1), _request(), template_path=str(template),
                       session_id="s1", export_path=str(out), export_format="markdown")
    assert "FM-01" in str(exc.value)


# --- the primitives -----------------------------------------------------------

def test_document_was_modified_detects_the_no_op():
    assert document_was_modified({"response": REAL_NOOP_RESPONSE}, TEMPLATE_HTML) is False
    assert document_was_modified({"document_changes": {"updated_html": TEMPLATE_HTML}}, TEMPLATE_HTML) is False
    assert document_was_modified({"document_changes": {"updated_html": "<p>new</p>"}}, TEMPLATE_HTML) is True


def test_expected_facts_include_ids_and_computed_rpn_but_not_template_boilerplate():
    facts = expected_facts(_request())
    assert "FM-01" in facts and "AC-01" in facts
    assert "84" in facts          # the computed RPN
    assert "1-10" not in facts    # template instruction text, not data


def test_verification_reads_a_real_docx_without_extra_dependencies(tmp_path):
    """A .docx is a zip; its body XML is readable without python-docx."""
    path = tmp_path / "out.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml",
            "<w:document><w:t>FM-01</w:t><w:t>84</w:t><w:t>AC-01</w:t><w:t>2026-09-30</w:t></w:document>",
        )
    assert verify_export(str(path), _request()).ok


def test_verification_reports_unreadable_formats_honestly_instead_of_passing(tmp_path):
    path = tmp_path / "out.pdf"
    path.write_bytes(b"%PDF-1.4 ...")
    result = verify_export(str(path), _request())
    assert result.readable is False
    assert result.ok is False          # unverifiable is not the same as verified
    assert "verification skipped, not passed" in result.note
