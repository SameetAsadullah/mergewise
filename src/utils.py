from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import re

HUNK_RE = re.compile(r'^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@')

def _newfile_lines_from_diff(file_diff: str) -> List[Tuple[int, str]]:
    """
    Return a list of (new_line_number, content_without_prefix) for all lines
    that exist in the *new* file, i.e. lines starting with ' ' (context) or '+' (added).
    Removed '-' lines do not advance the new file line counter.
    """
    lines_map: List[Tuple[int, str]] = []
    new_ln = None

    for raw in file_diff.splitlines():
        if raw.startswith('@@'):
            m = HUNK_RE.match(raw)
            if m:
                start = int(m.group(1))
                new_ln = start
            continue
        if new_ln is None:
            continue  # before first hunk header

        if raw.startswith(' '):
            lines_map.append((new_ln, raw[1:]))
            new_ln += 1
        elif raw.startswith('+'):
            lines_map.append((new_ln, raw[1:]))
            new_ln += 1
        elif raw.startswith('-'):
            # do not increment new_ln
            continue
        else:
            # other metadata lines; ignore
            continue
    return lines_map

def _locate_anchor_line(file_diff: str, anchor: Optional[str]) -> Optional[int]:
    """
    Find the new-file line number for the given anchor text.
    First try exact match; fall back to a whitespace-normalized match.
    """
    if not anchor:
        return None
    rows = _newfile_lines_from_diff(file_diff)
    # exact
    for ln, text in rows:
        if text == anchor:
            return ln
    # relaxed: collapse inner whitespace
    norm = " ".join(anchor.split())
    for ln, text in rows:
        if " ".join(text.split()) == norm:
            return ln
    return None

def result_to_check_conclusion(result: Dict[str, Any]) -> str:
    any_blocker = any(
        (x.get("severity","").upper()=="BLOCKER")
        for f in (result.get("files") or [])
        for x in (f.get("findings") or [])
    )
    return "failure" if any_blocker else "neutral"

def build_check_summary_markdown(result: Dict[str, Any]) -> str:
    files = result.get("files", []) or []
    blockers = sum(1 for f in files for x in (f.get("findings") or []) if (x.get("severity","").upper()=="BLOCKER"))
    warnings = sum(1 for f in files for x in (f.get("findings") or []) if (x.get("severity","").upper()=="WARNING"))
    nits     = sum(1 for f in files for x in (f.get("findings") or []) if (x.get("severity","").upper()=="NIT"))
    return (
        f"**{result.get('summary','')}**\n\n"
        f"- Blockers: {blockers}\n- Warnings: {warnings}\n- Nits: {nits}\n\n"
        "Use the Annotations tab to jump to each finding."
    )

def build_github_annotations(result: Dict[str, Any], per_file_diffs: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Convert findings to GitHub Check Run annotations using anchor→line mapping.
    `per_file_diffs` should map file path -> that file's unified diff chunk.
    """
    level_map = {"BLOCKER": "failure", "WARNING": "warning", "NIT": "notice"}
    anns: List[Dict[str, Any]] = []
    for f in (result.get("files") or []):
        path = f.get("file", "")
        file_diff = per_file_diffs.get(path, "")
        for x in (f.get("findings") or []):
            sev = (x.get("severity") or "").upper()
            lvl = level_map.get(sev, "notice")
            # prefer anchor mapping; fall back to parsed "lines"
            anchor_ln = _locate_anchor_line(file_diff, x.get("anchor"))
            if anchor_ln is not None:
                start_line = end_line = anchor_ln
            else:
                rng = str(x.get("lines") or "").strip()
                if "-" in rng:
                    try:
                        s, e = rng.split("-", 1)
                        start_line = int(s); end_line = int(e)
                    except Exception:
                        start_line = end_line = 1
                else:
                    try:
                        start_line = end_line = int(rng) if rng else 1
                    except Exception:
                        start_line = end_line = 1

            title = (x.get("title") or f"{sev} in {path}")[:255]
            why = (x.get("rationale") or "").strip()
            fix = (x.get("recommendation") or "").strip()
            message = (why + ("\n\nFix: " + fix if fix else "")).strip() or title

            anns.append({
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "annotation_level": lvl,
                "title": title,
                "message": message[:65535],
            })
    return anns[:500]
