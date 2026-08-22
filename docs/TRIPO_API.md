# Tripo 3D API — working notes

Written from the docs plus a full hands-on run against the live API on
2026-08-22 (model generated, rigged, six animations retargeted, 145 credits
spent). Where the docs and reality disagree, reality is recorded.

## Endpoints that actually work today

**v3 does not exist yet on this account, despite the migration notice.**
Every v3 path returns nginx 404:

    /v3/openapi/user/balance   404
    /v3/user/balance           404
    /v3/balance                404
    /v3/animations/rig-check   404

Everything below is v2 at `https://api.tripo3d.ai/v2/openapi`, which is
fully functional. Re-check v3 before relying on the newer verbs
(`/v3/animations/rig-check` is free and would have saved a rigging mistake).

| Verb | Path | Notes |
|---|---|---|
| GET | `/user/balance` | `{"code":0,"data":{"balance":455,"frozen":0}}` — `frozen` is work in flight |
| POST | `/upload` | multipart `file=`; returns `data.image_token` |
| POST | `/task` | all generation; body `{"type": ...}` |
| GET | `/task/{id}` | poll; `data.status` and `data.progress` |

`GET /task` (list) is **405** — there is no way to enumerate your tasks
through the API. This matters: lose a task id and you cannot recover the
job, only watch the credits stay frozen.

## Auth

`Authorization: Bearer <secret>`. The **client secret is the API key** —
`tsk_…`, 47 characters. Web-app credits and API credits are separate
wallets and are not interchangeable.

## Task types used here

```jsonc
// 1. Image to model — 20 credits, +10 with standard texture
{"type": "image_to_model",
 "file": {"type": "png", "file_token": "<from /upload>"},
 "texture": true, "pbr": true}

// 2. Rig it — 25 credits
{"type": "animate_rig",
 "original_model_task_id": "<model task id>",
 "out_format": "glb"}

// 3. One animation — 10 credits each, no batch discount
{"type": "animate_retarget",
 "original_model_task_id": "<RIG task id>",   // the rig, not the model
 "animation": "preset:walk",
 "out_format": "glb"}
```

Polling: jobs take 40–120 s. `status` runs `queued` → `running` →
`success`; failures give `failed`/`cancelled`/`banned`/`expired`.

## Animation presets

`preset:idle`, `preset:walk`, `preset:run`, `preset:climb`, `preset:jump`,
`preset:hurt` all work. **`preset:dance` does not exist** — it returns
`code 1004, "invalid animation name"`.

There is no endpoint that lists valid names, and the error message does not
enumerate them. Probing is awkward because the animation name is validated
*after* the model id, so a bogus model id makes every name look valid; and
against a real rig, a valid name **submits and bills immediately**. Budget
for discovery, or stick to the six confirmed above.

## The rigging trap (the expensive lesson)

Auto-rig succeeded on an eight-legged spider — and produced a **biped
skeleton**: `Root, Hip, Pelvis, L_Thigh, L_Calf, L_Foot, L_ToeBase,
L_CalfTwist01…` — 41 bones, humanoid throughout. Every retargeted clip then
drove a spider's body through a two-legged walk cycle and tore the mesh
into unusable spaghetti.

**Tripo's rigger assumes a humanoid.** For any non-humanoid creature,
expect the geometry to be fine and the animation to be worthless. The base
(unrigged) model is unaffected — same 248,043 vertices, intact — so the
salvage path is to render the unrigged model and animate it procedurally.

`POST /v3/animations/rig-check` is documented as **free** and reports
riggability before you spend. It is 404 today, but it is the first thing to
try when v3 lands.

## Asset shape

- One mesh, one material, three JPEG textures, embedded.
- **No morph targets.** Nothing to blend for blinks or expressions, so
  facial animation has to be geometry you add yourself.
- ~18 MB per animation GLB — each carries a full copy of the mesh, so six
  clips cost 110 MB for six short animations.
- Scale and origin are arbitrary; normalise height and re-centre on load.

## Costs (confirmed against the balance endpoint)

| Operation | Credits |
|---|---:|
| image_to_model + standard texture | 30 |
| animate_rig | 25 |
| animate_retarget (each) | 10 |
| upload | 0 |
| rejected request (bad params) | 0 |

$1 = 100 credits, flat, no volume discount; purchased credits never expire.
Free grant is 300 credits valid two weeks (legacy docs; not restated in the
current ones). This run: 30 + 25 + 60 = 115, plus 30 lost to an orphaned
task = 145 of 600.

## Operational rules learned

1. **Checkpoint the task id the instant it is returned**, before polling.
   An interrupt during the wait orphaned a running task and 30 credits with
   no way to recover it — there is no task-list endpoint.
2. **Cut the background out first.** Tripo reconstructs whatever fills the
   frame; her reference was a desert scene and the cacti would have become
   geometry.
3. **Crop to the subject and pad.** More of the frame filled by the subject
   means more mesh detail.
4. Rejected requests are free, so validation probing is cheap — but only
   where the parameter is validated before billing.
