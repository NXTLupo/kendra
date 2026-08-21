# Should Kendra run the DeerFlow harness? — assessment, 2026-08-21

**Verdict: no on the iMac, no on the Pi, yes to one of its ideas — which is
now implemented natively.**

## What DeerFlow is

ByteDance's open-source SuperAgent harness (80k stars): LangGraph
multi-agent orchestration, a Docker AIO sandbox (browser + shell + VSCode
server), progressive file-based skills, long/short-term memory, and
long-horizon tasks that run for minutes to hours.

## Why it does not fit this machine

From DeerFlow's own README sizing table:

| Their target | Minimum | Recommended |
|---|---|---|
| Local evaluation | 4 vCPU, 8 GB, 20 GB SSD | 8 vCPU, 16 GB |
| Docker development | 4 vCPU, 8 GB, 25 GB | 8 vCPU, 16 GB |
| Long-running server | 8 vCPU, 16 GB, 40 GB | 16 vCPU, 32 GB |

Two sentences from that same table decide this: **"These numbers cover
DeerFlow itself. If you also host a local LLM, size that service
separately"**, and **"macOS and Windows are best treated as development or
evaluation environments."**

Measured on Jonathan's iMac at the time of writing:

- 16 GB RAM, **6 cores** — below DeerFlow's recommended 8 vCPU before
  Kendra is considered at all
- **5.2 GB free**, with **16 GB of swap already in use**
- Kendra already resident at **4.7 GB** (llama servers mlocked so macOS
  cannot page her brain out — the fix for a measured 117-second reply)
- Docker Desktop already running for the CX Workbench apps

So DeerFlow's *minimum* for itself is roughly the entire free memory of the
machine, and its recommended core count exceeds what the machine has. We
also have direct evidence of the failure mode: earlier in this project, a
second llama-server plus Docker containers competing for these six cores
produced a **117-second** spoken reply and a load average of 57.

## Why it does not fit the Pi

Jonathan's own research says it plainly: the Pi can only act as a light
client to a hosted instance. That requires cloud APIs, which violates
Kendra's first invariant — local inference by default, fully functional
offline — and the project's no-Docker rule for the robot.

## What was worth taking

One thing: **long-horizon planning and sub-tasking.** Kendra could answer
one question well but had no way to work a request with stages ("research
X, then compare it to what I like"). That gap was real.

`kendra/agent/orchestrator.py` implements it natively — the idea without
the infrastructure:

- plans at most **four** concrete steps, each bound to an existing tool
  (research / recall / look / think)
- runs them **one at a time on the planner slot**, never in parallel with a
  spoken turn (the half-duplex law that this CPU forced on us)
- each step is timed and bounded; failures are reported as failures
- synthesis may use **only** what the steps actually returned
- cancellable between steps, so "stop" still works
- triggers only on genuinely multi-part requests, verified against
  single-question phrasings so ordinary conversation pays nothing

Measured: "Research what year Iron Maiden formed and then tell me how that
fits with the music I like" — research step 1.0 s, reasoning step 12.9 s,
**28.2 s total**, answer grounded in the retrieved source.

No Docker, no LangGraph, no cloud model, no sandbox, ~250 lines, and it
transplants to the Pi with everything else.

## If DeerFlow is ever wanted anyway

Run it on a separate machine (the NUC/mini-PC its docs recommend) and let
Kendra call it as one more research tool over the LAN. That keeps her
local-first guarantee intact: she still works with the network unplugged,
and the harness becomes an optional accessory rather than a dependency.
