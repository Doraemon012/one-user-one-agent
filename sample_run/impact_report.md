# Impact report

## Design
This impact report compares users by their original randomized assignment (`pilot/pilot_assignment.csv`), not by whether they were served. That matches the assignment requirement and avoids selection bias from comparing served viewers to the rest of the population.

## Randomized arm comparison
- Treatment users: 1,912
- Holdout users: 1,979
- Treatment goal success rate: 349 / 1,912 = 18.25%
- Holdout goal success rate: 387 / 1,979 = 19.56%
- Approximate 95% binomial intervals are 16.52%-19.98% for treatment and
  17.81%-21.30% for holdout; the approximate difference interval is -3.76 to
  +1.16 percentage points. These are normal-approximation context only, not a
  pre-registered significance analysis.

These randomized groups do not show a clear positive uplift in the pilot. The raw difference is about -1.3 percentage points in favor of the holdout, and the observed data is too weak to claim a causal improvement.

## Served vs unserved comparison is misleading
The internal pilot summary compares served viewers with everyone else, which is biased because serving itself is not randomized. Users who were served were selected by the earlier product flow and, in general, were different from the broader population in ways that also affect conversion.

This means the reported uplift is an association, not a causal estimate. It is not valid evidence of the agent’s effect.

## Outcome definition
The pilot’s `succeeded` flag is used as the outcome of interest and is compared across randomized groups. This is the least ambiguous available observed outcome in the supplied pilot dataset.

## Limitation
The pilot data supports a randomized comparison of assignment groups, but it does not justify a causal claim about the agent’s effect because the served-vs-unserved comparison is observational and selected on service status.
