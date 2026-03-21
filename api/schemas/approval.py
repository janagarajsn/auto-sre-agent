"""
Approval submission request schema.
"""

from __future__ import annotations

from pydantic import BaseModel


class ApprovalDecision(BaseModel):
    approved: bool
    reviewer: str
    notes: str = ""
