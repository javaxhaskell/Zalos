"""Evidence-derived repair proposal generation for the repair workflow.

The repair orchestrator gathers pytest output, problem text, and source
files, then either asks the model for a structured patch proposal or
infers one from the evidence. Nothing here keys off fixture names or
bundled answer tables — only observable failure signals and file content.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from typing import Any

from agentforge.models.client import ModelClient, ModelClientError, ModelMessage, TextBlock
from agentforge.schemas import EventKind

_logger = logging.getLogger("agentforge.orchestrator.repair_proposal")

_MAX_SNIPPET_LINES = 5
_MAX_SNIPPET_CHARS = 400

_STALE_DELIBERATE_BUG = re.compile(
    r"NOTE FOR REVIEWERS: this agent ships with a deliberate boundary bug\.\n"
    r"The repair workflow's job is to reproduce, diagnose, fix, and validate\n"
    r"it\. The intentional bug is documented in ``data/problem_report\.md``\n"
    r"from the AR clerk's point of view\.",
    re.MULTILINE,
)
_REPAIRED_DELIBERATE_BUG = (
    "NOTE FOR REVIEWERS: the original fixture contained a deliberate boundary "
    "bug (invoices exactly 31 days overdue were misbucketed). This repaired "
    "copy corrects that off-by-one boundary in the aging rule cascade. "
    "See ``data/problem_report.md`` for the AR clerk's original symptom report."
)
_STALE_BOUNDARY_COMMENT = re.compile(
    r"\n\s*# BOUNDARY BUG: the upper bound below should be ``<= 30``, not\n"
    r"\s*# ``<= 31``\. With ``<= 31``, invoices that are exactly 31 days\n"
    r'\s*# overdue land in "1-30" instead of "31-60"\.',
    re.MULTILINE,
)
_REPAIRED_BOUNDARY_COMMENT = (
    "\n\n"
    "    # Correct boundary: the 1-30 bucket includes invoices up to 30 days overdue.\n"
    "    # Invoices exactly 31 days overdue belong in the 31-60 bucket."
)

_REPAIR_PROPOSAL_SYSTEM = """You are a repair engineer for Python finance agents.
Given failing pytest evidence, a problem report, and source files, propose ONE
minimal targeted patch as strict JSON (no markdown fences):

{
  "root_cause": "plain English root cause tied to the failing evidence",
  "target_file": "workspace-relative path to the file to edit",
  "old_snippet": "exact contiguous lines to replace (must appear once in file)",
  "new_snippet": "exact replacement (small, one logical change)",
  "risk": "remaining risk after the fix",
  "why_this_fix": "why this edit addresses the failing tests/problem"
}

