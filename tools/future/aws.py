"""
AWS integration stub.

Extend BaseTool here when adding AWS-backed actions:
- EC2 auto-scaling group adjustments
- ECS service restarts
- CloudWatch metric fetching
- SSM parameter store reads
"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool, ToolResult


class AWSAutoScalingTool(BaseTool):
    name = "aws_autoscaling"
    description = "Adjust AWS Auto Scaling Group desired capacity (stub)"

    async def run(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError("AWS integration not yet implemented")
