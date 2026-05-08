# Soak (gated)

The 4-hour soak cron only activates after the v0.7.0 A2A surface achieves
**two consecutive 100% GREEN rounds** in `two-rounds.yml`.

## Cadence

`.github/workflows/soak.yml` mirrors `ai-memory-ship-gate`'s cadence:

```
*/4h × 14 days = 84 soak runs per release
at ~$3 each   → ~$252 per soak window
```

Each soak run is a complete campaign of all S1–S69 scenarios. Scenarios marked
soak-gated only (S40 1000-store, S62 HNSW, S69 token budget) carry through; the
deterministic regression scenarios (S1, S1b, S30, S35, …) also execute every
soak run because the soak's value is exactly that — proving stability under
many independent dispatches, not in any one round.

## Activation procedure

1. Confirm Round 1 + Round 2 both landed at 100% PASS in the runs index.
2. Update `.soak-state` (committed by the workflow on first manual dispatch).
3. Manually dispatch `soak.yml` once with `run_number=1` and the release tag.
   That kicks off the cron schedule.
4. After 14 days (or 84 successful runs, whichever first), the workflow
   self-bounds and stops dispatching.

## Failure handling

A soak FAIL never reverts the v0.7.0 cert — but it surfaces a regression
candidate that future patch releases must own. Each FAIL run files an issue
in the repo via the workflow's `gh issue create` step.

## Why gated

A soak that runs 84 times against a binary that fails Round 1 is a 84× waste
of DigitalOcean budget. The gate keeps the spend disciplined.
