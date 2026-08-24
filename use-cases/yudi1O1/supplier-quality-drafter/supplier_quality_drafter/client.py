"""Minimal SuperDocs REST client: the four-call contract (upload, chat, approve,
export) plus the async/jobs plumbing HITL needs, and nothing else.

Deliberately hand-rolled rather than pulled from an SDK dependency — this file
is the whole integration surface, easy to read end to end in one sitting.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

DEFAULT_BASE_URL = "https://api.superdocs.app"


class SuperDocsError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class JobFailed(SuperDocsError):
    pass


class JobTimedOut(SuperDocsError):
    pass


@dataclass
class PendingChange:
    change_id: str
    operation: str
    chunk_id: Optional[str]
    old_html: Optional[str]
    new_html: Optional[str]
    ai_explanation: str


class SuperDocsClient:
    def __init__(self, api_key: Optional[str] = None, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("SUPERDOCS_API_KEY")
        if not self.api_key:
            raise SuperDocsError(
                "No SuperDocs API key. Set SUPERDOCS_API_KEY in your environment "
                "(get one at use.superdocs.app -> Settings -> API Keys)."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    # ---- low-level ----

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._session.request(method, url, timeout=self.timeout, **kwargs)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            raise SuperDocsError(f"{method} {path} -> {resp.status_code}: {payload}", resp.status_code, payload)
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return {"_raw": resp.content}

    # ---- health / auth ----

    def health(self) -> dict:
        resp = self._session.get(f"{self.base_url}/health", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def verify_key(self) -> bool:
        try:
            self._request("GET", "/v1/sessions")
            return True
        except SuperDocsError as e:
            if e.status_code == 401:
                return False
            raise

    # ---- documents ----

    def upload_document(self, file_path: str, session_id: str, open_mode: Optional[str] = None) -> dict:
        data = {"session_id": session_id}
        if open_mode:
            data["open_mode"] = open_mode
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f)}
            return self._request("POST", "/v1/documents/upload", files=files, data=data)

    def export_document(
        self,
        out_path: str,
        session_id: Optional[str] = None,
        html: Optional[str] = None,
        format: str = "docx",
        options: Optional[dict] = None,
    ) -> str:
        body: dict[str, Any] = {"format": format}
        if session_id:
            body["session_id"] = session_id
        if html:
            body["html"] = html
        if options:
            body["options"] = options
        url = f"{self.base_url}/v1/documents/export"
        resp = self._session.post(url, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise SuperDocsError(f"export -> {resp.status_code}: {resp.text}", resp.status_code)
        with open(out_path, "wb") as f:
            f.write(resp.content)
        return out_path

    # ---- templates ----

    def upload_template(self, file_path: str) -> dict:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f)}
            return self._request("POST", "/v1/templates/upload", files=files)

    def list_templates(self) -> dict:
        return self._request("GET", "/v1/templates")

    # ---- chat (sync + async) ----

    def chat(self, message: str, session_id: str, document_html: Optional[str] = None, **extra) -> dict:
        body = {"message": message, "session_id": session_id, **extra}
        if document_html is not None:
            body["document_html"] = document_html
        return self._request("POST", "/v1/chat", json=body)

    def chat_async(self, message: str, session_id: str, document_html: Optional[str] = None, **extra) -> dict:
        body = {"message": message, "session_id": session_id, **extra}
        if document_html is not None:
            body["document_html"] = document_html
        return self._request("POST", "/v1/chat/async", json=body)

    def get_job(self, job_id: str) -> dict:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def cancel_job(self, job_id: str) -> dict:
        return self._request("POST", f"/v1/jobs/{job_id}/cancel")

    def approve(
        self,
        session_id: str,
        job_id: str,
        approved: bool,
        change_id: Optional[str] = None,
        changes: Optional[list[dict]] = None,
        feedback: Optional[str] = None,
    ) -> dict:
        body: dict[str, Any] = {"job_id": job_id, "approved": approved}
        if change_id:
            body["change_id"] = change_id
        if changes is not None:
            body["changes"] = changes
        if feedback:
            body["feedback"] = feedback
        return self._request("POST", f"/v1/chat/{session_id}/approve", json=body)

    def continue_job(self, session_id: str, job_id: str, do_continue: bool) -> dict:
        return self._request("POST", f"/v1/chat/{session_id}/continue", json={"job_id": job_id, "continue": do_continue})

    def poll_job(
        self,
        job_id: str,
        poll_interval: float = 3.0,
        max_wait: float = 900.0,
        on_poll=None,
    ) -> dict:
        """Poll a job until it lands on a status that needs the caller's attention:
        completed, failed, cancelled, or awaiting_approval. Raises on failure/timeout."""
        waited = 0.0
        while True:
            job = self.get_job(job_id)
            status = job.get("status")
            if on_poll:
                on_poll(job)
            if status in ("completed", "awaiting_approval", "cancelled"):
                return job
            if status == "failed":
                raise JobFailed(f"Job {job_id} failed: {job.get('error')}", payload=job)
            if waited >= max_wait:
                raise JobTimedOut(f"Job {job_id} did not finish within {max_wait}s (last status={status})", payload=job)
            time.sleep(poll_interval)
            waited += poll_interval
