# GPU Integration Roadmap (ai-memory)

!!! warning "Status: TABLED — research & exploratory only"
    This roadmap is **on the radar but not committed to any release schedule**. It captures the multi-vendor GPU integration analysis (Apple MLX, NVIDIA CUDA, AMD ROCm, cloud DO multi-vendor, Enterprise swarm/hive scale, ULTRA-1 latency budget) so the work can be picked up coherently when prioritized.
    No engineering effort is allocated against this document at present. Schedule, scope, and dependencies will be revisited when the operator decides to activate the work — likely after v0.7.1 (Plan C) closes and DigitalOcean GPU access materializes.
    Issue tracking: [`alphaonedev/ai-memory-mcp#651`](https://github.com/alphaonedev/ai-memory-mcp/issues/651) (architecture RFC) + the dedicated GPU integration tracking issue (linked below).

> **Living document — research/exploratory.** Tracks ai-memory's path from Ollama-only inference to platform-native multi-vendor GPU acceleration spanning single-node, multi-node, swarm, and hive Enterprise AI Agent architectures.

**Last updated:** 2026-05-10
**Related:** [`alphaonedev/ai-memory-mcp#651`](https://github.com/alphaonedev/ai-memory-mcp/issues/651) (architecture RFC) · [v0.7.0 cert closure (Plan B)](cpu-cert.md) · [Plan C — LAN GPU track](plan-c-lan-gpu-track.md)

---

## AI NHI overarching assessment + call

**My call, as the AI driving this work end-to-end:**

1. **Ollama is the right unifier RIGHT NOW.** It runs on every platform, ships growing native acceleration (auto-MLX on Apple Silicon Metal-4 in 0.23+, llama.cpp.cuda on Linux NVIDIA, llama.cpp.rocm on AMD, llama.cpp.metal on older Macs), and operators love its ergonomics. ai-memory v0.7.0 ships with Ollama as the autonomous-tier inference path; nothing about that decision needs to change.

2. **For single-node + multi-node architectures**, Ollama scales fine. The HTTP overhead per call is real (~10-30 ms) but it's a fixed cost, not a slope. Most autonomous-tier workloads (auto_tag, consolidate, expand_query, detect_contradiction, smart_load) are inference-bound, not transport-bound — so the HTTP tax is in the noise.

3. **For swarm + hive architectures**, Ollama is **insufficient** as the only path. When dozens to hundreds of ai-memory daemons share inference compute, you need:
   - **PagedAttention** (vLLM) for ~5× throughput at multi-tenant scale
   - **TensorRT-LLM** for +30-70% throughput on NVIDIA
   - **In-process inference** (candle / mistralrs / mlx-rs) for the daemons that need every millisecond
   - **GPU memory budgeting / queue isolation** so one swarm member can't starve others
   This is v0.9.0+ work. Bounded but real.

4. **For ULTRA-1 ultra-autonomous tier**, the 50 ms p95 recall budget is **incompatible with vanilla Gemma-4 forward passes** even on Apple Silicon at MLX speeds. The unlocks (in priority order):
   - **MTP (Multi-Token Prediction)** — Gemma 4 native; Ollama 0.23 already wires it via `OLLAMA_MLX_MTP_MAX_DRAFT_TOKENS`. Speculative decoding variant for non-MTP-native backends.
   - **Distilled <1B model** for the recall hot-path; full Gemma-4 reserved for autonomous tier curator passes
   - **Specialized inference accelerators** at the silicon level (TensorRT-LLM int8, MLX 4-bit, Groq LPU if available)

5. **Multi-vendor parity matters for Enterprise.** Customers will not be uniform. AMD is closing the gap with NVIDIA. Apple Silicon has carved out a serious local-AI niche. Windows-on-NVIDIA is a real deployment target via TensorRT-LLM and ChatRTX. Plan ahead by treating each as a first-class backend behind cargo features.

6. **Cloud GPU access is currently blocked on DigitalOcean** for the operator's account (every GPU SKU returns `422 Size is not available in this region`). When DO grants access, test **both NVIDIA AND AMD** SKUs — `gpu-h100x1-80gb` ($3.39/hr) for the NVIDIA story and `gpu-mi300x1-192gb` ($1.99/hr) for the AMD story. Validating multi-vendor parity against real-world hardware is the load-bearing benchmark.

7. **The Mac M4 + LAN postgres path (Plan C)** is the highest-value v0.7.1 cert investment available right now. Zero cloud spend, AGE bench-gate validity restored, autonomous-tier scenarios live-testable, Ollama-MLX already auto-active.

**Decision: stage the work over v0.7.1 → v0.8 → v0.9 → v1.0 milestones, with Ollama as the always-available default.** Each milestone delivers something operators can ship; nothing requires a big-bang rewrite.

---

## Current state (v0.7.0 — closed)

| Aspect | State |
|---|---|
| Daemon inference path | HTTP to `ollama serve` (single backend) |
| Mac M3/M4 inference | **Auto-MLX** via Ollama 0.23+ Metal 4 (verified live on M4 Mac Mini 2026-05-10: `lsof` shows `mlx_metal_v4/libmlx.dylib` loaded) |
| Mac M1/M2 inference | MLX via Ollama 0.23+ Metal 3 (`mlx_metal_v3` libs ship in same Ollama install) |
| Linux NVIDIA inference | llama.cpp.cuda via Ollama (CUDA 12.x driver path) |
| Linux AMD inference | llama.cpp.rocm via Ollama (ROCm 6.x kernel path) |
| Windows inference | Ollama HTTP (Windows-native) or LM Studio for end-users |
| Enterprise multi-tenancy | not addressed — single Ollama serves single daemon today |
| ULTRA-1 hot-path | not implemented — autonomous tier is the highest tier shipped |
| MTP support | available behind `OLLAMA_MLX_MTP_MAX_DRAFT_TOKENS` env var (Apple Silicon Ollama 0.23+); not wired from ai-memory daemon yet |

---

## Enterprise AI Agent architectures we plan for

| Topology | Description | Inference shape | What's required |
|---|---|---|---|
| **Single** | one ai-memory daemon, one user | local Ollama or remote Ollama | what v0.7.0 ships |
| **Multi-node** | several daemons, federation, shared workspace | each daemon has its own local inference, OR shared Ollama via HTTP | works today via Ollama HTTP |
| **Swarm** | dozens of daemons (e.g. one per agent identity in an org) | shared inference cluster (vLLM / TensorRT-LLM cluster), per-tenant GPU budget | **v0.9.0** — pluggable backend + remote vLLM/TensorRT-LLM + multi-tenancy |
| **Hive** | hundreds-to-thousands of daemons, distributed mesh, possibly cross-region | inference is a managed service; daemon talks to the nearest pool | **v0.9.x+** — geo-aware inference routing, mesh telemetry, model-version pinning |
| **Ultra-Autonomous (ULTRA-1)** | LLM in recall hot-path on every read; 50 ms p95 budget | MTP + speculative decoding + distilled hot-path model | **v1.0.0** — MTP integration, speculative decode, distilled-model layer |

The single → multi-node → swarm → hive progression is **continuous, not stepped** — the same pluggable inference trait serves all of them, with different backend choices per topology.

---

## Performance requirements (per tier)

| Tier | Operation | p95 budget | Current Ollama-MLX | Gap |
|---|---|---|---|---|
| Semantic | recall (vector + lexical, no LLM) | 100 ms | ~50 ms | ✅ in budget |
| Semantic | search (multi-pass) | 200 ms | ~150 ms | ✅ |
| Autonomous | auto_tag (single LLM call) | 1500 ms | ~800-1500 ms | ✅ borderline |
| Autonomous | consolidate (multi-LLM) | 5 s | ~3-5 s | ✅ borderline |
| Autonomous | expand_query (LLM rewrite) | 1500 ms | ~600-1200 ms | ✅ |
| **ULTRA-1** | recall (with hot-path LLM) | **50 ms** | ~100-500 ms | ❌ **2-10× over budget** |

ULTRA-1 is the architectural feasibility test. Current MLX-on-M4 numbers don't fit; even Q4 quantization with MTP only halves the gap. Distilled <1B specialty models are the most likely path.

---

## Hardware backend matrix

### Apple Silicon (Mac)

| Generation | GPU compute | Best inference | Notes |
|---|---|---|---|
| M1 / M1 Pro / M1 Max | 8/16/32 GPU cores · Metal 3 | Ollama 0.23+ MLX (mlx_metal_v3) | unified memory ≤ 64 GB |
| M2 / M2 Pro / M2 Max / M2 Ultra | up to 76 cores · Metal 3 | Same | up to 192 GB unified |
| **M3 / M3 Pro / M3 Max** | hardware ray-tracing · Metal 3 | **Ollama 0.23+ MLX (mlx_metal_v3)** | 128-bit memory bus → 400 GB/s |
| **M4 / M4 Pro / M4 Max** | Metal 4 · improved tensor units | **Ollama 0.23+ MLX (mlx_metal_v4)** | observed ~34 tok/s on gemma4:e4b (thinking variant) |
| M5 (when released) | Metal 4 | Same MLX path; recompiled kernels | future |

**Direct MLX (mlx-rs, Python mlx-lm)** is ~10-20% faster than Ollama-MLX due to no HTTP serialization. For ai-memory: NOT WORTH the architectural complexity at v0.8 unless a specific use case demands. v0.8.1 optional cargo feature for power-user Mac users.

### Linux NVIDIA

| GPU class | Compute capability | Best inference path | Throughput-class |
|---|---|---|---|
| Consumer (RTX 3060/3070/3080/3090) | 8.6 | Ollama (llama.cpp.cuda) | single-user; ChatRTX on Win |
| Consumer (RTX 4060/4070/4080/4090) | 8.9 | TensorRT-LLM > vLLM > Ollama | +30-70% over Ollama |
| Pro (L4 / L40 / L40S) | 8.9 | vLLM (PagedAttention) | multi-tenant serving |
| **Pro (RTX 4000/5000/6000 Ada)** | 8.9 | **vLLM or TensorRT-LLM** | data-center serving |
| **Datacenter (A100 / H100 / H200)** | 8.0 / 9.0 | **vLLM (sole choice at scale)** | hyperscale; PagedAttention essential |
| Datacenter (B100/B200 future) | 10.0 | vLLM + future TRT-LLM | future |

### Linux AMD

| GPU class | Compute stack | Best inference path | Notes |
|---|---|---|---|
| Consumer (RX 7900 XT / XTX) | ROCm 6.3+ or Vulkan | Ollama (llama.cpp.rocm), LM Studio + Vulkan | high VRAM (24 GB) at lower price |
| **Pro (Radeon Pro W7900)** | ROCm 6.3+ | Ollama or vLLM-rocm | enterprise serving |
| **Datacenter (MI250X / MI300X)** | ROCm 6.x | **vLLM-rocm or TensorRT-LLM-rocm (when ported)** | MI300X has 192 GB HBM3 |

ROCm is closing the gap with CUDA but not at parity yet. Most AI tools default-target CUDA; ROCm requires explicit support. For Enterprise on AMD, plan for vLLM-rocm specifically.

### Windows

| GPU | Inference path | Notes |
|---|---|---|
| NVIDIA RTX 30/40-series | LM Studio (TensorRT-LLM), Jan.ai, Ollama | NVIDIA ChatRTX is platform-native |
| AMD RX 7000-series | LM Studio + Vulkan, Ollama (Windows native) | ROCm-on-Windows is partial |

### Cloud GPU (DigitalOcean) — currently blocked

DigitalOcean account `justin@alpha-one.mobi` does not currently have GPU droplet access. Every GPU SKU returns `422 Size is not available in this region` across every tested region (nyc1-3, tor1, sfo3, atl1, ams3, fra1, syd1).

When access is granted, **test both NVIDIA AND AMD SKUs** to validate multi-vendor parity:

| SKU | Vendor | VRAM | Hourly | Per-cert pass (~18h) | Plan C-equivalent topology |
|---|---|---|---|---|---|
| `gpu-4000adax1-20gb` | NVIDIA RTX 4000 Ada | 20 GB | $0.76 | ~$14 | 4× quad |
| `gpu-l40sx1-48gb` | NVIDIA L40S | 48 GB | $1.57 | ~$28 | 4× quad ($113 incl xAI) |
| `gpu-6000adax1-48gb` | NVIDIA RTX 6000 Ada | 48 GB | $1.57 | ~$28 | 4× quad |
| **`gpu-h100x1-80gb`** | NVIDIA H100 | 80 GB | $3.39 | ~$61 | 1× headline |
| `gpu-h200x1-141gb` | NVIDIA H200 | 141 GB | $3.44 | ~$62 | 1× headline |
| **`gpu-mi300x1-192gb`** | **AMD MI300X** | **192 GB** | **$1.99** | **~$36** | **AMD validation track** |
| `gpu-h100x8-640gb` | 8× NVIDIA H100 | 640 GB | $23.92 | ~$430 | hyperscale parity |
| `gpu-mi300x8-1536gb` | 8× AMD MI300X | 1.5 TB | $15.92 | ~$286 | hyperscale AMD |

Recommended cert tracks once DO unblocks:

* **Track Q-Nvidia**: 4× `gpu-4000adax1-20gb` openclaw + 1× `gpu-l40sx1-48gb` postgres+AGE → exercises NVIDIA stack at moderate cost
* **Track Q-AMD**: 4× `gpu-mi300x1-192gb` openclaw + 1× CPU postgres → exercises AMD ROCm path
* **Track HiveScale**: 1× `gpu-h100x8-640gb` for inference + N× CPU openclaws → simulates swarm/hive topology

---

## Backend integration LOE

(Mirrors RFC #651; restated here for self-contained roadmap.)

| Backend | Crate | Platforms | LOE | Perf vs Ollama-current | Risk |
|---|---|---|---|---|---|
| **Ollama HTTP** (current) | (existing) | All | 0 | baseline | none |
| `llama-cpp-rs` | 0.3 | Mac/Linux/Win | 3-5 days | -10 ms HTTP overhead/call | low |
| **`candle`** | 0.10 | Mac Metal + Linux CUDA | 1-2 weeks | +20-40% in-process | medium (gemma4 may need crate PR) |
| **`mistralrs`** | 0.8 | Mac Metal + Linux CUDA | 1-2 weeks | +30-50% serving-optimized | medium |
| `mlx-rs` | 0.25 | Apple Silicon | 1-2 weeks | parity with Ollama-MLX, no HTTP | medium (less mature) |
| **vLLM remote** | OpenAI HTTP | Linux clusters | 1 week | +200-500% throughput at scale | low |
| **TensorRT-LLM remote** | OpenAI HTTP | NVIDIA Linux/Win | 1 week | +30-70% on RTX 30/40 | low |
| ChatRTX (Win) | OpenAI HTTP | Win + RTX 30/40 | 2-3 days | end-user convenience | low |
| MLX-LM remote | OpenAI HTTP | Apple Silicon | 2-3 days | matches in-process MLX | low |

**Sum of all backends after Phase 1 (trait): ~6-8 weeks** of incremental work; can be parallelized.

---

## Phased delivery plan

| Version | Scope | LOE | Depends on |
|---|---|---|---|
| v0.7.0 | Ollama default; auto-MLX on Mac M3/M4 | shipped | — |
| **v0.7.1** | Plan C cert (Mac + Linux LAN postgres+AGE; autonomous tier live) | 2-3 weeks | operator Mac + Linux specs |
| **v0.7.2** | Patch deferrals (G5 final, NHI-D-quota, NHI-D-PRIO-CLAMP, NHI-D-search) | 1-2 weeks | post-cert backlog |
| **v0.8.0** | Pluggable inference trait + Ollama default + 2 in-process backends (`candle`, `llama-cpp-rs`) | **6-8 weeks** | RFC #651 review |
| v0.8.1 | `mlx-rs` Apple-only optimization | +1-2 weeks | v0.8.0 trait |
| **v0.9.0** | Enterprise polish (mTLS / multi-tenancy / GPU mem budgeting / SLO + circuit breaker / signed weights / audit) | **5-7 weeks** | v0.8.0 trait |
| v0.9.1 | Remote backends (vLLM, TensorRT-LLM HTTP clients) | 2-3 weeks | v0.9.0 polish |
| v0.9.2 | DO GPU multi-vendor cert (Track Q-Nvidia + Q-AMD) | 1-2 weeks | DO GPU access granted |
| **v1.0.0** | MTP + speculative decoding + ULTRA-1 hot-path | **5-7 weeks** | distilled-model decision |
| v1.0.x | Hive-scale telemetry + geo-aware inference routing | 3-4 weeks | v1.0.0 ULTRA-1 |

**Total v0.7 → v1.0 inference modernization: ~25-32 weeks** focused engineering (~6-8 months); ~12-15 calendar months alongside other v0.8/v0.9 features.

---

## Cloud GPU access tracking (DigitalOcean)

**Status:** denied / pending support response on operator account.

**Triggers to retest:**

| Trigger | Action |
|---|---|
| DO support ticket approved | Provision Track Q-Nvidia (4× `gpu-4000adax1-20gb` + 1× `gpu-l40sx1-48gb`) |
| DO unblocks AMD tier | Provision Track Q-AMD (4× `gpu-mi300x1-192gb`) |
| DO publishes new region | Re-test region availability for any GPU SKU |
| Account spend prerequisites met | Re-test self-service GPU droplet UI |

**Alternative providers** (in case DO never unblocks):

| Provider | Best for ai-memory cert | Pricing |
|---|---|---|
| **RunPod** | RTX 4000 Ada single-user | ~$0.34/hr (cheaper than DO) |
| **Lambda Labs** | A10 / A100 sustained | $0.75-1.10/hr |
| **Vast.ai** | Spot RTX 4000 Ada | ~$0.18/hr |
| **AWS g5/g6** | enterprise integration | $0.80-1.50/hr; service quota request needed |
| **GCP A2** | A100 cluster | $3.67/hr; quota request |
| **Azure ND** | NVIDIA DC | $3-30/hr; quota request |

If DO denial persists, RunPod is the fastest fallback (instant signup, no approval queue).

---

## Benchmark plan (when GPU access lands)

When any GPU access materializes, run the canonical benchmark suite to populate the perf matrix:

```
cargo bench --bench inference -- \
    --models gemma4:e4b,gemma4:e2b,gemma3:4b \
    --backends ollama,candle,mistralrs,llama_cpp_rs,mlx_rs \
    --workloads auto_tag,consolidate,expand_query,detect_contradiction,smart_load \
    --metrics p50,p99,tok_per_sec,time_to_first_token,gpu_mem_peak \
    --output benches/inference-matrix-$(date +%Y%m%d).json
```

Output: a per-backend × per-workload latency + throughput grid; published to GitHub Pages alongside cert results.

**Required runs** (when hardware unblocks):

| Run | Hardware | Models | Backends | Purpose |
|---|---|---|---|---|
| `bench-mac-m4` | this M4 Mac Mini | gemma4:{e4b,e2b}, gemma3:4b | Ollama, mlx-rs, candle, llama-cpp-rs | Apple Silicon baseline |
| `bench-do-nvidia` | `gpu-4000adax1-20gb` × 1 | gemma4:e4b | Ollama, candle, mistralrs, llama-cpp-rs | NVIDIA single-card |
| `bench-do-amd` | `gpu-mi300x1-192gb` × 1 | gemma4:e4b | Ollama, candle (rocm) | AMD single-card |
| `bench-do-h100` | `gpu-h100x1-80gb` | gemma4:e4b | Ollama, vLLM | datacenter NVIDIA |
| `bench-vllm-cluster` | 8× h100 | gemma4:e4b | vLLM | hyperscale headline |
| `bench-mtp-mlx` | this M4 Mac Mini | gemma4:e4b + MTP draft tokens | Ollama-MLX (with `OLLAMA_MLX_MTP_MAX_DRAFT_TOKENS=4`) | ULTRA-1 feasibility |
| `bench-mtp-vllm` | `gpu-h100x1` | gemma4:e4b + speculative decode | vLLM | NVIDIA ULTRA-1 path |
| `bench-distilled-1b` | TBD (when distilled model exists) | distilled-1b | candle, mistralrs | ULTRA-1 hot-path |

---

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| DO denies GPU access permanently | medium | Pivot to RunPod / Lambda Labs / on-prem (Plan C) |
| Each Rust ML crate has different model-format support; gemma4 newness | high | Pin gemma4-supported crate versions; fall back to GGUF via llama-cpp-rs for unsupported |
| In-process inference complicates daemon failure modes (OOM in ML kernel = daemon crash) | medium | Run inference in subprocess with supervisor; or sandbox via cgroups |
| MTP acceptance rate varies by prompt | medium | Always have non-MTP fallback path |
| ULTRA-1 50 ms p95 unattainable even with MTP | high | Treat as feasibility test; result may be "distilled model required" |
| 4 release engineering paths (Mac × Linux x86 × Linux ARM × Windows) | medium | Cargo features for compile; CI matrix for testing |
| Maintaining 6+ inference backends long-term | high | Designate 2 first-class (Ollama, candle); others community-supported |
| AMD ROCm not at NVIDIA CUDA parity for some operations | medium | Accept some perf delta; document explicitly |
| vLLM Python deps complicate ai-memory daemon distribution | medium | Use vLLM as remote backend (HTTP); not in-process |
| Speculative decoding draft model = 2× model footprint | medium | Cargo feature; per-deployment opt-in |
| DO GPU pricing changes mid-roadmap | low | Re-evaluate tracks against new prices; pricing cells in this doc are 2026-05 |

---

## Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-09 | Pivot v0.7.0 to Plan B (CPU + postgres+AGE) after DO GPU denial | Cert closure not blocked on GPU; autonomous-tier deferred to v0.7.1 |
| 2026-05-10 | v0.7.0 Plan B cert closed (2-round 100% GREEN) | 68 PASS / 0 FAIL / 14 SKIP × 2 |
| 2026-05-10 | Plan C designated (Mac M4 + Linux LAN postgres+AGE) | Closes v0.7.1 autonomous-tier cert without DO GPU access |
| 2026-05-10 | RFC #651 opened on `ai-memory-mcp` (pluggable inference backend trait) | v0.8 architecture work scoped |
| 2026-05-10 | This roadmap created | Track GPU integration progress + multi-vendor benchmarks |

---

## References

- **RFC #651** — pluggable inference backend trait architecture: https://github.com/alphaonedev/ai-memory-mcp/issues/651
- **v0.7.0 cert closure (Plan B)** — [`docs/cpu-cert.md`](cpu-cert.md), [`docs/cert-evidence.md`](cert-evidence.md), [`docs/nhi-findings.md`](nhi-findings.md)
- **Plan C (LAN GPU)** — [`docs/plan-c-lan-gpu-track.md`](plan-c-lan-gpu-track.md)
- **Cloud GPU plan that DO denied** — [`docs/v0.7.0-gpu-autonomous-track.md`](v0.7.0-gpu-autonomous-track.md)
- **NHI discovery layer** (used to surface bugs that motivated G1-G5 daemon fixes) — [`docs/nhi-findings.md`](nhi-findings.md)
- **Ollama 0.23 MLX integration** — https://ollama.com/blog/mlx
- **vLLM** — https://docs.vllm.ai/
- **NVIDIA TensorRT-LLM** — https://github.com/NVIDIA/TensorRT-LLM
- **AMD ROCm** — https://rocm.docs.amd.com/
- **Apple MLX** — https://ml-explore.github.io/mlx/
- **HuggingFace Candle** — https://github.com/huggingface/candle
- **mistral.rs** — https://github.com/EricLBuehler/mistral.rs
