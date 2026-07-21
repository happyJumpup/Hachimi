---
name: candidate-fusion
version: 1.1.0
description: Fuse speech and visual evidence into reviewable action candidates.
---

# Candidate fusion

## Input

Structured speech signals and visual segments using absolute source-video times.

## Output behaviour

- Merge temporally overlapping evidence for the same action.
- Preserve distinct actions as separate candidates in timeline order.
- Deduplicate repeated speech and visual evidence for the same demonstration while
  preserving separate demonstrations that occur at different times.
- Prefer an explicit speech name; use a visual name only when speech has none.
- Preserve missing fields as `null`; never create training parameters.
- Attach each speech or visual evidence span to its candidate.
- Mark partial, unnamed, or weakly bounded candidates as needing confirmation.
- Emit no candidate when neither branch contains action evidence.
- Weight is not part of this Skill's input or output.