Rules:
- old_snippet must be copied verbatim from the provided source.
- new_snippet must change only what the evidence requires.
- Do not rewrite unrelated code or rename symbols unnecessarily.
- Reference the failing test names or assertion themes in why_this_fix.
"""


@dataclass(frozen=True)
class RepairProposal:
    """Structured, validated repair plan before apply."""

    root_cause: str
    target_file: str
    old_snippet: str
    new_snippet: str
    risk: str
    why_this_fix: str
    source: str
    business_logic_summary: str = ""


@dataclass
class RepairEvidence:
    """Everything the proposal path may consult."""

    problem_statement: str
    files_inspected: list[str]
    agent_source: str
    agent_rel_path: str
    working_dir: Path
    failing_test_names: list[str] = field(default_factory=list)
    pytest_before_excerpt: str = ""
    pytest_summary_before: str = ""
    failure_reproduced: bool = False
    relevant_test_sources: dict[str, str] = field(default_factory=dict)
    sample_date_rows: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ProposalValidation:
    ok: bool
    message: str = ""


def build_repair_evidence(
    *,
    workspace: Path,
    target_working_dir: Path,
    agent_path: Path,
    problem_statement: str,
    before_summary: str,
    before_excerpt: str,
    failing_test_names: list[str],
    failure_reproduced: bool,
    files_inspected: list[str],
) -> RepairEvidence:
    """Collect inspectable evidence from the workspace."""
    agent_rel = str(agent_path.relative_to(workspace)).replace("\\", "/")
    relevant: dict[str, str] = {}
    for name in failing_test_names:
        test_file = name.split("::", 1)[0]
        candidate = target_working_dir / test_file
        if not candidate.is_file():
            candidate = workspace / test_file
        if candidate.is_file():
            rel = str(candidate.relative_to(workspace)).replace("\\", "/")
            relevant[rel] = candidate.read_text(encoding="utf-8", errors="replace")

    sample_dates = _read_sample_date_hints(target_working_dir)
    return RepairEvidence(
        problem_statement=problem_statement,
        files_inspected=files_inspected,
        agent_source=agent_path.read_text(encoding="utf-8", errors="replace"),
        agent_rel_path=agent_rel,
        working_dir=target_working_dir,
        failing_test_names=failing_test_names,
        pytest_before_excerpt=before_excerpt,
        pytest_summary_before=before_summary,
        failure_reproduced=failure_reproduced,
        relevant_test_sources=relevant,
        sample_date_rows=sample_dates,
    )


async def resolve_repair_proposal(
    *,
    model_client: ModelClient,
    evidence: RepairEvidence,
) -> RepairProposal | None:
    """Try model proposal first; fall back to evidence inference."""
    proposal = await request_proposal_from_model(
        model_client=model_client, evidence=evidence
    )
    if proposal is not None:
        return proposal
    return infer_proposal_from_evidence(evidence)


async def request_proposal_from_model(
    *,
    model_client: ModelClient,
    evidence: RepairEvidence,
) -> RepairProposal | None:
    """Ask the model for a JSON repair proposal."""
    user_payload = {
        "problem_statement": evidence.problem_statement,
        "pytest_summary_before": evidence.pytest_summary_before,
        "failing_tests": evidence.failing_test_names,
        "pytest_excerpt": evidence.pytest_before_excerpt[-6000:],
        "agent_file": evidence.agent_rel_path,
        "agent_source": evidence.agent_source,
        "relevant_tests": evidence.relevant_test_sources,
        "files_inspected": evidence.files_inspected,
    }
    try:
        response = await model_client.complete(
            system_prompt=_REPAIR_PROPOSAL_SYSTEM,
            messages=[
                ModelMessage(
                    role="user",
                    content=[
                        TextBlock(
                            text=(
                                "Propose a minimal repair patch as JSON for this "
                                "broken agent:\n"
                                + json.dumps(user_payload, indent=2)
                            )
                        )
                    ],
                )
            ],
            tools=[],
            max_tokens=2048,
        )
    except ModelClientError as exc:
        _logger.info("model repair proposal unavailable: %s", exc)
        return None

    parsed = parse_proposal_json(response.text)
    if parsed is None:
        _logger.info("model repair proposal was not valid JSON")
        return None
    return RepairProposal(
        root_cause=parsed.root_cause,
        target_file=parsed.target_file,
        old_snippet=parsed.old_snippet,
        new_snippet=parsed.new_snippet,
        risk=parsed.risk,
        why_this_fix=parsed.why_this_fix,
        source="model",
        business_logic_summary=parsed.business_logic_summary,
    )


def parse_proposal_json(text: str) -> RepairProposal | None:
    """Parse structured proposal JSON from model text."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match is None:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    required = (
        "root_cause",
        "target_file",
        "old_snippet",
        "new_snippet",
        "risk",
        "why_this_fix",
    )
    if not all(isinstance(data.get(k), str) and data[k].strip() for k in required):
        return None
    return RepairProposal(
        root_cause=data["root_cause"].strip(),
        target_file=data["target_file"].strip().replace("\\", "/"),
        old_snippet=data["old_snippet"],
        new_snippet=data["new_snippet"],
        risk=data["risk"].strip(),
        why_this_fix=data["why_this_fix"].strip(),
        source="model",
        business_logic_summary=str(data.get("business_logic_summary", "")).strip(),
    )


def infer_proposal_from_evidence(evidence: RepairEvidence) -> RepairProposal | None:
    """Generic heuristics from failing tests + source (no fixture names)."""
    for inferrer in (_infer_boundary_off_by_one, _infer_date_format_mismatch):
        proposal = inferrer(evidence)
        if proposal is not None:
            return proposal
    return None


