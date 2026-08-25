# yt-is Personal Intelligence — Dashboard State
Updated: 2026-08-24 by architect handoff

## Goal & constraints

- [seen] Make the corpus provide ongoing decision utility rather than primarily
  document browsing.
- [seen] Primary UI objects should become interests, goals, questions, claims,
  opportunities, and actions; documents remain supporting evidence.
- [seen] Reuse the existing yt-is application/dashboard substrate by default.
- [seen] Expose provenance and uncertainty rather than presenting inference as
  unquestionable fact.

## Non-goals

- [seen] Do not replace the current dashboard framework without demonstrated
  outcome improvement.
- [seen] Do not build every envisioned visualization before the typed graph and
  evaluation gates work.
- [seen] Do not treat a semantic map or other visualization as epistemic
  authority.
- [seen] Do not hide incomplete corpus coverage behind polished UI.

## Decisions

- 2026-08-24: [seen] `/today`, `/interests`, and `/interest/{id}` are the
  initial core surfaces.
- 2026-08-24: [seen] Future high-value surfaces include Regret Feed, Research
  Queue, Emerging Radar, Bridge Topics, Timeline, Belief Update Board, Idea
  Graph, and Opportunity Board.
- 2026-08-24: [seen] AI-Paper-Trends is a design donor for hierarchical atlas
  and drill-through ideas, not an automatic dependency.
- 2026-08-24: [seen] Cytoscape.js is a candidate embedded graph component, not
  grounds to replace the application shell.
- 2026-08-24: [seen] marimo is a candidate analytical workspace, not the
  primary operator dashboard.

## Current state

- [seen] `/interests` exists and exposes v1.5 evidence-cluster information and
  corpus-coverage context.
- [seen] `/today` is structurally present.
- [seen] Current `/today` is still largely mechanical cluster/recent-document
  selection rather than the final typed goal-aware utility-ranked home.
- [seen] Feedback controls/endpoints are structurally present.
- [absent-unverified] A working `/interest/{id}` typed drill-down.
- [absent-unverified] Typed Interest Atlas hierarchy.
- [absent-unverified] Production Regret Feed, Research Queue, Belief Update
  Board, Bridge Topics, Timeline, Idea Graph, or Opportunity Board.

## Open questions

- What minimum `/today` experience is useful before ranking passes its
  falsifier?
- Which typed relationships should `/interest/{id}` expose first?
- Which interactions are feedback versus workflow state transitions such as
  investigate/watch/save?
- What portions of the atlas can remain mechanical?
- Which visualizations measurably improve decisions rather than browsing?

## Next action

Hold broad dashboard expansion until inference contract fidelity is accepted.
Then implement `/interest/{id}` and make `/today` consume authoritative typed
interest/goal/provenance state.
