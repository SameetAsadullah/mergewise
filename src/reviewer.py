from __future__ import annotations
import os, re, json
from dataclasses import dataclass
from typing import List, Dict, Any
from openai import OpenAI

from .settings import OPENAI_MODEL

SYSTEM_PROMPT = """You are a senior staff engineer doing code review.
Evaluate diffs for:
1) Correctness & security (injection, secrets, auth, crypto)
2) Reliability & concurrency
3) Performance & complexity
4) Testing impact (missing/updated tests)
5) Maintainability & style

Rules:
- Be precise and cite exact lines/hunks.
- Prefer minimal, concrete patches.
- Classify each finding: BLOCKER, WARNING, or NIT.
- If no issues, return an empty findings list.
- Output strict JSON matching the schema.
"""

@dataclass
class ReviewChunk:
    file: str
    diff_text: str  # unified diff for this file

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

def _call_openai_for_file(file_path: str, file_diff: str, pr_title: str) -> Dict[str, Any]:
    client = OpenAI()
    user_instructions = f"""
Review the following diff for one file in the context of the PR title.

PR_TITLE: {pr_title}

Return STRICT JSON with keys: file, summary, findings.
- file: the file path you are reviewing
- summary: one paragraph summary of your review for this file
- findings: array of objects with keys: severity (BLOCKER|WARNING|NIT), title, lines, rationale, recommendation, patch

If no issues: return findings: [].

FILE: {file_path}
DIFF:
{file_diff}
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

def review_pr(pr_title: str, unified_diff: str, max_files: int = 25) -> Dict[str, Any]:
    chunks = split_diff_by_file(unified_diff)
    if not chunks:
        return {"summary": "No diff to review.", "files": [], "findings_total": 0}

    chunks = chunks[:max_files]  # simple cap for MVP
    all_files: List[Dict[str, Any]] = []
    for ch in chunks:
        result = _call_openai_for_file(ch.file, ch.diff_text[:70_000], pr_title)
        all_files.append(result)

    blockers = sum(1 for f in all_files for x in f.get("findings", []) if x.get("severity") == "BLOCKER")
    warnings = sum(1 for f in all_files for x in f.get("findings", []) if x.get("severity") == "WARNING")
    nits = sum(1 for f in all_files for x in f.get("findings", []) if x.get("severity") == "NIT")
    summary = f"Reviewed {len(all_files)} file(s). Found {blockers} blocker(s), {warnings} warning(s), {nits} nit(s)."

    return {"summary": summary, "files": all_files, "findings_total": blockers + warnings + nits}
