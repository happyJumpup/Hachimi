---
name: training-plan-compilation
version: 1.0.0
description: Compile reliable actions from a structured content-understanding result into a usable base training plan.
---

# Training plan compilation

Accept `complete` results normally. For `partial`, compile only reliable returned actions and preserve the declared coverage gaps for the product to show and retry. Reject `insufficient` results. Produce a base plan proposal without reading raw media, transcripts, user profile data, or training history. Preserve missing evidence-backed parameters for deterministic product defaults; never invent weight.
