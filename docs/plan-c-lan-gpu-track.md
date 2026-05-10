# Plan C — LAN GPU cert track (autonomous tier)

!!! info "Status: Planning"
    Plan C closes the v0.7.1 autonomous-tier cert that Plan B (cloud
    CPU) had to defer when DigitalOcean denied GPU droplet access.
    Awaiting operator specs (Mac Mini RAM, Linux distro) to author
    provisioning + bootstrap scripts.

## Why Plan C exists

* **Plan A** (cloud GPU quad) — blocked: DO denied GPU access; every
  GPU SKU returned `422 Size is not available in this region`.
* **Plan B** (cloud CPU + postgres+AGE) — **CLOSED, 2-round 100% GREEN.**
  Autonomous-tier scenarios remained on SKIP because daemons ran
  `tier=semantic` (no Ollama).
* **Plan C** (LAN: Mac Mini + Linux postgres) — closes the autonomous-
  tier coverage gap on hardware the operator already has.

## Topology

```
┌─────────────────────────────────────────────┐    ┌─────────────────────────────────────┐
│  Apple Mac Mini  (host)                     │    │  Linux node  (LAN)                  │
│  ═══════════════════════════════════════    │    │  ═══════════════════════════════    │
│  Ollama  (native macOS, Metal GPU)          │    │  PostgreSQL 16                       │
│   • gemma4:e4b   (~9.6 GB resident)         │    │  Apache AGE 1.5.0+                   │
│   • nomic-embed  (~600 MB)                  │    │  pgvector 0.7.4                      │
│   • cross-encoder (~250 MB)                 │    │                                      │
│   • listens on :11434 (host)                │    │  96 GB RAM · 14 CPU                  │
│                                             │    │  shared_buffers = 24 GB              │
│  Docker Desktop                             │    │  effective_cache_size = 72 GB        │
│  ┌────────────────┐  ┌────────────────┐    │    │  work_mem = 256 MB                   │
│  │ openclaw-1     │  │ openclaw-2     │    │    │  max_connections = 200               │
│  │ ai-memory      │  │ ai-memory      │    │    │  random_page_cost = 1.1 (NVMe)       │
│  │ tier=autonomous│  │ tier=autonomous│    │    │                                      │
│  │ ollama_base_url│  │ ollama_base_url│    │    │  Listen: <lan-ip>                    │
│  │ →host.docker   │  │ →host.docker   │    │    │  pg_hba: host all aimemory           │
│  │ .internal:11434│  │ .internal:11434│    │    │  <mac-cidr>/N scram-sha-256          │
│  │ store_url→pg   │  │ store_url→pg   │    │◀──▶│                                      │
│  │ federation     │  │ federation     │    │    └─────────────────────────────────────┘
│  │ peer=oc2       │  │ peer=oc1       │    │
│  └────────────────┘  └────────────────┘    │
└─────────────────────────────────────────────┘
        ↕ federation HTTPS+mTLS over Docker bridge / loopback
```

### Load-bearing design choice

**Ollama runs natively on the Mac host, NOT inside containers.** Docker
Desktop on Mac runs a Linux VM that doesn't have Metal GPU passthrough.
The canonical AI-on-Mac pattern is host-native Ollama + containerized
clients pointing at `host.docker.internal:11434`. The 2 ai-memory
containers share the same Ollama instance on the host.

### Inference backend on Mac — Ollama 0.23+ auto-uses MLX

Probed on the operator's M4 Mac Mini (FROSTYi.local) 2026-05-10:

```
$ lsof -p $(pgrep -x ollama) | grep mlx
ollama  ... /Applications/Ollama.app/Contents/Resources/mlx_metal_v4/libmlx.dylib
ollama  ... /Applications/Ollama.app/Contents/Resources/mlx_metal_v4/libmlxc.dylib
ollama  ... /Applications/Ollama.app/Contents/Resources/mlx_metal_v4/libjaccl.dylib
```

Ollama **0.19+ ships MLX backend**; **0.23.1 (this Mac) auto-selects
MLX on Metal-4-capable Apple Silicon (M3/M4)**. The legacy
GGUF/llama.cpp.metal path is now the fallback; MLX is the default on
modern Apple chips. ai-memory's existing autonomous-tier flow
(`ollama_base_url` HTTP) gets MLX acceleration **for free** — no
daemon-side architectural change required.

