# Kendra Runtime Architecture

## Process boundary

```text
                         +-------------------+
                         | local llama.cpp   |
                         | Qwen3-4B GGUF     |
                         +---------+---------+
                                   |
+-----------+              +-------v-------+              +-------------+
| voice     +------------->| agent/planner |<------------>| Kendra Brain|
| local ASR |              +-------+-------+              | SQLite/FTS5 |
| local TTS |                      |                      +-------------+
+-----------+                      |
                          validated tool calls
                                   |
          +------------------------+------------------------+
          |             |                 |                 |
      +---v---+     +---v----+        +---v----+       +---v-----+
      | body  |     | vision |        |research|       |delivery |
      +---+---+     +--------+        +--------+       +---------+
          ^
          | stop / block
    +-----+------+
    | reflex     |
    | independent|
    +-----+------+
          |
  cliff / obstacle /
  battery / duty state
```

The agent never imports vendor servo code. It can call only tools registered by `ToolRegistry`.

## Safety hierarchy

1. Physical battery/e-stop/fuse design
2. Independent reflex process
3. Body service motion clamps, deadlines, and fresh-reflex-heartbeat requirement
4. Tool registry schemas and capability filtering
5. LLM planner

Higher layers cannot override lower layers.

## IPC

Kendra services use newline-delimited JSON RPC over Unix domain sockets under the runtime directory. Socket permissions are local-only and are suitable for a single-board appliance.

## Fail-closed movement

Before every movement command, `BodyService` checks the reflex state file and requires:

- file exists
- JSON parses
- heartbeat age is below the configured maximum
- reflex reports healthy
- no stop state blocks the requested direction
- no mandatory rest period is active

Missing safety state means **no movement**.

## Motion duty tracking

`BodyService` writes a separate motion-state record containing start/end monotonic times. `ReflexService` reads it independently and enforces maximum continuous servo-active time and minimum rest time.

## Agent tools

The initial registry includes bounded forms of:

- walk
- turn
- pose
- stop
- look, only when hardware capability says it exists
- observe
- research
- recall
- add_goal
- add_question
- express
- deliver_photo

There is deliberately no shell command tool and no arbitrary path/network fetch tool.

## Research

Online:

```text
SearXNG -> result URLs -> SafeFetcher -> trafilatura -> source objects -> LLM synthesis
```

Offline:

```text
Kiwix search -> local article -> source object -> LLM synthesis
```

The model receives source IDs created by code. It is instructed to cite only those IDs.

## Application state vs durable state

Application code belongs in Git/A-B slots. Durable state belongs outside the slot:

- brain database
- photos
- delivery outbox
- face embeddings
- logs/exports
- local secrets/config

This separation is mandatory for safe upgrades.
