import pytest

from src.review_models import FindingModel, FileReviewModel, ReviewResultModel


def test_finding_model_rejects_invalid_severity():
    with pytest.raises(ValueError):
        FindingModel(severity="CRITICAL", title="Issue")


def test_file_review_model_defaults():
    model = FileReviewModel(file="main.py")
    assert model.summary == ""
    assert model.findings == []


def test_review_result_model_counts_findings():
    result = ReviewResultModel(
        summary="Test",
        files=[
            FileReviewModel(
                file="main.py",
                findings=[FindingModel(severity="BLOCKER", title="Bug")],
            )
        ],
        findings_total=0,
        per_file_diffs={}
    )
    assert result.findings_total == 1
