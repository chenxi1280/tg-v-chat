# Index Maintenance

## Required Updates

- Product updates `docs/index/dataflow.md` when business objects, states, permissions, pages, or API/worker flows change.
- Dev updates `docs/index/project-structure.md` when entrypoints, modules, models, schemas, workers, or tests change.
- QA checks both indexes during validation when the task touches dataflow or module boundaries.

## Status Values

- `updated`: index changed in this task.
- `unchanged`: index was checked and no change is needed.
- `required`: index must change before handoff.
- `blocked`: index cannot be updated because required input is missing.
- `unproven`: the agent did not verify index impact.

