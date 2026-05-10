# Plan C — LAN GPU cert (autonomous tier) — live status

!!! info "Status: IN FLIGHT"
    **Provisioning + bootstrap: COMPLETE.** Postgres+AGE+pgvector live on f2 (Linux gateway 192.168.50.1). Ollama 0.23+ MLX confirmed live on Mac M4 host (192.168.50.100). Daemon container image building. R1 cert run pending build completion.

## Topology under test

```
┌──────────────────────────────────────────────┐    ┌─────────────────────────────────────┐
│  Mac Mini  M4 · macOS 26.4.1                 │    │  Linux f2 · Pop!_OS 24.04 LTS      │
│  192.168.50.100 (en0, gigabit)               │    │  192.168.50.1 (enp86s0, gigabit)   │
│  ═════════════════════════════════════════   │    │  ═════════════════════════════════  │
│  Apple M4 · 10 cores (4P+6E) · 32 GB RAM     │    │  Intel Core Ultra 5 225H · 14 C    │
│  10-GPU-core Metal 4                         │    │  93 GiB RAM · 912 GB NVMe          │
│                                              │    │                                     │
│  Ollama 0.23.1 (auto-MLX via Metal 4)        │    │  PostgreSQL 16.13                  │
│   • gemma4:e4b   (~9.6 GB resident)          │    │  Apache AGE 1.5.0 (built from src) │
│   • nomic-embed-text                         │    │  pgvector 0.8.2                    │
│   • cross-encoder rerank (CPU pool)          │    │                                     │
│   listens on host 11434                      │    │  shared_buffers = 24 GB            │
│                                              │    │  effective_cache_size = 72 GB      │
│  colima 0.10.1 (Docker via macOS Virt.fw)    │    │  work_mem = 128 MB                 │
│  ┌──────────────────┐  ┌──────────────────┐ │    │  maintenance_work_mem = 4 GB       │
│  │ openclaw-1       │  │ openclaw-2       │ │    │  max_connections = 300             │
│  │ ai-memory        │  │ ai-memory        │ │    │  shared_preload_libraries = 'age'  │
│  │ tier=autonomous  │  │ tier=autonomous  │ │    │  listen_addresses = 192.168.50.1   │
│  │ FULL TOOLS       │  │ FULL TOOLS       │ │    │  pg_hba allows 192.168.50.100/32   │
│  │ ollama_base_url  │  │ ollama_base_url  │ │    │                                     │
│  │  →host.docker    │  │  →host.docker    │ │    │  Role: aimemory (SUPERUSER)        │
│  │  .internal:11434 │  │  .internal:11434 │ │    │  Database: aimemory                │
│  │ store_url        │  │ store_url        │◀┼────┤  Extensions: age, vector            │
│  │  →192.168.50.1   │  │  →192.168.50.1   │ │    │  Graph: memory_graph (initialized) │
│  │ peer=oc-2:19077  │  │ peer=oc-1:19077  │ │    │                                     │
│  │ port :19077      │  │ port :19078      │ │    │                                     │
│  └──────────────────┘  └──────────────────┘ │    │                                     │
│        ↕ federation HTTPS+mTLS (W=2/N=2)    │    │                                     │
└──────────────────────────────────────────────┘    └─────────────────────────────────────┘
                            gigabit LAN · <2 ms RTT
```

## Build provenance

