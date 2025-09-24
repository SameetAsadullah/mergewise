from __future__ import annotations

from tests.conftest import FakeOpenAI
from src.context.service import RetrievalRequest
from src.reviewer import DiffParser, ReviewConfig, ReviewEngine


class StubContextService:
    def __init__(self):
        self.indexed_paths = []

    def ensure_index(self, paths):
        self.indexed_paths.append(list(paths))

    def retrieve_context(self, request: RetrievalRequest):
        return [f"Context for {request.file_path}"]


FAKE_COMPLETION = {
    "file": "src/app.py",
    "summary": "looks good",
    "findings": [
        {
            "severity": "WARNING",
            "title": "Possible bug",
            "lines": "10",
            "anchor": "return a + b",
            "rationale": "Need validation",
            "recommendation": "Add guard",
            "patch": "",
        }
    ],
}


DIFF_TEXT = """\
diff --git a/src/app.py b/src/app.py
index 000..111 100644
--- a/src/app.py
+++ b/src/app.py
@@\n+def add(a, b):\n+    return a + b\n"""


def test_review_engine_generates_summary():
    config = ReviewConfig(model="fake", context_top_k=2)
    engine = ReviewEngine(config=config, openai_client=FakeOpenAI(completion_payloads=[FAKE_COMPLETION]))
    context = StubContextService()

    result = engine.review(
        pr_title="Add function",
        unified_diff=DIFF_TEXT,
        max_files=5,
        context_service=context,
    )

    assert result["files"][0]["findings"][0]["severity"] == "WARNING"
    assert "Reviewed" in result["summary"]
    assert context.indexed_paths  # ensure context ensure_index called


def test_review_engine_no_diff():
    config = ReviewConfig(model="fake", context_top_k=2)
    engine = ReviewEngine(config=config, openai_client=FakeOpenAI())

    result = engine.review(pr_title="Empty", unified_diff="")
    assert result["summary"] == "No diff to review."
    assert result["findings_total"] == 0