Cross-platform inference matrix (informational; doesn't affect Plan C):

| Platform              | Plan C path             | What's under the hood          |
|-----------------------|-------------------------|---------------------------------|
| **macOS M3/M4**       | Ollama 0.23+ HTTP       | MLX via Metal 4 (auto)         |
| macOS M1/M2           | Ollama 0.23+ HTTP       | MLX via Metal 3 (`mlx_metal_v3`) |
| Linux NVIDIA CUDA     | Ollama 0.23+ HTTP       | llama.cpp.cuda (default)       |
| Linux AMD ROCm        | Ollama 0.23+ HTTP       | llama.cpp.rocm                 |
| Windows               | Ollama, LM Studio, Jan.ai TensorRT-LLM | platform-specific |

Future v0.8+ direction (out of scope for v0.7.1 Plan C): pluggable
in-process inference trait with cargo features for `candle`,
`mistralrs`, `mlx-rs`, `llama-cpp-rs` — eliminates HTTP per-call
overhead. Not on Plan C critical path.

## What Plan C unlocks vs Plan B

| Capability                                  | Plan B (CPU cloud)         | Plan C (LAN GPU)                      |
|---------------------------------------------|----------------------------|---------------------------------------|
| Postgres SAL contract                       | ✅ Closed                   | ✅ Same                                |
| Federation W=2/N=2 quorum                   | ✅ Closed                   | ✅ Same                                |
| HTTPS+mTLS perf                             | ✅ 13.6ms mean handshake   | ✅ Same (lower LAN latency)            |
| AGE bench-gate (S76) representative         | ⚠️ Storage-bound (16 GB pg) | ✅ Engine-bound (96 GB pg, 24 GB sbuf) |
| **Autonomous-tier scenarios live**          | ❌ Skipped (semantic only)  | ✅ **Live-testable** (Mac Metal Ollama)|
| `auto_tag` LLM tagger                       | SKIP                       | RUN                                   |
| `consolidate` LLM merger                    | SKIP                       | RUN                                   |
| `expand_query` LLM mode                     | SKIP                       | RUN                                   |
| `detect_contradiction` LLM judge            | SKIP                       | RUN                                   |
| `smart_load` LLM-driven inclusion           | SKIP                       | RUN                                   |
| GPU OOM graceful degradation test           | N/A                        | RUN                                   |
| Cloud cost per cert pass                    | ~$5-15                     | **$0**                                |
| Operator setup time (one-time)              | 0 (scripts ready)          | ~2-3 h                                |
| Reproducibility                             | doctl + scripts            | Mac brew + Linux apt + docker-compose |

## Strengths

1. **Closes the autonomous-tier cert backlog** — 5+ scenarios deferred since Round 1 become live-testable.
2. **AGE bench-gate validity** — 96 GB pg with 24 GB shared_buffers + 72 GB effective_cache_size produces engine-bound numbers matching ROADMAP2 §4.6 published baselines. Plan B's 16 GB pg was storage-bound.
3. **Zero cloud cost** — re-runnable indefinitely; CI hookable; long soak runs feasible without burn.
4. **Apple Silicon GPU is genuinely fast for Gemma-4-class** — M2/M3/M4 hit 60-100 tok/s on `gemma4:e4b`, well above the 1500ms p95 budget for `memory_consolidate`.

## Weaknesses / risks (rank-ordered by severity)

| # | Risk                                                                           | Mitigation                                                              |
|--:|--------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| 1 | Docker-on-Mac doesn't expose Metal GPU                                         | Run Ollama natively on host; daemons in containers via `host.docker.internal`. Standard pattern. |
| 2 | Single-host failure domain                                                     | Linux postgres provides durability of all writes; daemon restart re-syncs from SAL.    |
| 3 | Mac Mini RAM ceiling determines whether autonomous-tier fits                   | Need: 32+ GB. macOS (~5 GB) + Docker VM (~6 GB) + Ollama (~16 GB) + 2 daemons (~2 GB). |
| 4 | Federation across Docker bridge less realistic than cross-host                  | Simulate partition via `docker network disconnect`; S14/S25/S39 test still valid.      |
| 5 | macOS+Linux postgres-client SCRAM-SHA-256 compat under load                     | Smoke-test sqlx + scram + Mac→Linux postgres at start of bootstrap.                    |
| 6 | No ECC RAM on Mac Mini (Mac Studio has ECC; Mini doesn't)                      | Long soak runs may hit transient memory faults; attribute carefully if flakes appear. |

## Cost envelope

| Item                                | Plan B            | Plan C                |
|-------------------------------------|-------------------|------------------------|
| Cloud spend per cert pass           | ~$5-15            | **$0**                 |
| Hardware                            | rented per-hour   | already-owned          |
| Operator setup (one-time)           | 0 min             | ~2-3 h                 |
| Operator per-cert hands-on time     | ~10 min provision | ~5 min (start daemons) |
| xAI API spend per cert pass         | ~$15-20           | ~$15-20 (unchanged)    |

## Cert closure plan (when authored)

13 phases, mirror of Plan B's `run_cpu_cert.sh` master orchestrator
with autonomous-tier hooks:

| Phase | Action                                                                                         |
|------:|-------------------------------------------------------------------------------------------------|
| P0    | Pre-flight (brew, docker, ssh-to-Linux-node, postgres password file)                           |
| P1    | Provision Ollama on Mac host (brew install ollama; ollama pull gemma4:e4b nomic-embed-text)    |
| P2    | Provision postgres on Linux node (apt install postgresql-16, AGE source build, pgvector)      |
| P3    | Bootstrap 2× ai-memory containers via docker-compose (autonomous tier; ollama_base_url; store_url) |
| P4    | Schema-init via openclaw-1 → postgres                                                         |
| P5    | Wire mTLS (TLS material from /tmp/a2a-v07-tls-gpu/; volume-mount into containers)             |
| P6    | Baseline validation (B/D/T/N/S/C/X/P/F/A/U) — autonomous-tier C1-C9 path                       |
| P7    | Per-droplet autonomous-tier validation (Ollama health, models loaded, LLM-bound endpoints)     |
| P8    | Round 1 cert (full regression incl. autonomous-tier scenarios)                                 |
| P9    | Round 2 cert (must be 100% GREEN twice)                                                        |
| P10   | NHI discovery sweep (S83/S84/S85 with `tier_autonomous.md` overlay)                            |
| P11   | Render results page (3-audience analysis)                                                      |
| P12   | (no teardown — LAN hardware stays up; just docker-compose down)                                |

## Open questions for operator

| Question                                        | Why                                                |
|-------------------------------------------------|-----------------------------------------------------|
| **Mac Mini exact model + RAM?**                 | Determines whether autonomous-tier fits             |
|                                                 | • 16 GB → blocked                                   |
|                                                 | • 24 GB → tight                                     |
|                                                 | • 32 GB → comfortable                               |
|                                                 | • 64 GB → ideal                                     |
| **Mac Mini chip generation?** (M1/M2/M3/M4)     | M1 = ~30 tok/s; M3/M4 = ~60-100 tok/s on gemma4:e4b |
| **Linux distro on the LAN node?**               | Ubuntu/Debian/RHEL/Arch — bootstrap script target  |
| **Linux node Postgres-client friendly?** (apt postgresql-common) | Or do we build from source                       |
| **LAN IP scheme** (Mac IP, Linux IP, subnet)    | Postgres listen + pg_hba CIDR                       |
| **Network speed** (1Gb / 10Gb)                  | AGE bench expectations                              |
| **Keep Plan B cloud running in parallel?**      | If yes: both certify independently. If no: teardown |

## Status checkpoints

| Date       | Status                                          |
|------------|-------------------------------------------------|
| 2026-05-10 | Plan C concept proposed by operator             |
| 2026-05-10 | Plan C designated; planning doc landed          |
| TBD        | Operator provides Mac specs + Linux distro      |
| TBD        | Plan C provisioning scripts authored            |
| TBD        | Plan C R1 cert run                              |
| TBD        | Plan C R2 cert run                              |
| TBD        | v0.7.1 autonomous-tier cert closure             |

## What's reusable from Plan B

Plan B left a strong foundation that Plan C inherits unchanged:

* `scripts/validate_baseline.sh` — 11-domain baseline gates (tier-aware)
* `scripts/validate_autonomous_tier.sh` — per-droplet C1-C9 autonomous-tier checks
* `scripts/render_gpu_results.py` — 3-audience analysis renderer
* `scenarios/83_nhi_discovery_openclaw.py` + S84 + S85 — NHI discovery layer
* `prompts/nhi_discovery/{system_briefing,tier_autonomous,...}.md` — NHI briefings
* `scripts/run_round1.py` — track-aware load_scope (just need a new track ID `L`)
* `scope-v0.7.0.json` — scenario manifest (autonomous-tier skip rationales auto-inverted on track L)
* `/tmp/a2a-v07-tls/` — existing CA + 5 client certs (volume-mount into containers)

## What's net-new for Plan C

* `scripts/provision_lan_topology.sh` — sets up Mac host (brew install ollama, model pulls, port-forward smoke) and Linux postgres host (apt install, AGE build, pgvector, schema-init)
* `docker-compose.yaml` — 2 ai-memory containers with autonomous-tier env, mTLS material volume-mounted, federation peer set
* `scripts/run_lan_cert.sh` — master orchestrator (phases P0-P12 above)
* Minor: `q_track_in_scope` analog `lan_track_in_scope` in scope manifest if needed
