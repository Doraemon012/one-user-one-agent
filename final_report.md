# Final Report — One User, One Agent

## What was implemented

- A deterministic, local replay agent with viewer-specific state and audit records.
- Goal selection that chooses at most one eligible goal per decision.
- Personalized homepage actions validated against catalogue availability, dialect, and holdout restrictions.
- Explicit no-action decisions when evidence or policy checks prevent a safe intervention.
- Evidence provenance, conflict visibility, event deduplication, and handling for late or stale triggers.
- Goal-rule correction handling and outcome judging based on observed, attributable events.
- Automated regression tests and a simulator-based evaluation workflow.

## Verified results

The latest verified run produced:

- **Tests:** 9 passed.
- **Decisions:** 2,679.
- **Published actions:** 1,089.
- **No-action decisions:** 1,590.
- **Holdout actions:** 0.
- **Simulator events:** 31,476.
- **Simulator policy problems:** 0.

These figures describe the supplied local replay and simulator, not a production deployment.

## Impact analysis

In the supplied randomized pilot data:

- Treatment: 349 successes among 1,912 users (**18.25%**).
- Holdout: 387 successes among 1,979 users (**19.56%**).
- Observed difference: approximately **−1.30 percentage points** for treatment.

The available results do not establish a positive causal improvement. The served-versus-unserved comparison is observational and may be affected by selection bias. See `impact_report.md` for the methodology and uncertainty estimates.

## Limitations

- The replay provider is deterministic; no live LLM inference is required or evaluated.
- The implementation is a local replay CLI, not a deployed streaming-service integration.
- G10 cannot be evaluated because its configured success event is null.
- G11 remains unsupported because the supplied event schema does not establish `tv_app_open` as a valid success signal.
- Viewer state checkpoints are written, but a fresh replay rebuilds state from the input pack rather than resuming incrementally from saved checkpoints.
- Reported latency measures local deterministic replay processing, not end-to-end latency with an external model or production services.

## How to reproduce

Follow the setup, test, replay, simulation, and outcome-judging commands in `README.md`. The `sample_run/` directory contains representative output artifacts.