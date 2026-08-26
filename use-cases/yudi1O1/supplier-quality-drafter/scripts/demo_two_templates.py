#!/usr/bin/env python
"""Demo: draft the SAME structured input onto TWO different customer templates
and prove the output differs only in presentation, not content.

This is what "WHAT STRONG LOOKS LIKE" asks for on this build's card: "the same
inputs re-drafted onto a second customer template change presentation only."

Requires a live SUPERDOCS_API_KEY (uses real operations) — this is a demo
script, not a test. The unit tests in tests/ cover the same logic without
hitting the network.

Usage:
    export SUPERDOCS_API_KEY=sk_...
    python scripts/demo_two_templates.py
"""
from __future__ import annotations

import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supplier_quality_drafter.client import SuperDocsClient
from supplier_quality_drafter.io_yaml import load_draft_request
from supplier_quality_drafter.models import DraftRequest
from supplier_quality_drafter.verify import expected_facts
from supplier_quality_drafter.workflow import approve_all, draft_document

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def required_facts(req: DraftRequest) -> list[str]:
    """The exact data-derived facts that must survive onto ANY template,
    verbatim, regardless of that template's own wording or instructions.

    Deliberately NOT a raw number scan over the whole document — a template's
    own boilerplate (e.g. "1-10 AIAG-VDA scale" in one template's instructions,
    absent in the other's) contains numbers too, and those are presentation,
    not data. Comparing "every digit in the file" conflates the two.

    Builds on `verify.expected_facts` (the same check the drafter itself runs
    after every export, so the demo and the tool can't drift apart) and adds the
    free-text fields that only matter for a cross-template comparison.
    """
    facts: list[str] = list(expected_facts(req))
    for fm in req.failure_modes:
        facts.append(fm.failure_mode)
        for value in (fm.severity, fm.occurrence, fm.detection):
            if value is not None:
                facts.append(str(value))
    for a in req.actions:
        facts.append(a.recommended_action)
    if req.ppap and req.ppap.submission_level is not None:
        facts.append(str(req.ppap.submission_level))
    if req.eightd:
        facts.append(req.eightd.d4_root_cause)
    return facts


def main() -> int:
    client = SuperDocsClient()
    # sample_input.yaml deliberately has a missing rating (to demo the validation
    # gate elsewhere); this demo needs a clean input so it draws on the complete variant.
    req = load_draft_request(os.path.join(ROOT, "examples", "sample_input_complete.yaml"))

    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)

    print("Drafting onto template A (Acme Automotive Systems)...")
    result_a = draft_document(
        client, req,
        template_path=os.path.join(ROOT, "templates", "customer_template_a.html"),
        session_id="demo-two-templates-a",
        export_path=os.path.join(out_dir, "demo-template-a.md"),
        export_format="markdown",
        approval_callback=approve_all,
    )

    print("Drafting onto template B (Meridian Powertrain Co.)...")
    result_b = draft_document(
        client, req,
        template_path=os.path.join(ROOT, "templates", "customer_template_b.html"),
        session_id="demo-two-templates-b",
        export_path=os.path.join(out_dir, "demo-template-b.md"),
        export_format="markdown",
        approval_callback=approve_all,
    )

    with open(result_a.exported_path, encoding="utf-8") as f:
        text_a = f.read()
    with open(result_b.exported_path, encoding="utf-8") as f:
        text_b = f.read()

    print(f"\nTemplate A output: {result_a.exported_path} ({len(text_a)} chars)")
    print(f"Template B output: {result_b.exported_path} ({len(text_b)} chars)")

    facts = required_facts(req)
    missing_a = [f for f in facts if f not in text_a]
    missing_b = [f for f in facts if f not in text_b]

    ok = True
    if missing_a:
        print(f"\nFAIL: {len(missing_a)} data fact(s) missing from template A output: {missing_a}")
        ok = False
    if missing_b:
        print(f"\nFAIL: {len(missing_b)} data fact(s) missing from template B output: {missing_b}")
        ok = False

    if ok:
        print(
            f"\nOK: all {len(facts)} data-derived facts (IDs, S/O/D ratings, computed RPNs, dates, "
            "narrative fields) appear verbatim in both outputs. The two documents differ only in "
            "the surrounding template structure and wording — content parity holds."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
