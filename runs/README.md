# runs/

Each campaign run lands here as `runs/<campaign-id>/`. The shape:

```
runs/<campaign-id>/
├── a2a-summary.json              # aggregator output (one per run)
├── scenario-<id>.json            # per-scenario report (one line of JSON)
├── scenario-<id>.log             # per-scenario stderr log
└── index.html                    # offline evidence page (generated)
```

`scripts/collect_reports.sh` writes `a2a-summary.json` after running the full
manifest. `scripts/render_pages.py` reads each summary to populate `docs/index.html`
and the per-campaign mkdocs pages under `docs/runs/`.

This directory is ALMOST entirely gitignored — only `runs/README.md` is checked
in by default. The campaign workflow commits run-specific artifacts as part of
its evidence-publishing step.
