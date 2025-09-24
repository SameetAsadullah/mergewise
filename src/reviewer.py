from __future__ import annotations
import logging
import os, re, json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence
from openai import OpenAI

from .settings import OPENAI_MODEL, CONTEXT_TOP_K

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior staff engineer doing code review.
Evaluate diffs for:
1) Correctness & security (injection, secrets, auth, crypto)
2) Reliability & concurrency
3) Performance & complexity
4) Testing impact (missing/updated tests)
5) Maintainability & style

Output strict JSON for each file:
- file: string
- summary: string
- findings: array of objects with keys:
  - severity: BLOCKER | WARNING | NIT
  - title: string
  - lines: string range like "72" or "41-45" (best estimate; optional)
  - anchor: EXACT code line copied verbatim from the DIFF (prefer a '+' added line; else a context ' ' line). Do NOT invent.
  - rationale: string
  - recommendation: string
  - patch: unified diff snippet for the fix (optional)
"""

@dataclass
class ReviewChunk:
    file: str
    diff_text: str  # unified diff for this file


class ContextProvider(Protocol):
    def ensure_index(self, target_paths: Sequence[str]) -> None:
        ...

    def retrieve_context(self, file_path: str, diff_text: str, top_k: int = CONTEXT_TOP_K) -> List[str]:
        ...

def split_diff_by_file(unified_diff: str) -> List[ReviewChunk]:
    """Split a unified PR diff into file-scoped chunks."""
    parts = re.split(r'(?m)^diff --git ', unified_diff)
    chunks: List[ReviewChunk] = []
    for part in parts:
        if not part.strip():
            continue
        header = part.splitlines()[0]
        m = re.search(r'\sa\/([^\s]+)\s+b\/([^\s]+)', "diff --git " + header)
        file_path = m.group(2) if m else header.strip()
        chunk_text = "diff --git " + part if not part.startswith("diff --git ") else part
        chunks.append(ReviewChunk(file=file_path, diff_text=chunk_text))
    return chunks

def _call_openai_for_file(
    file_path: str,
    file_diff: str,
    pr_title: str,
    context_blocks: Optional[List[str]] = None,
) -> Dict[str, Any]:
    client = OpenAI()
    context_section = ""
    if context_blocks:
        formatted = "\n\n---\n\n".join(block.strip() for block in context_blocks if block and block.strip())
        if formatted:
            context_section = f"\n\nAdditional repository context:\n{formatted}"
    user_instructions = f"""
Review the following single-file unified diff in the context of the PR title.

Rules for 'anchor':
- Choose ONE exact line from the DIFF that best represents the issue location.
- Prefer a line that begins with '+' (added). If none, choose a context line that begins with a single space ' '.
- Copy the line's content AFTER the sign (+ or space) exactly; no trimming besides leading sign.
- Never use a '-' removed line as the anchor.

Return STRICT JSON with keys: file, summary, findings[{{
  severity, title, lines, anchor, rationale, recommendation, patch
}}].

PR_TITLE: {pr_title}

FILE: {file_path}
DIFF:
{file_diff}
{context_section}
"""
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_instructions},
        ],
        temperature=0.2,
    )
    content = resp.choices[0].message.content
    try:
        data = json.loads(content)
    except Exception:
        data = {"file": file_path, "summary": "", "findings": []}
    data.setdefault("file", file_path)
    data.setdefault("summary", "")
    data.setdefault("findings", [])
    return data

def review_pr(
    pr_title: str,
    unified_diff: str,
    max_files: int = 25,
    context_provider: Optional[ContextProvider] = None,
    context_top_k: Optional[int] = None,
) -> Dict[str, Any]:
    chunks = split_diff_by_file(unified_diff)
    if not chunks:
        return {"summary": "No diff to review.", "files": [], "findings_total": 0, "per_file_diffs": {}}

    chunks = chunks[:max_files]
    all_files: List[Dict[str, Any]] = []
    per_file_diffs = {}

    if context_provider:
        try:
            context_provider.ensure_index([ch.file for ch in chunks])
        except Exception as exc:
            logger.warning("Context indexing failed; continuing without context: %s", exc)
            context_provider = None

    for ch in chunks:
        per_file_diffs[ch.file] = ch.diff_text
        contexts: Optional[List[str]] = None
        if context_provider:
            try:
                contexts = context_provider.retrieve_context(
                    ch.file,
                    ch.diff_text,
                    top_k=context_top_k or CONTEXT_TOP_K,
                )
            except Exception as exc:
                logger.warning("Context retrieval failed for %s: %s", ch.file, exc)
                contexts = None
        result = _call_openai_for_file(
            ch.file,
            ch.diff_text[:70_000],
            pr_title,
            context_blocks=contexts,
        )
        all_files.append(result)

    blockers = sum(1 for f in all_files for x in f.get("findings", []) if x.get("severity") == "BLOCKER")
    warnings = sum(1 for f in all_files for x in f.get("findings", []) if x.get("severity") == "WARNING")
    nits = sum(1 for f in all_files for x in f.get("findings", []) if x.get("severity") == "NIT")
    summary = f"Reviewed {len(all_files)} file(s). Found {blockers} blocker(s), {warnings} warning(s), {nits} nit(s)."

    return {
        "summary": summary,
        "files": all_files,
        "findings_total": blockers + warnings + nits,
        "per_file_diffs": per_file_diffs,
    }