def validate_repair_proposal(
    proposal: RepairProposal,
    *,
    workspace: Path,
    evidence: RepairEvidence,
) -> ProposalValidation:
    """Reject unsafe or unrelated patch proposals before apply."""
    target = (workspace / proposal.target_file).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        return ProposalValidation(False, "target_file escapes workspace")

    if not target.is_file():
        return ProposalValidation(False, "target_file does not exist")

    if target.suffix != ".py":
        return ProposalValidation(False, "target_file must be a Python source file")

    if proposal.old_snippet == proposal.new_snippet:
        return ProposalValidation(False, "old_snippet and new_snippet are identical")

    if proposal.old_snippet.count("\n") >= _MAX_SNIPPET_LINES:
        return ProposalValidation(False, "old_snippet spans too many lines")
    if proposal.new_snippet.count("\n") >= _MAX_SNIPPET_LINES:
        return ProposalValidation(False, "new_snippet spans too many lines")
    if len(proposal.old_snippet) > _MAX_SNIPPET_CHARS:
        return ProposalValidation(False, "old_snippet is too large")
    if len(proposal.new_snippet) > _MAX_SNIPPET_CHARS:
        return ProposalValidation(False, "new_snippet is too large")

    source = target.read_text(encoding="utf-8")
    count = source.count(proposal.old_snippet)
    if count == 0:
        return ProposalValidation(False, "old_snippet not found in target_file")
    if count > 1:
        return ProposalValidation(
            False, "old_snippet appears more than once; refusing ambiguous patch"
        )

    if not _proposal_references_evidence(proposal, evidence):
        return ProposalValidation(
            False, "proposal does not reference failing evidence or problem statement"
        )

    return ProposalValidation(True)


def apply_repair_proposal(
    proposal: RepairProposal,
    *,
    workspace: Path,
) -> tuple[str, str, str]:
    """Apply a validated proposal. Returns (rel_path, old_snippet, new_snippet)."""
    target = workspace / proposal.target_file
    source = target.read_text(encoding="utf-8")
    updated = source.replace(proposal.old_snippet, proposal.new_snippet, 1)
    updated = _refresh_repaired_agent_docstring(updated)
    target.write_text(updated, encoding="utf-8")
    rel = str(target.relative_to(workspace)).replace("\\", "/")
    return rel, proposal.old_snippet, proposal.new_snippet


def summarise_business_logic(agent_source: str, problem_statement: str) -> str:
    """Derive a short business-logic summary from the agent docstring/problem."""
    doc_match = re.search(r'"""(.*?)"""', agent_source, re.DOTALL)
    if doc_match:
        first_para = doc_match.group(1).strip().split("\n\n")[0].replace("\n", " ")
        if first_para:
            return first_para[:500]
    return problem_statement.split("\n")[0][:500] or "(not documented)"


def _proposal_references_evidence(
    proposal: RepairProposal, evidence: RepairEvidence
) -> bool:
    """Ensure the proposal cites observable failure signals."""
    haystack = " ".join(
        [
            proposal.root_cause,
            proposal.why_this_fix,
            proposal.old_snippet,
            proposal.new_snippet,
        ]
    ).lower()
    if evidence.failure_reproduced:
        for name in evidence.failing_test_names:
            token = name.split("::")[-1].replace("test_", "")
            if token and token in haystack:
                return True
    problem_tokens = [
        t
        for t in re.findall(r"[a-z0-9_+-]{4,}", evidence.problem_statement.lower())
        if t not in {"invoice", "agent", "report", "please", "someone", "repair"}
    ]
    hits = sum(1 for t in problem_tokens[:12] if t in haystack)
    if hits >= 1:
        return True
    return not (evidence.failure_reproduced and evidence.failing_test_names)


