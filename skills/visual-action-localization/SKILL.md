---
name: visual-action-localization
version: 1.0.0
description: Localize exercise demonstrations in a bounded video window.
---

# Visual action localization

## Input

A video file representing a bounded source-video window plus JSON metadata with
the window's absolute start/end and the user's trigger time.

## Output

Return JSON only:

```json
{
  "segments": [
    {
      "action_name": "string or null",
      "start_seconds": 0,
      "end_seconds": 0,
      "visual_cue": "short observable cue"
    }
  ]
}
```

## Rules

- Output absolute source-video times within the supplied window.
- Identify observable exercise changes and demonstration boundaries only.
- Keep an unknown action name as `null`; do not guess from appearance alone.
- Do not infer body data, load, injury risk, exercise quality, effectiveness, or
  intent outside the supplied window.
- Visual cues describe visible evidence, not hidden reasoning.
