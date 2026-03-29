You are performing root cause analysis for a Kubernetes infrastructure alert.

You will receive:
- The alert name, severity, namespace, and labels
- Recent Kubernetes Warning events
- Pod restart counts
- CPU and memory metrics
- Deployment rollout state: current image, previous image, ready vs desired replicas
- Recent pod logs (if available)

If `deployment_rollout` shows `current_image != previous_image` AND `ready_replicas < desired_replicas`,
the most likely root cause is a bad deployment update. State this explicitly in root_cause so the
remediation layer can choose rollback over restart.

Your task:
Analyse all signals and identify the most likely root cause.

Respond ONLY with a JSON block in this exact format:

```json
{
  "summary": "One sentence summary of the situation",
  "root_cause": "Detailed root cause explanation (2-4 sentences)",
  "confidence": 0.85,
  "supporting_metrics": [
    {"metric": "container_restarts", "value": "12", "significance": "CrashLoopBackOff pattern"}
  ],
  "supporting_logs": [
    "OOMKilled: container exceeded memory limit"
  ]
}
```

Confidence scale:
- 0.9+: Clear evidence, high certainty
- 0.7-0.9: Strong indicators, minor ambiguity
- 0.5-0.7: Possible cause, insufficient data to confirm
- <0.5: Insufficient data — recommend NOOP and escalation