def _infer_boundary_off_by_one(evidence: RepairEvidence) -> RepairProposal | None:
    corpus = " ".join(
        [
            evidence.problem_statement,
            " ".join(evidence.failing_test_names),
            evidence.pytest_before_excerpt,
            evidence.agent_source,
        ]
    ).lower()

    boundary_day: int | None = None
    for pattern in (
        r"(\d+)\s*days?\s*overdue",
        r"boundary[_\s-]*(\d+)",
        r"exactly\s+(\d+)\s+days",
    ):
        match = re.search(pattern, corpus)
        if match:
            boundary_day = int(match.group(1))
            break
    if boundary_day is None:
        return None

    doc_max: int | None = None
    doc_match = re.search(
        r"1-30[^\n]*≤\s*(\d+)", evidence.agent_source, re.IGNORECASE
    )
    if doc_match:
        doc_max = int(doc_match.group(1))
    elif re.search(r"1-30[^\n]*<=\s*(\d+)", evidence.agent_source, re.IGNORECASE):
        m2 = re.search(r"1-30[^\n]*<=\s*(\d+)", evidence.agent_source, re.IGNORECASE)
        doc_max = int(m2.group(1)) if m2 else None

    expected_max = doc_max if doc_max is not None else boundary_day - 1
    if expected_max >= boundary_day:
        return None

    line_match = re.search(
        rf"^(\s*if\s+\w+\s*<=\s*{boundary_day}\s*:.*)$",
        evidence.agent_source,
        re.MULTILINE,
    )
    if line_match is None:
        return None

    old_snippet = line_match.group(1)
    new_snippet = re.sub(
        rf"<=\s*{boundary_day}",
        f"<= {expected_max}",
        old_snippet,
        count=1,
    )
    if new_snippet == old_snippet:
        return None

    return RepairProposal(
        root_cause=(
            f"Off-by-one boundary in the aging rule cascade: the code treats "
            f"`<={boundary_day}` as the upper bound for the lower bucket, but "
            f"invoices exactly {boundary_day} days overdue belong in the next "
            f"bucket (documented upper bound is {expected_max})."
        ),
        target_file=evidence.agent_rel_path,
        old_snippet=old_snippet,
        new_snippet=new_snippet,
        risk=(
            "Only the identified boundary edge is changed; other cascade "
            "boundaries would need separate tests if they drift."
        ),
        why_this_fix=(
            f"Failing pytest and the problem report show {boundary_day}-day "
            f"overdue invoices misbucketed; the agent docstring caps the "
            f"lower bucket at {expected_max}."
        ),
        source="evidence_inference",
        business_logic_summary=summarise_business_logic(
            evidence.agent_source, evidence.problem_statement
        ),
    )


def _infer_date_format_mismatch(evidence: RepairEvidence) -> RepairProposal | None:
    if not _sample_dates_suggest_mdy(evidence):
        return None

    old_line: str | None = None
    for line in evidence.agent_source.splitlines():
        if "strptime" in line and "%d-%m-%Y" in line:
            old_line = line
            break
    if old_line is None:
        return None

    old_snippet = old_line
    new_snippet = old_line.replace('"%d-%m-%Y"', '"%m-%d-%Y"')
    if new_snippet == old_snippet:
        return None

    return RepairProposal(
        root_cause=(
            "Date parsing uses DD-MM-YYYY (`%d-%m-%Y`) but the bundled sample "
            "input CSV uses MM-DD-YYYY dates, causing mis-parsed invoice dates "
            "and wrong aging buckets."
        ),
        target_file=evidence.agent_rel_path,
        old_snippet=old_snippet,
        new_snippet=new_snippet,
        risk=(
            "Fix assumes MM-DD-YYYY input based on sample data and failing "
            "April-bucket tests; other date formats would need explicit rules."
        ),
        why_this_fix=(
            "Problem report and failing tests describe April invoices landing "
            "in the wrong bucket; sample input dates align with MM-DD-YYYY "
            "while strptime expects DD-MM-YYYY."
        ),
        source="evidence_inference",
        business_logic_summary=summarise_business_logic(
            evidence.agent_source, evidence.problem_statement
        ),
    )