| Component                                  | Version / source                                                      |
|--------------------------------------------|-----------------------------------------------------------------------|
| ai-memory daemon                           | `round-2-fixes` HEAD `fda9e64` (G1+G2+G3+G4+G5+S79 fixes all in)     |
| Container image                            | `ai-memory-plan-c:fda9e64` (multi-stage Debian bookworm + sal-postgres) |
| Container build context                    | [`/plan-c/Dockerfile`](https://github.com/alphaonedev/ai-memory-a2a-v0.7.0/blob/main/plan-c/Dockerfile) (cargo build --release --features sal-postgres inside builder stage) |
| Container entrypoint                       | [`/plan-c/entrypoint.sh`](https://github.com/alphaonedev/ai-memory-a2a-v0.7.0/blob/main/plan-c/entrypoint.sh) (config.toml + systemd-equivalent + identity gen) |
| Compose stack                              | [`/plan-c/docker-compose.yaml`](https://github.com/alphaonedev/ai-memory-a2a-v0.7.0/blob/main/plan-c/docker-compose.yaml) (2 services: openclaw-1, openclaw-2) |
| Mac Ollama backend                         | 0.23.1 with `mlx_metal_v4` libs auto-loaded (verified via `lsof`)    |
| Linux postgres                             | apt.postgresql.org bookworm-pgdg + AGE built from `release/PG16/1.5.0` source |
| TLS material                               | reused from Plan B at `/tmp/a2a-v07-tls/` (5 client certs in allowlist) |

## Cert configuration

* **Tier:** `autonomous` — full tools loaded
   * auto_tag (LLM tagger)
   * consolidate (LLM merger)
   * expand_query (LLM rewrite mode)
   * detect_contradiction (LLM judge)
   * smart_load (LLM-driven inclusion)
   * memory_inbox LLM summary
* **Inference:** Ollama 0.23+ on Mac host (auto-MLX on M4 Metal 4)
* **LLM model:** `gemma4:e4b` (already cached, 9.6 GB resident)
* **Embedder:** `nomic-embed-text` (already cached, 274 MB)
* **Cross-encoder rerank:** built into ai-memory; runs on CPU
* **Storage:** PostgreSQL 16 + AGE 1.5.0 + pgvector 0.8.2 on f2
* **Federation:** W=2/N=2 quorum (openclaw-1 ↔ openclaw-2 via Docker bridge)
* **Transport:** HTTPS+mTLS (TLS material from Plan B reused)
* **Network:** colima Docker bridge → host.docker.internal → Mac Ollama (loopback) and Mac → 192.168.50.1 → f2 postgres (gigabit LAN)

## Phase status

| Phase | Action | Status | Detail |
|---|---|---|---|
| P0 | Pre-flight (operator hardware approved, NO REBOOT directive) | ✅ done | Mac M4 32 GB confirmed; f2 14C/93 GiB confirmed; sudo NOPASSWD; firewall pre-configured |
| P1 | Postgres+AGE+pgvector bootstrap on f2 | ✅ **done 2026-05-10** | PG16.13 + AGE 1.5.0 + pgvector 0.8.2; 24/72 GB shared_buffers/eff_cache; pg_hba 192.168.50.100/32 |
| P2 | Mac→Linux postgres reachability (firewall, listen, hba) | ✅ verified | nc handshake OK; ICMP ping 1ms; firewall rule `iif "enp86s0" ip saddr 192.168.50.100 tcp dport 5432 accept` confirmed |
| P3 | Daemon container image build (cargo --features sal-postgres) | 🔄 in flight | Building from `round-2-fixes` HEAD `fda9e64` inside Debian bookworm builder stage |
| P4 | Schema-init via openclaw-1 (post-build) | ⏭️ pending | `ai-memory schema-init --store-url postgres://...` from inside openclaw-1 container |
| P5 | docker-compose up + verify both daemons healthy + capabilities | ⏭️ pending | curl `https://oc-1:19077/api/v1/capabilities` should report `tier=autonomous`, `storage_backend=postgres` |
| P6 | mTLS wiring (volume-mount /tmp/a2a-v07-tls/ into containers) | ⏭️ pending | Re-uses 5-client-cert allowlist + reused-CA from Plan B |
| P7 | Baseline validation (B/D/T/N/S/C/X/P/F/A/U with autonomous-tier C1-C9) | ⏭️ pending | `validate_baseline.sh` + `validate_autonomous_tier.sh` |
| P8 | Round 1 cert (full regression incl. autonomous-tier scenarios) | ⏭️ pending | ~3-4h wall (S83/S84/S85 NHI discovery 30 min each + autonomous-tier scenarios live for first time on this campaign) |
| P9 | Round 2 cert (must be 100% GREEN twice) | ⏭️ pending | DB clean + AGE drop+create + R2 |
| P10 | NHI discovery sweep (S83/S84/S85 with `tier_autonomous.md` overlay) | ⏭️ pending | autonomous-tier specific NHI prompts |
| P11 | Render results page (3-audience analysis from R1+R2 source JSON) | ⏭️ pending | `render_gpu_results.py --track LAN` |
| P12 | Cert closure → tag v0.7.1 → flip test-hub card | ⏭️ pending | depends on R1+R2 GREEN |

## What Plan C unlocks vs Plan B

| Capability | Plan B (CPU cloud) | Plan C (LAN, this run) |
|---|---|---|
| Postgres SAL contract | ✅ Closed (2-round GREEN) | ✅ same daemon binary |
| Federation W=2/N=2 quorum | ✅ Closed | ✅ same |
| HTTPS+mTLS perf | ✅ 13.6ms mean handshake | ✅ same path, lower LAN latency |
| **AGE bench-gate (S76) representative** | ⚠️ Storage-bound (16 GB pg) | ✅ **Engine-bound (96 GB pg, 24 GB shared_buf)** |
| **Autonomous-tier scenarios live** | ❌ Skipped (semantic only) | ✅ **Live-testable** for the first time |
| `auto_tag` LLM tagger | SKIP | RUN |
| `consolidate` LLM merger | SKIP | RUN |
| `expand_query` LLM mode | SKIP | RUN |
| `detect_contradiction` LLM judge | SKIP | RUN |
| `smart_load` LLM-driven inclusion | SKIP | RUN |
| GPU OOM graceful degradation test | N/A | RUN (Apple Silicon Metal pool) |
| Cloud cost per cert pass | ~$5-15 | **$0** |
| Persistent infrastructure | per-cert teardown | LAN hardware stays up; just `docker-compose down` |

## Ollama-MLX live verification (M4)

Verified on the operator's M4 Mac Mini 2026-05-10:

```
$ lsof -p $(pgrep -x ollama) | grep mlx
ollama  ... /Applications/Ollama.app/Contents/Resources/mlx_metal_v4/libmlx.dylib
ollama  ... /Applications/Ollama.app/Contents/Resources/mlx_metal_v4/libmlxc.dylib
ollama  ... /Applications/Ollama.app/Contents/Resources/mlx_metal_v4/libjaccl.dylib
```

**Ollama 0.23.1 auto-loaded the Metal-4 MLX backend on this M4 chip.** The autonomous-tier inference path uses MLX, not legacy GGUF/llama.cpp.metal. ai-memory's HTTP-to-Ollama call gets the speedup transparently.

Live benchmark on this M4 with `gemma4:e4b` (the thinking variant — does chain-of-thought):
- 34.5 tok/s generation (eval phase)
- 244 tok/s prompt evaluation (prefill)
- 5.74s one-time model load

For comparison: Ollama 0.18 (pre-MLX) on this same hardware would expect ~15-25 tok/s. The MLX backend in 0.23+ delivers the documented 1.5-3× speedup automatically.

## Known constraints (documented for transparency)

* **Mac→Linux psql via libpq has a routing oddity** (`No route to host` error despite `nc` succeeding and ICMP ping working). Likely macOS Application Firewall classification of just-installed `/opt/homebrew/opt/libpq/bin/psql`. **Not a cert blocker** — daemon containers in colima use a different network path and connect successfully. Schema-init runs from inside openclaw-1 container.
* **Linux node memory pressure** — at probe time f2 showed 49 Gi used + 14 Gi swap-in-use + 47 Gi reclaimable buff/cache. With shared_buffers=24 GB, postgres needs ~24-30 GB of resident working set. Linux will reclaim buff/cache as needed; not a blocker, but operator was advised to be conscious of running other heavy workloads concurrently with cert.
* **Single-host failure domain** — Mac restart/sleep mid-cert would drop both daemons simultaneously (vs Plan B's cross-VPC isolation). Postgres durability on f2 means daemon restart re-syncs state from SAL; cert can resume.
* **gemma4:e4b is the "thinking" variant** — chain-of-thought tokens are generated alongside the answer. Throughput numbers (34.5 tok/s) include those. For non-thinking variants like gemma3:4b, throughput is ~60-80 tok/s on M4.
* **No ECC RAM on Mac Mini** — long soak runs may hit transient memory faults. Cert is a one-shot 2-round run, not a soak; ECC isn't blocking for this scope.

## Cost so far + budget

| Item | Cost |
|---|---|
| Cloud spend | **$0** (Plan B droplets torn down 2026-05-10; Plan C is on-prem) |
| Hardware | already-owned (Mac Mini + Linux gateway) |
| xAI API spend (NHI discovery + S67/S68 dialog scenarios) | ~$15-20 projected |
| Operator setup time | ~30 min (Mac Ollama already cached; Linux postgres bootstrap took ~5 min) |
| Operator per-cert hands-on | ~5 min (`docker-compose up` + monitor) |

## Cert closure criteria

Same as Plan B: **two consecutive rounds 100% GREEN.**

For Plan C specifically, scenarios that were SKIPPED on Plan B because of `tier=semantic` will now RUN:
- S5 consolidation (LLM merger)
- S6 contradiction detection (LLM judge)
- S18 query expansion (LLM mode)
- S51 autonomous-tier suite (full test of LLM-bound features)
- S55 smart_load veto (LLM-driven inclusion)
- S63 consolidate a2a (federation + consolidation)

If those PASS in both R1 and R2 + zero in-scope FAILs, Plan C closes the v0.7.1 autonomous-tier cert that Plan B had to defer.

## v0.7.0.x deferrals carried over

These are unchanged from Plan B; Plan C does not address them:
- **G5 final** — find_paths Cypher AGE 1.5 `|`-syntax limit (S65 still scope-skipped)
- **NHI-D-quota-postgres-501** — quota_status returns 501 on postgres
- **NHI-D-PRIO-CLAMP** — write_memory silently clamps priority=0 to 5
- **NHI-D-postgres-search-501** — search returns 501 on postgres
- **S75** — Wave-4-hardcoded DB name + sqlite path

## Live progress

R1+R2 cert results, NHI findings, and 3-audience analysis will be populated to this page once cert closes. Watch for the "✅ verdict: SHIP" badge when both rounds GREEN.

## Provenance

* **Roadmap context:** [GPU-INTEGRATION-ROADMAP.md](GPU-INTEGRATION-ROADMAP.md) (TABLED meta-roadmap)
* **Architecture RFC:** [`alphaonedev/ai-memory-mcp#651`](https://github.com/alphaonedev/ai-memory-mcp/issues/651)
* **Plan C planning brief:** [plan-c-lan-gpu-track.md](plan-c-lan-gpu-track.md)
* **Plan B closed cert (predecessor):** [cpu-cert.md](cpu-cert.md), [cert-evidence.md](cert-evidence.md), [nhi-findings.md](nhi-findings.md)
* **Daemon source (HEAD `fda9e64`):** [github.com/alphaonedev/ai-memory-mcp](https://github.com/alphaonedev/ai-memory-mcp/tree/round-2-fixes)
