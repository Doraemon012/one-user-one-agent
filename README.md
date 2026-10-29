# One User, One Agent

Deterministic, local replay implementation for the OTT lifecycle-agent hiring challenge.
The system gives each viewer one evidence-backed next step: a measurable goal, one
validated intervention or an explicit no-action decision, followed by attributable outcome
judging.

All viewer, catalogue, replay, and outcome data remains local. The runtime uses Python's
standard library and does not require network access or API credentials.

## What this implementation does

- Maintains one viewer-scoped lifecycle state, goal decision history, action history, and
  evidence/conflict record.
- Processes historical data, live events, controls, and triggers in delivery order.
- Deduplicates event and trigger IDs and records stale offline triggers without acting on them.
- Uses `received_at` for event ordering; phone-clock timestamps are not treated as authoritative.
- Selects at most one eligible goal from the configured goal catalogue.
- Publishes a simulator-compatible personalized home page only after independent catalogue,
  dialect, availability, and holdout checks.
- Abstains safely when evidence is insufficient, a model fault is injected, a trigger is stale,
  or no valid action can be constructed.
- Applies goal-rule corrections to subsequent decisions and records the correction timestamp.
- Judges outcomes only from observed, attributable simulator events.

The implementation intentionally prefers a conservative home-page action or no action over
unsupported messaging or model claims.

## Architecture and lifecycle

`replay_agent.py` is a single-process replay CLI with a small deterministic provider boundary:

1. Load the immutable pack, profiles, catalogue, goals, intervention policy, randomized arms,
   replay controls, and model-fault script.
2. Build per-viewer state from de-duplicated historical events. Profiles use the latest
   `updated_at` row; events use server `received_at`; conflicts remain visible.
3. Merge controls, live events, and triggers by delivery time. Controls are applied before
   same-time triggers.
4. Reject duplicate triggers and stale offline triggers. A stale trigger is recorded as
   no-action rather than treated as a current visit.
5. Evaluate goals in configured priority order. A viewer can have at most one selected goal.
6. Apply independent validation before publication: holdout isolation, catalogue availability,
   dialect, content uniqueness, and minimum row size.
7. Write a decision record with evidence references, freshness, assumptions, conflicts,
   deadline, success signal, validation checks, provider metadata, and measured latency.
8. Persist compact per-viewer state as `viewer_state/<viewer_id>.json`.
9. After simulation, `judge` attributes success only to a matching published action whose
   personalized page was actually shown before the goal deadline.

The simulator remains the execution oracle for action acceptance and generated user behavior.

## Evidence, conflicts, rules, and abstention

Evidence precedence is explicit:

- Server `received_at` controls event order and freshness.
- Profile facts come from the latest profile row by `updated_at`, but event history can expose
  conflicts such as purchases contradicting a `never` profile status.
- Current app dialect on the trigger is used for page construction; profile dialect is a
  fallback, and disagreement is retained in the decision record.
- Catalogue availability is checked at the measured publication time, including replay title
  withdrawals.
- Catalogue descriptions, titles, search text, deeplinks, and model-generated claims are data,
  never instructions or evidence.

The G03 correction is applied to future decisions after its control timestamp and is included
in the effective goal success contract. Historical decisions are not silently rewritten.
G10 is unsupported because its configured success event is null. G11 is unsupported because
the supplied event schema does not establish a valid `tv_app_open` signal.

No action is recorded when the viewer is in holdout, the trigger is stale, a configured model
fault occurs, a dependency fault requires safe degradation, the goal is ineligible, or no
valid catalogue action exists.

## Safety and failure boundaries

- **Catalogue:** title IDs must exist, be available at publication, use the trigger dialect,
  and not duplicate a content slug on a page. Rows below three titles are not emitted.
- **Holdout:** users assigned to `holdout` receive no agent actions.
- **Messaging:** the policy gate enforces consent, opt-outs, IST quiet hours, and the shared
  one-message-per-day cap. The current replay strategy does not need to publish outbound
  messages.
- **Model/provider:** the replay provider is credential-free and deterministic. Injected
  malformed, unsupported-claim, and timeout faults become no-action decisions. Provider
  metadata reports zero model calls in this deterministic mode; the interface is bounded for
  a configurable maximum of 8 model calls and 20 tool calls per decision.
- **Latency:** every decision records measured latency and publication uses delivery time plus
  measured processing time.
- **Privacy:** supplied synthetic data is treated as personal data and is never uploaded.

## Repository structure

```text
replay_agent.py                 CLI, lifecycle logic, provider boundary, outcome judge
tests/test_replay_agent.py      behavioral regression tests
config/                         goal, intervention, pricing, and messaging policy inputs
data/                           supplied synthetic events, profiles, catalogue, episodes
replay/                         supplied replay stream, controls, faults, and randomized arms
simulator/                      supplied outcome simulator
sample_run/                     reproducible representative outputs
viewer_state/                   local generated state; ignored by Git
ARCHITECTURE.md                 brief-required architecture note
AI_COLLABORATION.md             brief-required, factual collaboration note
KNOWN_LIMITATIONS.md            brief-required limitations note
impact_report.md                randomized pilot measurement
final_report.md                 concise submission summary
SUBMISSION_NOTES.md              legacy overlapping notes; not required by the brief
assignment_brief.pdf            original challenge brief
```

