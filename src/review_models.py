from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class FindingModel(BaseModel):
    severity: str = Field(..., description="BLOCKER | WARNING | NIT")
    title: str
    lines: Optional[str] = None
    anchor: Optional[str] = None
    rationale: Optional[str] = None
    recommendation: Optional[str] = None
    patch: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def normalize_severity(cls, value: str) -> str:
        token = (value or "").strip().upper()
        if token not in {"BLOCKER", "WARNING", "NIT"}:
            raise ValueError("severity must be BLOCKER, WARNING, or NIT")
        return token

    @field_validator("lines", mode="before")
    @classmethod
    def coerce_lines(cls, value):
        if value is None:
            return None
        return str(value)


class FileReviewModel(BaseModel):
    file: str
    summary: str = ""
    findings: List[FindingModel] = Field(default_factory=list)


class ReviewResultModel(BaseModel):
    summary: str
    files: List[FileReviewModel]
    findings_total: int = 0
    per_file_diffs: Dict[str, str] = Field(default_factory=dict)

    @field_validator("findings_total", mode="after")
    @classmethod
    def ensure_consistent_total(cls, value: int, values) -> int:
        data = getattr(values, "data", {}) or {}
        files: List[FileReviewModel] = data.get("files", [])
        actual = sum(len(f.findings) for f in files)
        return actual if value != actual else value
