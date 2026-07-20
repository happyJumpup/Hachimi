---
name: training-speech-understanding
version: 1.0.0
description: Extract explicit training-action signals from timestamped speech.
---

# Training speech understanding

## Input

A JSON object containing the trigger time, the absolute analysis window, and
timestamped transcript utterances. Times are seconds on the source-video clock.

## Output

Return JSON only:

```json
{
  "signals": [
    {
      "action_name": "string",
      "sets": null,
      "reps": null,
      "duration_seconds": null,
      "rest_seconds": null,
      "start_seconds": 0,
      "end_seconds": 0,
      "evidence_text": "short supporting phrase"
    }
  ]
}
```

## Rules

- Extract only actions and parameters explicitly stated in the transcript.
- Preserve missing fields as `null`; never invent a prescription.
- Never extract or infer creator weight, user weight, body measurements, injury
  risk, exercise quality, or calories.
- Prefer the signal nearest the trigger time when speech spans several actions.
- Evidence text must be a short transcript phrase, not hidden reasoning.
