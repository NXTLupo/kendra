# Hardware Gates — Do Not Skip

The repository starts in simulation mode. Real movement is unavailable until all five gates are true.

## Gate 1 — servo_mapping_verified

Resolve the RaspClaws-Metal servo map from the exact current vendor resource package and your actual assembled robot. Document every high-level joint/servo mapping in `manifests/hardware.yaml` and implement only verified vendor calls in `hardware/vendor/kendra_adeept_bridge.py`.

Do not guess channel numbers.

## Gate 2 — battery_path_verified

Before using loose cells, verify the exact battery holder, polarity, charging topology, protection behavior, and vendor instructions supplied with your current HAT/kit. Record what you verified and how.

Do not experiment with lithium cells by trial and error.

## Gate 3 — e_stop_verified

Verify with a multimeter and then with the robot that the physical emergency stop removes actuator power through the intended hardwired path. Test it without relying on Python, Linux, speech, Wi-Fi, or the agent.

## Gate 4 — cliff_array_verified

Mount and calibrate all four downward sensors. Test each sensor on every common floor surface and at representative table/step edges. Verify front/rear/left/right movement blocks correctly and that the agent can be killed without disabling the reflex process.

## Gate 5 — motion_calibrated

On a padded floor area, establish safe values for:

- maximum steps per command
- maximum turn per command
- minimum/maximum speed
- movement timeout
- continuous servo duty limit
- required rest time
- obstacle hard-stop distance

Store those measured values in local config.

## Enabling hardware

After all tests pass, edit **only your local uncommitted config**:

```yaml
project:
  mode: hardware
hardware_gates:
  servo_mapping_verified: true
  battery_path_verified: true
  e_stop_verified: true
  cliff_array_verified: true
  motion_calibrated: true
body:
  driver: raspclaws
reflex:
  sensors:
    provider: mcp23017
```

Then run:

```bash
kendra gates
kendra doctor
```

Do not enable autonomy at the same time. Qualify manual tool-driven movement first.