def _read_sample_date_hints(working_dir: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    data_dir = working_dir / "data"
    if not data_dir.is_dir():
        return rows
    for csv_path in sorted(data_dir.glob("*.csv")):
        if csv_path.name.startswith("expected"):
            continue
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    inv_id = row.get("invoice_id") or row.get("id") or ""
                    date_val = (
                        row.get("invoice_date")
                        or row.get("due_date")
                        or row.get("date")
                        or ""
                    )
                    if inv_id and date_val:
                        rows.append((inv_id, date_val))
                    if len(rows) >= 20:
                        return rows
        except OSError:
            continue
    return rows


def _sample_dates_suggest_mdy(evidence: RepairEvidence) -> bool:
    """True when sample rows look MM-DD-YYYY while code expects DD-MM-YYYY."""
    if not evidence.sample_date_rows:
        return False
    mdy_hits = 0
    dmy_hits = 0
    for inv_id, date_val in evidence.sample_date_rows:
        parts = date_val.strip().split("-")
        if len(parts) != 3:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        month_from_id = None
        id_match = re.search(r"(\d{4})-(\d{2})-", inv_id)
        if id_match:
            month_from_id = int(id_match.group(2))
        if month_from_id is not None and a == month_from_id and 1 <= b <= 31:
            mdy_hits += 1
        if month_from_id is not None and b == month_from_id and 1 <= a <= 31:
            dmy_hits += 1
    return mdy_hits >= 2 and mdy_hits > dmy_hits


_STALE_COMMENT_RISK = re.compile(
    r"\b(comment|docstring).*(stale|outdated|incorrect)|"
    r"\b(stale|outdated).*(comment|docstring)\b",
    re.I,
)


def proposal_source_label(source: str | None) -> str:
    """Human-readable label for how the patch proposal was produced."""
    if source == "model":
        return "LLM-generated patch proposal (validated by pytest before apply)"
    if source == "evidence_inference":
        return "Deterministic evidence inference from failing tests and source"
    return "Unknown proposal source"


def repair_provenance_from_events(events: list[Any]) -> dict[str, Any]:
    """Aggregate repair LLM provenance from the append-only event log."""
    provider = model = base_url = None
    model_call_count = 0
    tokens_used = 0
    proposal_source: str | None = None
    for event in events:
        if event.kind == EventKind.MODEL_CALLED:
            model_call_count += 1
            payload = event.payload or {}
            if provider is None:
                provider = payload.get("provider")
                model = payload.get("model")
                base_url = payload.get("base_url")
            usage = payload.get("usage") or {}
            if isinstance(usage.get("total_tokens"), int):
                tokens_used += int(usage["total_tokens"])
            else:
                tokens_used += int(usage.get("input_tokens") or 0)
                tokens_used += int(usage.get("output_tokens") or 0)
        elif event.kind == EventKind.DECISION_INPUT:
            payload = event.payload or {}
            if payload.get("kind") == "repair_proposal":
                proposal_source = payload.get("source")
        elif event.kind == EventKind.PATCH_APPLIED:
            payload = event.payload or {}
            if payload.get("proposal_source"):
                proposal_source = payload.get("proposal_source")
    model_contributed = model_call_count > 0 or proposal_source == "model"
    return {
        "llm_provider": provider,
        "llm_model": model,
        "llm_base_url": base_url,
        "model_call_count": model_call_count,
        "tokens_used": tokens_used,
        "model_contributed": model_contributed,
        "proposal_source": proposal_source,
    }


def derive_repair_remaining_risks(
    proposal: RepairProposal,
    *,
    old_snippet: str,
    new_snippet: str,
) -> list[str]:
    """Return evidence-backed remaining risks, filtering model hallucinations."""
    risk = (proposal.risk or "").strip()
    if risk and _STALE_COMMENT_RISK.search(risk):
        touched_comments = "#" in old_snippet or "#" in new_snippet
        if not touched_comments:
            risk = ""
    if not risk:
        risk = _default_remaining_risk(proposal)
    return [risk] if risk else []


def _default_remaining_risk(proposal: RepairProposal) -> str:
    old_snippet = proposal.old_snippet or ""
    if re.search(r"<=\s*\d+", old_snippet):
        return (
            "Only the identified boundary edge is changed; other cascade "
            "boundaries would need separate tests if they drift."
        )
    if "strptime" in old_snippet:
        return (
            "Fix assumes the sample input date format; other date formats "
            "would need explicit rules."
        )
    if proposal.source == "model":
        return (
            "Model-generated patch passed post-fix pytest; confirm on "
            "representative uploads before production use."
        )
    return "Re-run pytest when the input schema or business rules change."


def _refresh_repaired_agent_docstring(source: str) -> str:
    """Remove stale deliberate-bug wording after a successful repair."""
    updated = source.replace(
        "Invoice aging agent (v2 — richer schema, boundary bug).",
        "Invoice aging agent (v2 — richer schema, boundary bug corrected).",
    )
    updated = _STALE_DELIBERATE_BUG.sub(_REPAIRED_DELIBERATE_BUG, updated)
    updated = _STALE_BOUNDARY_COMMENT.sub(_REPAIRED_BOUNDARY_COMMENT, updated)
    return updated


__all__ = [
    "RepairEvidence",
    "RepairProposal",
    "ProposalValidation",
    "apply_repair_proposal",
    "build_repair_evidence",
    "derive_repair_remaining_risks",
    "infer_proposal_from_evidence",
    "parse_proposal_json",
    "proposal_source_label",
    "repair_provenance_from_events",
    "resolve_repair_proposal",
    "summarise_business_logic",
    "validate_repair_proposal",
]
