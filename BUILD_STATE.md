# Kendra build state

This repository is intentionally split into a **Virtual Kendra desktop milestone** and a later **physical-body milestone**.

## Virtual Kendra software state

| Capability | State | Notes |
|---|---|---|
| Core Python package | IMPLEMENTED | Unit-tested simulation-safe services |
| Cross-platform local process manager | IMPLEMENTED | `kendra dev start/stop/status` |
| Intel iMac bootstrap | IMPLEMENTED | `scripts/bootstrap_intel_macos.sh`; targets Darwin `x86_64` |
| Local text LLM client | IMPLEMENTED | loopback llama.cpp only |
| Low-latency streamed voice response path | IMPLEMENTED | bounded planner -> streaming LLM -> phrase-streamed Piper |
| Wake phrase `Kendra` | IMPLEMENTED | local Vosk grammar |
| Emotional/prosodic voice profiles | IMPLEMENTED | Piper synthesis parameters |
| Native Kendra Brain | IMPLEMENTED | SQLite/FTS5, embeddings, provenance, corrections, backup/export |
| Separate biometric identity store | IMPLEMENTED | consent-gated local embeddings/encounters |
| Local face recognition | IMPLEMENTED | YuNet + SFace |
| Semantic local vision adapter | IMPLEMENTED | loopback multimodal llama.cpp endpoint |
| Webots body bridge | IMPLEMENTED | same high-level body API as hardware |
| 18-joint visual gait articulation | IMPLEMENTED | coxa/femur/tibia digital-twin animation |
| Webots cliff/obstacle telemetry | IMPLEMENTED | feeds independent reflex layer |
| Coding-agent bootstrap prompt | IMPLEMENTED | `START_HERE_CODING_AGENT.md` |
| Automated unit tests | PASS | run `pytest -q` |
| End-to-end Intel iMac qualification | NOT RUN HERE | must be run on the user's actual iMac with audio/webcam/Webots/models |

## Physical body gates

Change a physical gate to `PASS` only after completing the matching test in `docs/KENDRA_BUILD_GUIDE_V3.md` and recording evidence.

| Gate | State | Evidence required |
|---|---|---|
| Adeept Metal resource archive identified | PASS | Current vendor Metal resource package identified |
| 17-servo / controller-channel mapping verified on this exact kit | NOT VERIFIED | Record `hardware/wiring/servo-map.csv` before hardware motion |
| Battery protection/balancing/charge path verified | NOT VERIFIED | Do not infer from connector fit or marketing summary |
| Physical e-stop + fuse installed and continuity-tested | NOT VERIFIED | Required before autonomous movement |
| Four cliff sensors installed/calibrated | NOT VERIFIED | Required before autonomous movement |
| Motion calibration completed | NOT VERIFIED | Required before agent motion |
| Power torture test completed | NOT VERIFIED | Required before sustained mixed workload |
| Baseline v1 physical acceptance suite | NOT RUN | Store results under a future baseline artifact directory |

The production example therefore keeps every hardware gate `false`. Real motion is fail-closed.


## 2026-08-18 — Virtual Kendra feature-complete for transplant rehearsal

- Turn telemetry: every turn stores timings metadata (kind/sight/search/total);
  the desktop app renders them in the live transcript (app-only display).
- Curiosity loop live: ambient sight -> observation memory -> reflex-gated
  two-step approach (verified against the real webcam; body event + memory
  recorded). Same code drives the RaspClaws walk verb.
- Build documentation rewritten: docs/TRANSPLANT_GUIDE.md (authoritative,
  Pi 5 16GB + NVMe + RTC), docs/ARCHITECTURE_CURRENT.md (compact truth),
  scripts/sync_architecture_memory.py (run after every architecture change —
  keeps Kendra able to discuss her own systems and build steps by voice;
  verified: she recites her future computer and build phases correctly).
- Webots: config/webots.yaml verified loading against the current stack
  (webots body driver, port 8765). To rehearse embodied motion: open
  simulator/webots/worlds/kendra_virtual.wbt in Webots, then run services
  with KENDRA_CONFIG=config/webots.yaml — curiosity approaches then move the
  3D twin instead of the abstract sim.
