from __future__ import annotations
from pydantic import BaseModel, Field

class GithubReviewRequest(BaseModel):
    owner: str = Field(..., example="pallets")
    repo: str  = Field(..., example="flask")
    pr_number: int = Field(..., example=12345)
    max_files: int = Field(25, ge=1, le=100, description="Cap number of files to review")

class ReviewRequest(BaseModel):
    pr_title: str = Field(..., description="Short PR title")
    unified_diff: str = Field(..., description="Unified git diff text (e.g., git diff HEAD~1..HEAD)")
