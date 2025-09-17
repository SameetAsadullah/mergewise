from __future__ import annotations
from typing import Dict, Any, List

def render_markdown_comment(result: Dict[str, Any]) -> str:
    files = result.get("files", []) or []
    # counts for header badges
    def _count(level: str) -> int:
        return sum(1 for f in files for x in (f.get("findings") or []) if str(x.get("severity","")).upper() == level)

    blockers = _count("BLOCKER")
    warnings = _count("WARNING")
    nits     = _count("NIT")

    sev_emoji = {"BLOCKER": "🚫", "WARNING": "⚠️", "NIT": "💡"}

    def _truncate(s: str, n: int) -> str:
        return s if s is None or len(s) <= n else s[: n - 1] + "…"

    def _safe_code(s: str | None) -> str:
        # avoid breaking markdown fences
        if not s:
            return ""
        return s.replace("```", "`` `")

    # --- Header ---
    lines: List[str] = []
    lines.append("### 🤖 MergeWise Code Review")
    lines.append("")
    lines.append(
        f"**Summary:** {result.get('summary','')}"
        f" &nbsp;&nbsp;|&nbsp;&nbsp; **{sev_emoji['BLOCKER']} Blockers:** {blockers}"
        f" &nbsp;&nbsp; **{sev_emoji['WARNING']} Warnings:** {warnings}"
        f" &nbsp;&nbsp; **{sev_emoji['NIT']} Nits:** {nits}"
    )
    lines.append("")

    # --- Quick index of files with issues ---
    files_with_issues = [f for f in files if f.get("findings")]
    if files_with_issues:
        lines.append("<details><summary><strong>Files with issues</strong></summary>")
        for f in files_with_issues:
            fname = f.get("file", "(unknown)")
            count = len(f.get("findings") or [])
            lines.append(f"- `{fname}` — {count} finding(s)")
        lines.append("</details>")
        lines.append("")

    # --- Per-file sections ---
    for f in files_with_issues:
        fname = f.get("file", "(unknown)")
        fsummary = _truncate(f.get("summary","").strip(), 300)
        lines.append(f"#### `{fname}`")
        if fsummary:
            lines.append(f"_Summary:_ {fsummary}")
        findings = f.get("findings") or []
        for i, x in enumerate(findings, 1):
            sev = str(x.get("severity","")).upper()
            emoji = sev_emoji.get(sev, "•")
            title = x.get("title","").strip() or "(no title)"
            rng = x.get("lines","") or "—"
            why = _truncate(x.get("rationale","").strip(), 800)
            fix = _truncate(x.get("recommendation","").strip(), 800)
            patch = _safe_code(x.get("patch"))

            lines.append(f"- {emoji} **{sev}** — **{title}** _(lines {rng})_")
            if why:
                lines.append(f"  - **Why:** {why}")
            if fix:
                lines.append(f"  - **Fix:** {fix}")
            if patch:
                lines.append("  <details><summary>Suggested patch</summary>\n\n```diff")
                lines.append(patch)
                lines.append("```\n</details>")
        lines.append("")  # spacing between files

    # If no issues at all
    if not files_with_issues:
        lines.append("_No issues found. Nice work!_ 🎉")

    # Keep under GitHub limits (big safety margin)
    text = "\n".join(lines).strip()
    return text[:18000]
