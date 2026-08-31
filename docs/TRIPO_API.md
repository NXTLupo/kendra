# Tripo 3D API — working notes

Written from the full developer documentation (all eight pages, read
2026-08-22) plus a hands-on run against the live API the same day: a model
generated, rigged, six animations retargeted, 145 credits spent. Where the
docs and reality disagreed, reality is recorded.

## v3 is live — on a different host

An earlier version of this file said "v3 does not exist yet on this
account" because every v3 path returned nginx 404. That conclusion was
wrong, and the reason is worth writing down: **v3 is on a different
domain.** Probing `api.tripo3d.ai/v3/...` 404s forever, because v2's host
does not serve v3 at all.

| | Base URL |
|---|---|
| v2 (retiring) | `https://api.tripo3d.ai/v2/openapi` |
| **v3 (current)** | **`https://openapi.tripo3d.ai/v3`** |
| v3, China | `https://openapi.tripo3d.com/v3` |

`scripts/tripo_pipeline.py` still targets the v2 host and **will break when
v2 is retired**.

## Auth

`Authorization: Bearer <key>`. The **client secret is the API key** —
`tsk_…`, 47 characters. Shown once at creation. Web-app credits and API
credits are separate wallets and are not interchangeable.

## Response envelope

Every response is `{"code": …, …}`; `0` means success and anything else is
an error carrying `message` and `suggestion`.

```json
{"code": 0, "data": {...}}
{"code": 2010, "message": "Insufficient credits", "suggestion": "Please top up"}
```

Task statuses: `queued` → `running` → `success`, or `failed` / `cancelled`.

## v2 → v3, the shape of the change

Four structural changes, in order of how much code they touch:

1. **One endpoint became many.** v2 posted everything to `/task` with a
   `type` field. v3 has a dedicated path per capability and no `type`.
2. **All input fields became one.** `file`, `file_token`, `url`, `object`,
   `draft_model_task_id`, `original_model_task_id` are all now `input`,
   and the server infers the kind from the value (task id, file token, or
   URL).
3. **`/openapi` left the path**, so `/v2/openapi/…` is just `/v3/…`.
4. **Two fields were renamed**: `create_time` → `created_at`, and
   `consumed_credit` → `credits_consumed`.

| v2 | v3 |
|---|---|
| `POST /task` `type=image_to_model` | `POST /v3/generation/image-to-model` |
| `POST /task` `type=text_to_model` | `POST /v3/generation/text-to-model` |
| `POST /task` `type=animate_prerigcheck` | `POST /v3/animations/rig-check` |
| `POST /task` `type=animate_rig` | `POST /v3/animations/rig` |
| `POST /task` `type=animate_retarget` | `POST /v3/animations/retarget` |
| `POST /task` `type=texture_model` | `POST /v3/models/texture` |
| `POST /task` `type=convert_model` | `POST /v3/models/convert` |
| `GET /task/{id}` | `GET /v3/tasks/{id}` |
| `POST /upload` | `POST /v3/files` |
| `GET /user/balance` | `GET /v3/account/balance` |

Tripo publishes a migration Skill (`SKILL.md`) intended to be dropped into
a project and handed to an AI coding tool. Ours is a two-call surface, so
the table above is enough.

## What v3 adds that we actually needed

- **`POST /v3/tasks/list`** — batch task query. **v2 had no way to
  enumerate tasks** (`GET /task` is 405), which is exactly how 30 credits
  were orphaned here: an interrupt lost a running task's id and there was
  no endpoint to find it again. This closes that hole.
- **`GET /v3/account/usage`** — per-task credit history (`task_id`, `type`,
  `credits_consumed`, `created_at`).
- **`POST /v3/files/upload-credentials`** — direct upload credentials.
- **Webhooks**, so completion no longer requires polling.

## Credits are decimals, not integers

The August 2026 changelog exists to correct the docs on exactly this point:
`balance`, `frozen` and `credits_consumed` are JSON **numbers with up to
two decimal places** (`48.00`, `41465.00`). Parsing them with `int()` or
`parseInt` truncates or throws — VIP discounts produce fractional credits.

### Freeze/deduct model

Credits are **frozen** at task creation, **deducted** on success, and
**returned** on failure or cancellation. Failed and cancelled tasks cost
nothing. So the 30 "lost" credits from the orphaned task were only lost if
that task succeeded unobserved; `GET /v3/account/usage` can now settle it.

## Rate limits and concurrency

Two different limits, easy to confuse:

- **Rate limit** — request frequency, per **API key**. Exceeding returns
  HTTP 429 with `code 1007`. Every response carries `X-RateLimit-Limit`,
  `X-RateLimit-Remaining` and `X-RateLimit-Reset`.
- **Concurrency** — simultaneous running tasks, per **account**, pooled
  **per category**. Exceeding returns HTTP 429 with `code 2000` plus a
  `Retry-After` header. Categories are independent, so a full 3D pool does
  not block an animation task.

| Category | Default concurrent |
|---|---:|
| 3D generation, H series | 10 |
| 3D generation, P series | 5 |
| **Image generation** | **1** |
| Animation (rig, rig-check, retarget) | 10 |
| Model processing (texture, convert, refine) | 5 |
| Mesh operations | 10 |

Back off on `Retry-After` first, then `X-RateLimit-Reset`, then
exponential; poll tasks every 1–2 s, not faster.

## Webhooks