Supplied input data and the simulator are preserved. `replay_output/`, `viewer_state/`,
Python caches, logs, and local environments are regenerable and ignored. `sample_run/` is
the retained representative output required by the brief; rerunning the commands below
replaces/regenerates it.

## Setup

Verified with Python 3.12. The implementation uses only the standard library.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

No package installation is required. `requirements.txt` documents that choice.

## Test and replay commands

Run from the repository root:

```bash
# Tests
.venv/bin/python -m unittest discover -s tests -v

# Deterministic replay
.venv/bin/python replay_agent.py replay --pack . --out replay_output

# Supplied simulator
.venv/bin/python simulator/simulate_outcomes.py \
  --pack . \
  --actions replay_output/actions.jsonl \
  --out replay_output/sim

# Attributable outcome judging
.venv/bin/python replay_agent.py judge \
  --pack . \
  --out replay_output \
  --outcome-events replay_output/sim/outcome_events.jsonl
```

To regenerate the retained sample directory:

```bash
rm -rf sample_run
mkdir -p sample_run
cp replay_output/actions.jsonl replay_output/decisions.jsonl replay_output/replay_report.json sample_run/
.venv/bin/python simulator/simulate_outcomes.py \
  --pack . --actions sample_run/actions.jsonl --out sample_run/sim
.venv/bin/python replay_agent.py judge \
  --pack . --out sample_run \
  --outcome-events sample_run/sim/outcome_events.jsonl
```

The `rm -rf sample_run` command is intentionally limited to the named generated output
directory. Do not use it against supplied data or the repository root.

## Outputs

- `actions.jsonl`: simulator input actions.
- `decisions.jsonl`: one decision per trigger, including no-action reasons and audit evidence.
- `replay_report.json`: counts, controls applied, holdout action count, and unsupported goals.
- `viewer_state/*.json`: durable per-viewer facts, conflicts, decision history, and actions.
- `sim/outcome_events.jsonl`: simulator-observed events.
- `sim/simulator_report.json`: simulator acceptance and policy-problem counts.
- `outcome_judgement.json`: attributable success counts; G10/G11 remain unsupported.
- `latency_report.json`: measured decision latency distribution for the retained run.
- `impact_report.md`: randomized pilot comparison and selection-bias explanation.

Inspect representative records without dumping datasets:

```bash
head -2 sample_run/decisions.jsonl
head -2 sample_run/actions.jsonl
cat sample_run/replay_report.json
cat sample_run/outcome_judgement.json
cat sample_run/latency_report.json
```

## Evaluation methodology

The pilot impact report uses original randomized assignment, not service status:

- Treatment: 1,912 users; 349 successes; 18.25%.
- Holdout: 1,979 users; 387 successes; 19.56%.
- Raw difference: approximately -1.30 percentage points for treatment.
- Approximate 95% binomial intervals are 16.52%-19.98% for treatment and
  17.81%-21.30% for holdout; the approximate difference interval is -3.76 to
  +1.16 percentage points. These are descriptive normal-approximation intervals,
  not a pre-registered significance analysis.

The outcome is the supplied per-user `succeeded` flag in `pilot_outcomes.csv`; the denominator
is every user in the corresponding randomized arm, including failures. No missing pilot outcome
rows were found. This is a descriptive randomized-arm comparison, not a claim that the
implementation caused an effect. The available pack does not provide a pre-registered
analysis or power calculation, so the intervals are included only as transparent uncertainty
context.

The earlier report's 40.1% served headline is observational: 217 served treatment users had
87 successes (40.09%), while 1,695 unserved treatment users had 262 successes (15.46%).
Service status is selected and can reflect viewer readiness, so served-versus-unserved is
selection-biased and cannot establish causal uplift.

For replay outcomes, the judge uses only a matching goal, action, observed personalized
page exposure, success event, and configured deadline. It does not invent G10 or G11
semantics.

## Verified latest run

The latest local validation produced:

- 6 tests passed.
- 2,679 replay decisions. The supplied trigger stream contains duplicate trigger IDs;
  duplicates are ignored idempotently.
- 1,089 home-page actions.
- 1,590 no-action decisions.
- 0 holdout actions.
- 31,476 simulator events.
- 1,089 accepted home pages.
- 0 simulator policy problems.
- 2,679 measured decisions under 60 seconds; latency was 0.005 ms minimum,
  0.065 ms median, 0.652 ms p95, and 1.323 ms maximum in the retained run.

## Trade-offs and limitations

The action strategy is intentionally conservative: home-page personalization provides a useful
simulator-compatible intervention without inventing copy, prices, templates, or consent. The
trade-off is that this does not explore the full message-channel action space and may leave
some eligible viewers unserved. That is preferable to publishing an unsupported or unsafe
message under conflicting evidence.

Known limitations are documented in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). In particular,
G10 and G11 cannot be honestly measured from this pack, the model provider is deterministic
replay logic rather than a live LLM, and the data is synthetic. The separate required notes
are [ARCHITECTURE.md](ARCHITECTURE.md), [AI_COLLABORATION.md](AI_COLLABORATION.md), and
[final_report.md](final_report.md).
