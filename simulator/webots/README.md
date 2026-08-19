# Virtual Kendra — Adeept RaspClaws-family Webots digital twin

Virtual Kendra is the desktop body for the complete Kendra cognition stack. It is designed to let the agent, reflex process, voice system, vision/identity system, Second Brain, autonomy logic, and body contract run before physical servos are enabled.

## What the model represents

The Webots world is an articulated six-leg representation of the Adeept RaspClaws family and is intended to converge toward the current RaspClaws-Metal chassis after the exact physical robot is measured. Each virtual leg has visible coxa/femur/tibia articulation, giving the simulator 18 modeled leg joints for gait development. This is a simulation abstraction; it does **not** assert that the current Metal kit exposes 18 independently mapped physical servo channels.

The current RaspClaws-Metal's advertised servo count and the Robot HAT channel count remain a physical verification gate. Never copy simulated joint indices into hardware PWM configuration without completing `docs/HARDWARE_GATES.md`.

## Launch

1. Install a Webots build compatible with the Intel iMac.
2. Open `worlds/kendra_virtual.wbt`.
3. Let the `kendra_bridge` controller start.
4. Start Kendra with `config/webots.yaml`.

The controller exposes a JSON-lines bridge only on `127.0.0.1:8765`. `WebotsBodyDriver` and the Webots reflex sensor provider use this bridge.

## Shared body contract

Virtual and physical drivers expose the same high-level operations:

- `walk(direction, steps, speed)`
- `turn(degrees, speed)`
- `pose(name)`
- `look(pan, tilt)` when the configured body has a gimbal
- `stop()` / emergency stop path
- front-distance telemetry
- cliff telemetry
- battery/pose telemetry where supported

The agent never directly manipulates Webots transforms or physical servo pulse widths.

## Test world

`kendra_virtual.wbt` contains:

- a finite raised platform with real visual edges;
- four simulated cliff sensing positions;
- a forward obstacle;
- a human-sized target;
- a visual home/perch target;
- Kendra's articulated body and head.

This lets you watch directionality, tripod-style gait intent, turns, head aiming, obstacle stops, cliff stops, planner decisions, and recovery behavior.

## What must be calibrated later

The digital twin is deliberately **not** authoritative for real:

- PWM channels or servo centers;
- final 17-servo mapping;
- joint zero offsets;
- link dimensions;
- mass/inertia and center of gravity;
- AD002 torque/speed behavior;
- foot friction;
- current limits;
- battery/charger topology.

Record those from the assembled Metal robot, then update a hardware calibration file and the simulator together. The simulator should become a progressively better digital twin; it must never be used to invent missing hardware facts.