`task.completed`, `task.failed`, `balance.low` POSTed to an HTTPS endpoint
configured under Settings → Webhooks. Signature is
`Tripo-Webhook-Signature: t=<unix>,v1=<hex>`, an HMAC-SHA256 over
`{timestamp}.{raw_body}` with the `whsec_…` signing secret. Verify with a
constant-time compare, reject timestamps older than ~5 minutes, dedupe on
`Tripo-Webhook-Delivery`, and return 2xx within ~5 s.

Not worth wiring for Kendra: webhooks need a public HTTPS endpoint, and she
is deliberately offline-only. Polling is the right choice here.

## SDKs

Official SDKs exist for JS/TS, Python, Go, Rust and Java, each pinned to a
locked commit. **The Python SDK (`tripo3d==0.4.2`) is still v2** — the docs
list it as "V2 + limited V3", and it points at `api.tripo3d.ai/v2/openapi`.
JS, Go, Rust and Java are v3.

So for a Python client there is no v3 SDK to adopt: hand-rolled `httpx`
against the v3 paths above is the current best option, which is what
`scripts/tripo_pipeline.py` already does, modulo the base URL.

## The rigging trap — and how v3 fixes it

On v2, auto-rig succeeded on an eight-legged spider and produced a **biped
skeleton**: `Root, Hip, Pelvis, L_Thigh, L_Calf, L_Foot, L_ToeBase…`, 41
bones, humanoid throughout. Every retargeted clip then drove a spider
through a two-legged walk cycle and tore the mesh into spaghetti. That is
where "Tripo mangles non-humanoids" came from.

**That conclusion is obsolete.** v2 had one rigger and it was biped-only.
v3 has seven rig types and a rigging model built for the rest:

| `rig_type` | |
|---|---|
| `biped` | humanoid |
| `quadruped` | four-legged |
| **`hexapod`** | **six-legged — Kendra** |
| `octopod` | eight-legged |
| `avian`, `serpentine`, `aquatic` | winged, snake, fish |

Set `model: "v2.5-20260210"` for any non-humanoid; the default
`v1.0-20240301` is the old biped-only rigger. `spec` chooses bone naming,
`tripo` or `mixamo`.

**`POST /v3/animations/rig-check` is free** and returns
`{riggable, rig_type}` — it tells you which of the seven to ask for before
you spend anything. Call it first, always. It was unreachable during the v2
run only because it was being called on the wrong host.

## Animation presets — the real constraint

Getting a correct hexapod skeleton does **not** get you hexapod animation.
The preset library is overwhelmingly biped:

| Rig | Presets available |
|---|---|
| v1.0 biped | **90+** — `greet_01…04`, `sing_01…04`, `laugh`, `cry`, `wave_goodbye`, `dance_01…06`, `clap`, `hug`, `bow`, `cheer`… |
| v2.5 biped | 11 — idle, walk, run, dive, climb, jump, slash, shoot, hurt, fall, turn |
| v2.5 quadruped | 1 — `preset:quadruped:walk` |
| **v2.5 hexapod** | **1 — `preset:hexapod:walk`** |
| v2.5 octopod | 1 — `preset:octopod:walk` |
| v2.5 serpentine / aquatic | 1 each |

So for Kendra, Tripo can supply a correct six-legged skeleton and **one
walk cycle**. Greetings, singing, emotion and blinking do not exist for
hexapods at any price, and blinking never exists at all — Tripo emits no
morph targets. Those stay procedural, which is what `kendraStage.ts`
already does.

`preset:dance` (unprefixed) does not exist — `code 1004`. The v1.0 dances
are `preset:biped:dance_01…06` and are biped-only.

### Two flags worth knowing

- **`animations: [...]`** retargets a whole list in **one call**, instead
  of one request per clip.
- **`animate_in_place: true`** strips root displacement. For a character
  on a fixed stage this is what you want — otherwise a walk cycle
  translates her out of frame.

## Asset shape

- One mesh, one material, three embedded textures.
- **No morph targets.** Nothing to blend for blinks or expressions, so
  facial animation must be geometry you add yourself.
- ~18 MB per animation GLB — each embeds a full copy of the mesh, so six
  clips cost 110 MB. `export_with_geometry: false` avoids that when the
  base mesh is already loaded.
- Scale and origin arbitrary; normalise and re-centre on load.

## Costs (confirmed against the balance endpoint)

| Operation | Credits |
|---|---:|
| image_to_model + standard texture | 30 |
| rig | 25 |
| retarget (each) | 10 |
| rig-check | 0 |
| upload | 0 |
| rejected request (bad params) | 0 |

$1 = 100 credits, flat; purchased credits never expire. This run: 30 + 25 +
60 = 115, plus 30 to an orphaned task = 145 of 600.

## Operational rules learned

1. **Call `rig-check` before `rig`.** It is free, and it returns the
   `rig_type` to pass to `rig` — the difference between a hexapod skeleton
   and a biped one forced onto six legs.
2. **Checkpoint the task id the instant it is returned**, before polling.
   On v3, `POST /v3/tasks/list` is the recovery path if you miss it.
3. **Cut the background out first.** Tripo reconstructs whatever fills the
   frame; her reference was a desert scene and the cacti would have become
   geometry.
4. **Crop to the subject and pad** — more subject in frame, more mesh
   detail.
5. **Parse credits as floats.** They are decimals.
6. Rejected requests are free, so validation probing is cheap — but only
   where the parameter is validated before billing.
