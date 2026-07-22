---
name: training-speech-understanding
version: 1.4.0
description: Extract all explicit training-action signals from full-source timestamped speech.
---

# Training speech understanding

## Input

A JSON object containing the full source-video range and timestamped transcript
utterances. Times are seconds on the source-video clock.

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
      "evidence_text": "short supporting phrase",
      "segment_role": "follow_along | teaching_demo | unknown"
    }
  ]
}
```

## Rules

- Extract only actions and parameters explicitly stated in the transcript.
- Preserve missing fields as `null`; never invent a prescription.
- Treat spoken action labels as identifiers: preserve every distinguishing
  modifier such as seated, cross-body, drag, or hammer-grip. Never shorten a
  qualified label to a generic movement family such as curl or row.
- Preserve an explicitly spoken English or Chinese exercise term in the label.
- Never extract or infer creator weight, user weight, body measurements, injury
  risk, exercise quality, or calories.
- Extract every distinct training action explicitly stated across the full source.
- Set `segment_role` to `follow_along` only when speech explicitly invites the
  viewer to perform the action in sync, and to `teaching_demo` only when speech
  clearly frames the interval as explanation, demonstration, or correction.
  Otherwise return `unknown`.
- A teaching segment's elapsed video time is not a training-duration parameter.
- Keep signals in source-video timeline order.
- Evidence text must be a short transcript phrase, not hidden reasoning.
