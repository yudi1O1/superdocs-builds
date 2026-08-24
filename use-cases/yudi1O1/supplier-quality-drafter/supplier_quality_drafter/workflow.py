"""End-to-end draft workflow: template -> chat -> human approval gate -> export.

This is the orchestration layer. It never talks numbers itself — by the time
it runs, `validate.py` has already refused to proceed if anything was missing,
and `render.py` has already turned the engineer's numbers into a literal HTML
block. This module's only job is the four-call contract: upload, chat,
approve, export.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .client import SuperDocsClient
from .models import DraftRequest
from .render import build_instruction
from .validate import validate_request

ApprovalCallback = Callable[[list[dict]], list[dict]]
"""Given the list of pending_changes (as dicts), return the list of
{"change_id": ..., "approved": bool} decisions. The CLI's default
implementation prints a diff and prompts on stdin; tests pass a stub that
approves everything so the flow can be exercised without a human."""


def approve_all(pending_changes: list[dict]) -> list[dict]:
    return [{"change_id": c["change_id"], "approved": True} for c in pending_changes]


@dataclass
class DraftResult:
    session_id: str
    job_id: str
    exported_path: str
    ai_response: str


def draft_document(
    client: SuperDocsClient,
    req: DraftRequest,
    template_path: str,
    session_id: str,
    export_path: str,
    export_format: str = "docx",
    approval_callback: ApprovalCallback = approve_all,
    model_tier: Optional[str] = None,
) -> DraftResult:
    validation = validate_request(req)
    if not validation.ok_to_draft:
        lines = "\n".join(f"  - [{f.location}] {f.message}" for f in validation.blocking)
        raise ValueError(
            f"Refusing to draft: {len(validation.blocking)} blocking finding(s) — "
            f"ask the engineer for these before drafting, don't guess:\n{lines}"
        )

    # 1. Upload the customer's own template as the active document for this session.
    upload = client.upload_document(template_path, session_id=session_id)
    doc_html = upload["html"]

    # 2. Send the drafting instruction as an async HITL request.
    instruction = build_instruction(req)
    extra = {"approval_mode": "ask_every_time"}
    if model_tier:
        extra["model_tier"] = model_tier
    started = client.chat_async(instruction, session_id=session_id, document_html=doc_html, **extra)
    job_id = started["job_id"]

    job = client.poll_job(job_id)

    ai_response = ""
    # 3. Human-in-the-loop gate — resolve every awaiting_approval round.
    while job.get("status") == "awaiting_approval":
        metadata = job.get("metadata", {})
        kind = metadata.get("awaiting_kind", "change_review")
        if kind == "continue_prompt":
            job = client.continue_job(session_id, job_id, do_continue=True)
            job = client.poll_job(job_id)
            continue

        pending = metadata.get("pending_changes", [])
        decisions = approval_callback(pending)
        client.approve(session_id, job_id, approved=True, changes=decisions)
        job = client.poll_job(job_id)

    if job.get("status") != "completed":
        raise RuntimeError(f"Job ended in unexpected status: {job.get('status')} — {job}")

    result = job.get("result", {})
    ai_response = result.get("response", "")

    # 4. Export the finished, human-approved document.
    out = client.export_document(export_path, session_id=session_id, format=export_format)

    return DraftResult(session_id=session_id, job_id=job_id, exported_path=out, ai_response=ai_response)
