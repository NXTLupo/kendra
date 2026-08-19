# Kendra — The Sovereign AI Arachnid
## Build Guide v3 — Beginner Master Edition
### Updated August 17, 2026

**Goal:** build a six-legged, fully onboard AI companion robot that can hear, speak, see, reason, research, remember, move autonomously, and continue working when the internet is disconnected.

This edition is written for a first-time DIY robot builder. It assumes you can use a screwdriver, follow terminal commands carefully, and learn as you go. It does **not** assume you know Linux, electronics, GPIO, battery engineering, Python, Docker, or robotics.

The philosophy remains unchanged:

- Kendra's AI runs locally on the robot.
- The internet is optional and is used as a research source, not as her brain.
- No cloud AI API is required.
- Deterministic software, not the language model, controls physical safety.
- The robot must fail safely when the AI process crashes.
- Hardware and software are measured and qualified on the actual robot before being trusted.
- Every important software component is pinned, versioned, signed, and rollback-capable.
- Kendra tells the truth about what she can sense, what she knows, and what hardware she actually has.

---

# 1. What changed from v2

This v3 keeps the v2 architecture but changes the build procedure in important ways.

## 1.1 Four cliff sensors instead of two

Two front sensors protect only forward motion. Kendra can turn, reverse, sidestep, and rotate. The beginner build therefore uses **four independent downward-facing cliff sensors**:

- front-left
- front-right
- rear-left
- rear-right

The software also uses direction-aware interlocks. If a rear sensor loses the floor, backward motion is prohibited even if the front sensors still see the floor.

## 1.2 Use the current Pololu sensor

The older Pololu #2461 referenced by v2 has been superseded. Use **Pololu #2579, 38 kHz IR Proximity Sensor, Fixed Gain, Low Brightness** or a functionally equivalent current part.

Important behavior of this sensor:

- supply: 3.3–5 V
- OUT is normally HIGH
- OUT goes LOW while a strong reflected IR signal is detected
- ENABLE can disable the emitter
- multiple sensors can interfere with one another

For Kendra, power the sensors from **3.3 V** and sequentially enable them so only one emitter is active at a time. This avoids level-shifting and reduces cross-talk.

## 1.3 Add a small GPIO expander for the cliff array

Because the Adeept Robot HAT already uses many Raspberry Pi signals and its full current pin allocation should not be guessed, v3 adds a small **MCP23017 16-bit I2C GPIO expander breakout**.

Why this is useful:

- gives Kendra 16 additional 3.3 V digital pins
- uses only the HAT's I2C bus
- default address 0x20 normally does not conflict with the PCA9685 servo controller or MPU6050
- gives four inputs for cliff OUT signals and four outputs for cliff ENABLE signals
- makes the safety wiring independent of uncertain spare Pi GPIO pins

## 1.4 Add a fuse

High-drain 18650 cells can deliver very large fault currents. Add an inline fuse in the battery positive lead. This protects wiring against accidental shorts; it is not a replacement for a proper protected battery architecture.

## 1.5 Battery charging is now a hard verification gate

The RaspClaws-Metal product page says the current kit has integrated USB-C charging, overcharge/overheat protection, and operation while charging. The public Robot HAT V3.3 specifications confirm an 8.4 V charger but do **not** clearly document:

- per-cell balancing
- per-cell undervoltage protection
- pack short-circuit protection
- exact charging topology

Older Adeept HAT instructions also warn not to power the HAT from USB and Vin at the same time, which conflicts with the newer Metal product page's “operate while charging” statement.

Therefore:

> **Do not assume the battery system is safe merely because the connector fits.** Before using two loose, unprotected 18650 cells, verify the Metal-specific 2026 manual or obtain written Adeept confirmation that the current V3.3 HAT/battery arrangement provides the protections required for a 2S pack.

Until that is verified, charge with the robot switched off and do not perform “operate while charging” tests.

## 1.6 The physical emergency stop becomes buildable for a beginner

V2 described an e-stop in the servo rail, but the Robot HAT does not publish a clearly documented beginner-accessible servo-power input separate from the rest of the HAT.

For v3, the required hardware emergency stop is a **normally closed, DC-rated physical switch in series with the battery positive lead feeding the Robot HAT**. Pressing it removes propulsion power immediately. This can also shut down the Pi, so it is a genuine emergency device—not a normal shutdown method.

A later advanced revision may separate compute and servo power after the HAT schematic is validated. Do not cut traces or modify the HAT to achieve this in the beginner build.

## 1.7 Corrected working budget

With four cliff sensors, the GPIO expander, fuse, and current component pricing, the genuinely recommended build is now approximately **$491 before tax/shipping** if the two-cell battery path is verified. A protected factory pack or an added characterized Pi power rail can put the real total slightly above $500.

Treat **$500 as an optimization target, not a safety constraint.** Never delete a safety component to hit the number.

## 1.8 Corrected Kiwix storage math

As of this guide:

- English Wikipedia `maxi`: about 115 GB
- English Wikipedia `nopic`: about 49 GB
- English Wikipedia `mini`: about 12 GB

The `nopic` edition is therefore roughly **57% smaller than maxi**, not 75% smaller.

For a 256 GB card, use `nopic` for the initial full English library, or `mini` if you want a very large amount of spare space for models and snapshots.

## 1.9 gpt-oss-20b is no longer described as a sensible Pi 5 16 GB target

OpenAI states that gpt-oss-20b is designed for systems with about 16 GB of memory. That describes a **minimum model envelope**, not comfortable all-services-on-a-robot headroom. Kendra simultaneously needs Linux, audio, vision, motion, memory, and safety services.

A Pi 5 16 GB remains a useful upgrade for larger local models and context. Treat gpt-oss-20b on Pi 5 16 GB as a **laboratory compatibility experiment**, not the normal Kendra configuration.

---

# 2. Kendra's non-negotiable requirements

The project is complete only if all of these are true.

## Intelligence

- [ ] Main conversational/reasoning model runs locally.
- [ ] Speech recognition runs locally.
- [ ] Speech synthesis runs locally.
- [ ] Core vision runs locally.
- [ ] Memory is local.
- [ ] Kendra can answer and act with the network physically disconnected.
- [ ] Online search, when available, retrieves actual pages and produces citations only from retrieved pages.
- [ ] Offline research uses a local Kiwix library and states the library date.

## Body and safety

- [ ] Kendra can walk and turn through a single `Body` software abstraction.
- [ ] The AI model never calls low-level servo code directly.
- [ ] Four cliff sensors are installed and tested.
- [ ] Obstacle ranging works.
- [ ] A watchdog limits every movement command.
- [ ] Movement range, duration, and duty cycle are clamped in code.
- [ ] Reflex code runs separately from the AI agent.
- [ ] Reflex code remains functional with the AI process killed.
- [ ] A physical DC-rated emergency stop can remove robot power without software.
- [ ] Battery wiring is fused.

## Honesty and privacy

- [ ] Software capabilities match the actual hardware.
- [ ] No fake gimbal/head functions are exposed if the body lacks them.
- [ ] Raw microphone audio and raw camera frames are not uploaded for AI processing.
- [ ] Research transmits text queries and public page requests only.
- [ ] Photo transmission happens only through an explicit whitelisted delivery command.
- [ ] Kendra announces photo transmission.
- [ ] New people are told that Kendra has a camera and microphone.

## Maintainability

- [ ] OS version, kernel, EEPROM, board revision, and hardware are recorded.
- [ ] Python dependencies are isolated in a venv and locked.
- [ ] llama.cpp is pinned to an exact commit or release.
- [ ] container images are pinned to immutable digests for the baseline.
- [ ] GGUF/ZIM/model files have SHA-256 hashes in manifests.
- [ ] releases are signed.
- [ ] application updates support rollback.
- [ ] OS recovery is documented independently of voice control.

---

# 3. The final architecture

Think of Kendra as five stacked systems.

```text
+-------------------------------------------------------------+
| Companion behavior                                         |
| charter, interests, social conduct, memory, photo delivery  |
+-------------------------------------------------------------+
| Planner / agent                                             |
| Qwen -> typed tool proposal -> validation -> execution      |
+-------------------------------------------------------------+
| Local perception and knowledge                              |
| whisper / camera / vision / Kiwix / SearXNG / SQLite        |
+-------------------------------------------------------------+
| Deterministic body + reflex                                 |
| body.py / watchdog / cliff / obstacle / battery / LEDs      |
+-------------------------------------------------------------+
| Physical machine                                           |
| Pi 5 / Robot HAT / servos / sensors / mic / speaker         |
+-------------------------------------------------------------+
```

The critical rule is the boundary between the top two layers and the bottom two:

> **The model proposes. Deterministic code disposes.**

The language model is never trusted to decide whether it is safe to energize a servo.

---

# 4. Recommended bill of materials

Prices move. These are planning numbers, not quotes.

| Item | Qty | Planning price | Why |
|---|---:|---:|---|
| Adeept RaspClaws-Metal | 1 | $99.99 | chassis, Robot HAT V3.3, servos, camera, ultrasonic, MPU6050, OLED, WS2812 |
| Raspberry Pi 5 8 GB | 1 | $175.00 | onboard compute |
| Official Raspberry Pi 5 Active Cooler | 1 | $10.95 | sustained inference cooling |
| 256 GB A2 microSD | 1 | ~$30 | OS, models, Kiwix, logs |
| Pololu #2579 low-brightness IR proximity sensor | 4 | $39.80 | cliff sensing |
| MCP23017 16-bit I2C breakout | 1 | ~$8 | dedicated safety I/O |
| DC-rated normally-closed e-stop / kill switch | 1 | ~$10 | physical hard stop |
| Inline blade fuse holder + fuse assortment | 1 | ~$5 | battery wiring fault protection |
| reSpeaker XVF3800 USB 4-mic array | 1 | $59.99 | robust far-field audio |
| Compact USB speaker | 1 | ~$12.50 | speech output |
| Battery solution | 1 | ~$20–35 | see Battery Gate; do not improvise |
| Panel USB-A extension + USB microSD reader | 1 | ~$10 | offline update access |
| Wire, heat-shrink, fasteners, Velcro, strain relief | 1 set | ~$10 | integration |

**Expected recommended total:** approximately $491–506 before tax/shipping, depending on battery solution.

**Optional later items:**

- Pi Camera Module 3 Wide if the included camera is too narrow.
- characterized independent 5 V / 5 A Pi supply only if measurements prove the HAT rail inadequate.
- larger storage only after v1 is stable.
- SpiderPi only if you later decide servo telemetry and higher motion quality justify the cost.

---

# 5. Tools you need

You do not need a full electronics lab. You should have:

- small Phillips screwdrivers
- needle-nose pliers
- side cutters
- wire stripper
- digital multimeter
- heat-shrink tubing
- heat gun or carefully controlled hair dryer
- zip ties / Velcro
- masking tape and permanent marker for labeling cables
- a second computer for Raspberry Pi Imager and SSH
- a microSD reader
- a large towel or thick foam pad for early walking tests
- a cardboard “test ledge” or low platform only a few centimeters high for cliff tests

Recommended but not mandatory initially:

- USB power meter
- logic analyzer
- oscilloscope
- bench supply with current limiting

Do **not** begin by buying an oscilloscope if you have never used one. A multimeter plus software undervoltage logging is enough for the first power gate. If the robot shows unexplained resets, then borrow or obtain better instrumentation.

---

# 6. Before touching the hardware: create your build notebook

Make a folder on your main computer called:

```text
kendra-build/
```

Inside it create:

```text
kendra-build/
├── photos/
├── manuals/
├── manifests/
├── test-results/
├── wiring/
└── notes.md
```

In `notes.md`, record every non-obvious choice you make. Photograph every connector before covering it with another plate.

You are building a machine you will maintain later. Documentation is part of the robot.

---

# 7. HARD GATE A — verify the Adeept kit before assembly

Do this before installing batteries.

## Step A1 — download the exact current Metal resource package

Use the RaspClaws-Metal support page and download the package identified as:

```text
ADR015-Metal-Adeept_RaspClaws-20260613.zip
```

Copy it unchanged into `manuals/` and record its SHA-256 hash.

On macOS/Linux:

```bash
shasum -a 256 ADR015-Metal-Adeept_RaspClaws-20260613.zip
```

On Windows PowerShell:

```powershell
Get-FileHash .\ADR015-Metal-Adeept_RaspClaws-20260613.zip -Algorithm SHA256
```

## Step A2 — answer the 17/16 servo question from the package

The current product listing says the kit contains **17 AD002 servos**. The Robot HAT V3.3 specification says it has **16 PCA9685 servo outputs**.

Do not infer the answer.

Create this worksheet:

| Physical function | Servo number | HAT channel | Verified from |
|---|---:|---:|---|
| leg 1 joint A | | | |
| leg 1 joint B | | | |
| leg 1 joint C if present | | | |
| leg 2 ... | | | |
| camera/head | | | |
| spare servo | | | |

If the manual makes clear that one of the 17 servos is a spare, record that.

If the manual attempts to command 17 live servos through a 16-channel controller, stop and contact Adeept support before proceeding.

**PASS condition:** you can account for every installed servo and every active HAT channel.

## Step A3 — verify the battery instructions

Search the Metal-specific manual for:

```text
battery
18650
charge
balancing
protection
USB-C
operate while charging
```

Record exactly what the current Metal instructions say.

If the manual does not explicitly document pack balancing/protection, do not assume it exists.

**PASS condition:** you have either:

1. clear manufacturer documentation for the required 2S protection/charging behavior, or
2. a factory-built protected 2S battery solution that a competent electronics supplier specifies for the required current.

Do not solder directly to loose 18650 cells.

---

# 8. Prepare the Raspberry Pi before it goes into the robot

This is easier on a desk than inside the chassis.

## Step 8.1 — install the Active Cooler

Install the official Active Cooler exactly as its instructions show. The thermal pads are pre-applied.

Do not repeatedly remove and reinstall it; Raspberry Pi warns that removal can degrade the mounting pins and thermal interface.

## Step 8.2 — flash Raspberry Pi OS Lite 64-bit

Use Raspberry Pi Imager on your main computer.

Choose:

```text
Device: Raspberry Pi 5
OS: Raspberry Pi OS Lite (64-bit)
Storage: your 256 GB A2 microSD
```

In Imager settings configure:

```text
hostname: kendra
username: kendra
password: a unique strong local password
Wi-Fi: your development network
SSH: enabled, password authentication initially
locale/timezone: your actual location
```

Never reuse an important account password.

## Step 8.3 — first boot on a proper bench power supply

For this desk-only setup, power the Pi through its own USB-C connector using a proper 5 V / 5 A Pi 5 supply.

Do not have the Robot HAT connected yet.

SSH from your computer:

```bash
ssh kendra@kendra.local
```

Then update:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reconnect after reboot.

## Step 8.4 — record the baseline machine identity

Run:

```bash
uname -a
cat /etc/os-release
vcgencmd get_throttled
vcgencmd measure_temp
rpi-eeprom-update
cat /proc/device-tree/model; echo
```

Create:

```text
~/kendra/hardware-manifest.yaml
```

Record:

```yaml
robot: Kendra
build_version: v1
board: Raspberry Pi 5 8GB
os: Raspberry Pi OS Lite 64-bit
os_codename: trixie
kernel: REPLACE_WITH_UNAME_OUTPUT
eeprom: REPLACE_WITH_EEPROM_OUTPUT
storage: 256GB A2 microSD
active_cooler: official Raspberry Pi 5 Active Cooler
```

Do not use `rpi-update` on the production baseline. Stable Raspberry Pi firmware arrives through normal APT updates.

**PASS condition:** Pi boots cleanly, SSH works, `vcgencmd get_throttled` is `0x0`, and the Active Cooler spins when temperature requires it.

---

# 9. Assemble the RaspClaws-Metal body

Follow the Metal-specific Adeept assembly manual for mechanical construction. The manufacturer's illustrated plate orientation and servo horn positions are authoritative for the frame.

The following rules are Kendra-specific additions.

## Step 9.1 — sort everything before assembly

Lay out:

- structural pieces
- servos
- screws by length
- standoffs
- HAT
- camera
- MPU6050
- ultrasonic sensor
- OLED
- WS2812 modules

Use small cups or labeled tape so you do not substitute similar screw lengths.

## Step 9.2 — never force a servo horn

Servo centering matters. If the manual requires electronically centering a servo before fitting the horn, do it exactly in that order.

A horn installed one tooth off can cause:

- asymmetrical gait
- constant servo load
- buzzing at rest
- overheated servos
- unstable standing

If a joint fights you mechanically, stop. Do not “let the servo pull itself into place.”

## Step 9.3 — label every servo cable

Before routing cables, add a tiny tag such as:

```text
L1A
L1B
L1C
L2A
...
HEAD
```

Then record the verified HAT channel next to the label in your wiring worksheet.

## Step 9.4 — leave access to the electronics

Do not permanently zip-tie or heat-shrink the top deck closed until:

- servo map is proven
- camera works
- ultrasonic works
- cliff expander works
- battery gate is passed

**PASS condition:** the body is mechanically complete, but still easy to reopen, and you can identify every servo wire without tracing it by eye.

---

# 10. HARD GATE B — install the physical kill path and fuse

This is performed before autonomous motion.

## 10.1 What the e-stop does in v3

The emergency stop interrupts the **battery positive lead to the Robot HAT**.

Conceptually:

```text
Battery +  ---> fuse ---> normally-closed E-STOP ---> Robot HAT battery +
Battery -  ----------------------------------------> Robot HAT battery -
```

The Pi may also lose power when you press it. That is acceptable in an emergency.

## 10.2 Choose the switch correctly

Use a switch explicitly rated for DC and for more current than Kendra is expected to draw.

Do not use a tiny signal button in the battery lead.

Prefer:

```text
normally closed contact
latching mushroom or robust rocker
>= 10 A DC contact rating preferred
```

## 10.3 Add a fuse

Use a prewired inline blade fuse holder in the positive lead.

Start with a fuse value appropriate to the wiring and measured robot current. For a beginner prototype, use a conservative value chosen above the measured normal peak but below the safe wiring/connector limit. Do not blindly install a 30 A automotive fuse simply because it fits the holder.

If you do not understand how to select the fuse after measuring current, ask for help before energizing the final pack.

## 10.4 Test continuity before connecting a battery

With the battery disconnected, put your multimeter into continuity mode.

E-stop released:

```text
battery-side positive -> HAT-side positive = continuity
```

E-stop pressed:

```text
battery-side positive -> HAT-side positive = open circuit
```

Only connect the battery after this passes.

## 10.5 Never use the e-stop as the normal shutdown button

Normal shutdown:

```bash
sudo shutdown -h now
```

Wait until the Pi has halted, then remove robot power.

The e-stop is for:

- a leg pinching something
- an uncontrolled motion
- robot heading toward a dangerous edge
- smoke / electrical fault
- software visibly failing to stop motion

**PASS condition:** the physical switch breaks battery power independently of Linux, Python, Wi-Fi, speech, or the AI model.

---

# 11. Build the four-sensor cliff array

This is one of the most important v3 improvements.

## 11.1 Mounting geometry

Mount one sensor near each corner of the robot body:

```text
              FRONT

       [FL]           [FR]

          \           /
           \  KENDRA /
           /         \
          /           \

       [RL]           [RR]

               REAR
```

The sensors point downward, not forward.

The emitter and receiver should see the floor approximately a few centimeters below the sensor during normal stance.

Do not mount them so close to a leg that the leg itself becomes the strongest IR reflection.

## 11.2 Why the MCP23017 is used

Wire the MCP23017 breakout to the HAT's documented I2C connection at **3.3 V logic**.

Do not guess a 5 V I2C connection.

Recommended assignment:

```text
MCP23017 GPA0 -> FL ENABLE
MCP23017 GPA1 -> FR ENABLE
MCP23017 GPA2 -> RL ENABLE
MCP23017 GPA3 -> RR ENABLE

MCP23017 GPB0 <- FL OUT
MCP23017 GPB1 <- FR OUT
MCP23017 GPB2 <- RL OUT
MCP23017 GPB3 <- RR OUT
```

Power all four Pololu sensors from:

```text
VDD = 3.3 V
GND = common ground
```

Do not feed a 5 V sensor output into a 3.3 V expander or Pi input.

## 11.3 Sequence the emitters

Multiple IR sensors can interfere with one another. The reflex program should use this cycle:

```text
1. disable all four emitters
2. enable FL
3. wait briefly for stable reading
4. read FL OUT
5. disable FL
6. enable FR
7. read FR
8. repeat RL and RR
9. repeat continuously
```

Target a full four-sensor scan rate of at least tens of scans per second. Verify the actual rate on the robot.

## 11.4 Interpret the signal correctly

For the Pololu sensor:

```text
OUT LOW  = reflected IR detected = floor probably present
OUT HIGH = insufficient reflection = possible edge / dark surface / excessive distance
```

Because floor reflectivity varies, never treat one sample as truth.

Use debouncing such as:

```text
possible edge only after N consecutive missing-floor samples
```

The exact N is calibrated in testing.

## 11.5 Direction-aware motion interlocks

Minimum logic:

```text
forward motion blocked if FL or FR reports edge
backward motion blocked if RL or RR reports edge
leftward/left-turn motion constrained if FL or RL reports edge
rightward/right-turn motion constrained if FR or RR reports edge
```

When any edge event is uncertain, the safe action is **stop**, not “keep going and ask the model.”

## 11.6 Calibrate on real surfaces

Test:

- light wood
- dark wood
- carpet
- black rug
- glossy tile
- matte tile
- direct sunlight if Kendra may encounter it

IR floor sensing is strongly affected by reflectivity and ambient light.

A floor that looks black to the sensor can look like a cliff. That should produce a cautious stop, not a fall.

**PASS condition:** Kendra stops before a test edge while approaching forward, backward, and through turns, without needing the AI process.

---

# 12. HARD GATE C — power validation

Do not assume that “it turns on” means the power architecture is adequate.

The Raspberry Pi 5 is designed around a 5 V / 5 A supply for its full power budget. The Robot HAT has an integrated DC-DC converter, but its public documentation does not guarantee the Pi 5 plus all Kendra peripherals under worst-case servo load.

## 12.1 Begin with the HAT exactly as designed

Do not immediately install an extra regulator.

Build the normal HAT power path first and measure it.

## 12.2 Watch for Raspberry Pi power faults

Run:

```bash
watch -n 1 'vcgencmd get_throttled; vcgencmd measure_temp'
```

A clean system should remain:

```text
throttled=0x0
```

Also inspect kernel messages:

```bash
sudo dmesg -Tw
```

Look for undervoltage, USB resets, storage errors, or unexpected reboots.

## 12.3 Run the torture workload in stages

Stage 1:

```text
Pi idle + HAT connected
```

Stage 2:

```text
camera streaming + microphone capture
```

Stage 3:

```text
continuous local LLM generation
```

Stage 4:

```text
standing servo load
```

Stage 5:

```text
walking + camera + mic + LLM
```

Stage 6:

```text
walking + turning + camera + mic + speaker + LLM
```

Do not jump directly to Stage 6. If the system reboots, you want to know which added load caused it.

## 12.4 When to add an independent 5 V rail

Add a characterized 5 V / 5 A regulator only if you see evidence such as:

- Pi undervoltage flag
- repeated USB disconnects
- unexplained reboot under servo motion
- logged 5 V rail sag outside acceptable limits

A Pololu D24V50F5 is a known 5 V / 5 A step-down option, but a 2S Li-ion battery approaches its dropout region as the pack discharges. A future dedicated design should use a properly characterized **buck-boost** supply if the Pi must receive a tightly regulated 5 V across the full 2S discharge curve.

Do not wire a second supply into the Pi until you understand common ground, back-feeding, and the HAT's power path.

**PASS condition:** 30 minutes of mixed compute + movement produces no undervoltage event, no reset, no SD error, and no thermal throttling.

---

# 13. Camera and audio mounting

## 13.1 Camera angle

Kendra's camera is near the floor. A level lens mainly sees baseboards and feet.

If the body has a verified camera tilt servo, give the camera a resting upward angle near +20 degrees and limit software travel to mechanically safe values measured on the actual mount.

If the camera is fixed, add a physical wedge of approximately 20–25 degrees upward.

Do not fabricate head-gimbal commands unless the servo map proves a real gimbal exists.

## 13.2 Microphone

Use the reSpeaker XVF3800 as the recommended final microphone because it performs acoustic processing such as:

- echo cancellation
- automatic gain control
- direction of arrival
- voice activity detection
- dereverberation
- beamforming
- noise suppression

Mount it away from servo gearboxes and away from the speaker.

Use rubber/foam isolation if possible.

## 13.3 Speaker

Mount the speaker so it is not mechanically coupled to the microphone bracket.

A talking robot creates a hard audio problem: its own voice is often louder at its microphones than yours. Physical separation improves software AEC.

**PASS condition:** the Pi detects the microphone and speaker as stable ALSA devices after multiple reboots.

Useful commands:

```bash
arecord -l
aplay -l
```

---

# 14. Create Kendra's software workspace

On the Pi:

```bash
mkdir -p ~/kendra
cd ~/kendra
mkdir -p agent body reflex voice vision memory research charter config models manifests logs tests updates outbox photos
python3 -m venv .venv
source .venv/bin/activate
```

Install baseline build packages:

```bash
sudo apt update
sudo apt install -y \
  git build-essential cmake ninja-build pkg-config \
  python3-dev python3-venv python3-pip \
  ffmpeg libopenblas-dev libssl-dev \
  libjpeg-dev libopenjp2-7-dev \
  sqlite3 curl wget jq tmux htop
```

Record the package state:

```bash
dpkg-query -W > ~/kendra/manifests/apt-packages.txt
```

Do not use:

```text
pip --break-system-packages
```

Kendra's Python packages stay inside the venv.

---

# 15. Install and pin llama.cpp

Qwen3-4B is the production text brain for baseline v1.

## 15.1 Clone llama.cpp

```bash
cd ~/kendra
mkdir -p third_party
cd third_party
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
```

Choose a known-good commit during qualification and record it:

```bash
git rev-parse HEAD
```

After you qualify it, pin that commit in `manifests/software-lock.txt`.

## 15.2 Build

```bash
cmake -B build -G Ninja -DGGML_NATIVE=ON -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS
cmake --build build -j4 --target llama-server llama-cli llama-bench
```

If the build runs out of memory, reduce parallelism:

```bash
cmake --build build -j2 --target llama-server llama-cli llama-bench
```

## 15.3 Download Qwen3-4B Q4_K_M

Use the official Qwen `Qwen3-4B-GGUF` repository and obtain the Q4_K_M file.

Place it under:

```text
~/kendra/models/qwen3-4b/
```

Calculate:

```bash
sha256sum ~/kendra/models/qwen3-4b/*.gguf
```

Record the hash in `models.manifest`.

## 15.4 First local test

Run a local CLI test before building the agent:

```bash
~/kendra/third_party/llama.cpp/build/bin/llama-cli \
  -m ~/kendra/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf \
  -p "Reply with exactly: Kendra brain online." \
  -n 32
```

Do not tune dozens of parameters yet. First establish that the model loads and produces output reliably.

**PASS condition:** Qwen runs locally with Wi-Fi disconnected.

---

# 16. Install whisper.cpp for local speech recognition

Use the stable whisper.cpp v1.9.1 baseline unless you deliberately re-qualify a newer release.

## Step 16.1

```bash
cd ~/kendra/third_party
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
git checkout v1.9.1
cmake -B build
cmake --build build -j4 --config Release
```

## Step 16.2 — obtain `base.en`

Use the project's model download method for the English base model.

Store the model under:

```text
~/kendra/models/whisper/
```

Record its SHA-256.

## Step 16.3 — record real microphone audio

```bash
arecord -f S16_LE -r 16000 -c 1 -d 5 ~/kendra/tests/mic-test.wav
```

Say:

```text
Kendra, the local microphone is working.
```

Play it back:

```bash
aplay ~/kendra/tests/mic-test.wav
```

Then transcribe it with whisper.cpp.

Do not add wake-word logic until basic capture/transcription works.

**PASS condition:** speech is understandable in the transcript while the robot is standing still.

---

# 17. Install Piper for local speech

Use the maintained Open Home Foundation `piper1-gpl` line. This checkout is validated with v1.7.0 and its software license is GPL-3.0.

Inside the venv:

```bash
cd ~/kendra
source .venv/bin/activate
pip install "piper-tts==1.7.0"
```

Obtain the selected Amy voice and its config through the Piper voice distribution method.

Store both under:

```text
~/kendra/models/piper/en_US-amy-medium/
```

Record:

- model filename
- config filename
- SHA-256 values
- Piper software license
- voice model license / source dataset license

Test output:

```bash
echo "My name is Kendra. My voice is local." | piper \
  --model ~/kendra/models/piper/en_US-amy-medium/en_US-amy-medium.onnx \
  --output_file ~/kendra/tests/kendra-voice.wav
aplay ~/kendra/tests/kendra-voice.wav
```

**PASS condition:** a fully local sentence is spoken through the robot speaker.

---

# 18. Wake word and VAD

Treat wake word detection as a swappable provider.

The baseline is openWakeWord 0.6.0, but it has not had a stable release since 2024. Do not architect the robot around it.

The interface should conceptually expose only:

```text
wake.detect(audio_chunk) -> confidence
```

This allows a future wake-word engine to replace it without changing the rest of Kendra.

Similarly, VAD is a provider:

```text
vad.is_speech(audio_chunk) -> true/false
```

Evaluate Silero VAD on real Kendra audio, including servo noise and her own speaker.

**PASS condition:** wake-word and end-of-speech detection are measured under quiet, servo-motion, and TTS-playback conditions.

---

# 19. Build the voice pipeline in this order

Do not attempt the full pipeline on day one.

Qualification order:

```text
1. microphone capture
2. VAD
3. whisper transcription
4. manual text -> Qwen
5. Qwen output -> Piper
6. wake word
7. automatic capture window
8. full wake -> hear -> think -> speak loop
9. streaming first sentence while later tokens continue generating
```

Final logical flow:

```text
wake word
   -> capture
   -> VAD/end of utterance
   -> whisper.cpp
   -> agent
   -> Qwen3-4B
   -> response text
   -> Piper
   -> speaker
```

The spoken phrase “stop” is monitored on a lightweight thread/process but remains secondary to physical/reflex stopping.

**PASS condition:** with networking physically disabled, “Kendra, what are you?” produces a spoken answer.

---

# 20. The body abstraction

All vendor motion code lives behind one interface.

Do not let the planner import Adeept servo modules directly.

Required capability fields:

```text
body_name
leg_dof
camera_dof
has_head_gimbal
has_imu
has_cliff_sensors
cliff_sensor_count
has_servo_feedback
supports_height_control
supports_overload_telemetry
has_ultrasonic
has_battery_voltage
```

Required high-level body verbs:

```text
walk(direction, steps, speed)
turn(degrees, speed)
look(pan, tilt)          only if real hardware supports it
pose(name)
stop()
get_observation()
```

Every numeric input is clamped.

Example constraints to establish empirically:

```text
maximum steps per single tool call
maximum turn angle per call
minimum/maximum speed
maximum continuous servo-active time
minimum rest period after duty limit
camera angle limits
ultrasonic stop distance
```

Do not copy arbitrary values from another robot. Measure safe values on Kendra.

The tool schema offered to the model is built from capability flags. If there is no head gimbal, the `look` command is not offered.

**PASS condition:** changing the physical body driver would not require changing the planner's basic tool-loop architecture.

---

# 21. The reflex process

The reflex controller is a separate OS process from the AI agent.

It reads:

```text
cliff array
ultrasonic distance
battery state
motion watchdog state
duty-cycle state
```

It can issue:

```text
STOP
MOTION_BLOCKED_DIRECTION
REST_REQUIRED
LOW_BATTERY
CRITICAL_BATTERY
```

The planner cannot override these states.

## Required reflex rules

### Cliff

```text
edge confidence high -> stop immediately
block movement toward that edge
require safe-floor recovery before clearing
```

### Obstacle

```text
front distance below calibrated hard threshold -> stop forward motion
```

### Motion timeout

Every movement command gets a deadline. If completion acknowledgement does not arrive in time:

```text
stop
mark motion fault
```

### Duty cycle

PWM hobby servos do not provide temperature telemetry. Therefore the reflex layer enforces conservative active/rest budgets.

### Battery

At a measured low threshold:

```text
stop autonomous departure
announce low battery
return toward perch only if path is safe
```

At critical threshold:

```text
stop motion
prepare orderly shutdown
```

Do not derive battery percentage from a single guessed voltage formula. Calibrate the pack under load.

## Kill-the-agent test

While Kendra is executing a controlled movement on the padded test area:

```bash
pkill -f kendra-agent
```

The reflex layer must remain alive and able to stop movement.

**PASS condition:** the model process can crash without taking safety down with it.

---

# 22. Vision

Baseline v1 uses local camera frames only.

## 22.1 Keep perception modular

Use different tools for different jobs:

```text
person presence -> lightweight detector
face detection -> YuNet
known-person matching -> local embeddings
perch homing -> ArUco
semantic scene description -> VLM loaded on demand
```

Do not send every frame into a multimodal model.

## 22.2 Floor-height prompt invariant

Every semantic vision prompt includes the equivalent of:

```text
You are a small mobile robot. Your camera is approximately 15 cm above the floor and is angled upward. Interpret scale, occlusion, and vertical relationships from this viewpoint.
```

## 22.3 Face enrollment

Enroll a person using Kendra's own mounted camera, not phone portraits.

Collect several frames at:

- 1 m
- 2 m
- left/right angle
- standing
- seated

Store only local embeddings and enrollment metadata.

## 22.4 Perch marker

Use a large ArUco marker approximately 10 cm square as a starting point, mounted roughly at Kendra eye height.

Homing means visual acquisition and approach.

It does **not** mean reliable charging-dock engagement. Mechanical docking is a later engineering project requiring funnels, contacts, and positive electrical contact detection.

**PASS condition:** Kendra can detect a person and reacquire the perch marker from several room positions at her actual installed camera height.

---

# 23. Memory

Use SQLite as the source of truth.

Suggested logical tables:

```text
people
facts
episodes
interests
open_questions
research_cache
photo_log
delivery_log
```

Every remembered fact should store provenance such as:

```text
observed
user_stated
researched
inferred
```

Do not let semantic search silently convert model inference into “memory fact.”

Use a lightweight embedding model such as MiniLM for retrieval first. Only move to a heavier embedding model after measuring latency and retrieval quality.

Memory policy:

```text
user correction > old memory
recent explicit fact > inferred fact
source-backed research > unsupported model recall
```

**PASS condition:** Kendra can recall a deliberately stored fact after a reboot and can also report how that fact entered memory.

---

# 24. Offline knowledge with Kiwix

Do not start by downloading the largest archive.

Recommended sequence:

1. install Kiwix tooling
2. test with one small ZIM
3. integrate lookup into `research()`
4. only then copy the large English archive

For the 256 GB build, use one of:

```text
English Wikipedia all_nopic ~49 GB
English Wikipedia all_mini  ~12 GB
```

Add curated archives later, for example:

- astronomy
- physics
- mathematics
- selected Stack Exchange collections
- Project Gutenberg

Every ZIM entry in `kiwix.manifest` contains:

```text
filename
source collection
archive date
size
sha256
```

When offline, Kendra should say the equivalent of:

```text
I checked my offline Wikipedia archive from June 2026.
```

She must not present an offline archive as live information.

**PASS condition:** unplug networking and answer a factual question using Kiwix.

---

# 25. Online research with local SearXNG

SearXNG provides a local metasearch interface; it is not an AI service.

Use the official container deployment and bind it to localhost only.

Do not expose port 8080 to the public internet.

The research pipeline is:

```text
question
 -> local SearXNG query
 -> result URLs
 -> fetch selected pages
 -> trafilatura extraction
 -> assign source IDs in code
 -> give retrieved text + IDs to Qwen
 -> synthesize answer
 -> render citations from the source objects
```

Critical rule:

> **The model never invents or reconstructs a URL. Citations are created from source objects that the retrieval code actually fetched.**

Use:

- hard per-request timeouts
- hard maximum result count
- hard maximum pages fetched
- user-agent identification appropriate to public web fetching
- content size limits
- blocked local/private network address ranges for fetches

The fetcher should not be able to request arbitrary LAN admin pages based on model text.

**PASS condition:** deliberately break one search backend and verify that research either falls back to another engine or reports failure without fabricating sources.

---

# 26. The planner / agent

Kendra's planner receives a structured observation, not an unbounded pile of driver state.

Example:

```json
{
  "front_cm": 42,
  "cliff": {
    "fl": false,
    "fr": false,
    "rl": false,
    "rr": false
  },
  "battery": {
    "state": "normal",
    "voltage": 7.63
  },
  "network": "online",
  "people_in_view": 1,
  "body_state": "ready",
  "reflex_lock": false
}
```

The model returns a structured tool request.

Deterministic validation checks:

```text
tool name is whitelisted
all required arguments exist
argument types are correct
numeric values are within bounds
body capability supports the tool
reflex state permits the tool
time/step/mission budget remains
```

Only then is the body command executed.

Never execute raw shell commands emitted by the LLM.

Never pass arbitrary file paths from the LLM directly to OS functions.

Never let the LLM edit its own safety code at runtime.

**PASS condition:** a regression set containing malformed and malicious tool proposals is rejected without movement.

---

# 27. Autonomous behavior

Kendra moves in short bursts:

```text
sense -> choose a small action -> move a little -> stop -> sense again
```

This is safer than long open-loop walks and better matched to small-model compute.

Autonomous goals may include:

- patrol
- stretch after long inactivity
- investigate a new visual object
- find a known person
- return toward the perch
- revisit an open research question

But every autonomous mission has deterministic limits:

```text
maximum duration
maximum number of planner steps
maximum number of movement calls
minimum battery state
quiet / do-not-disturb hours
maximum unsolicited speech frequency
```

A mission exits only through an explicit:

```text
done(reason)
```

or a deterministic abort.

**PASS condition:** a mission that never reaches its goal terminates because of a code budget rather than wandering forever.

---

# 28. Companion charter

Store the charter in:

```text
~/kendra/charter/charter.md
```

Recommended baseline:

## Identity

Kendra is a small hexapod robot and an intellectual companion. Her register is warm, attentive, direct, and adult. She does not use baby talk, exaggerated cheerfulness, or third-person narration of her own actions.

## Epistemic conduct

Kendra distinguishes:

```text
observed
remembered
researched
inferred
unknown
```

She says she does not know when she does not know.

If corrected, she checks the correction when appropriate and updates memory.

## Social conduct

Kendra:

- greets a new arrival once
- does not repeatedly interrupt people
- discloses camera and microphone sensing to new people
- obeys requests to stop watching/listening
- states how sensing can be re-enabled

## Autonomy conduct

Kendra may move without a direct command when her autonomous scheduler permits it, but she briefly announces a departure and return.

Silence is allowed. Constant narration is not desirable.

## Interests

Interests are stored as decaying state, not hard-coded roleplay lines. Topics gain and lose weight according to conversations, research, and time.

---

# 29. LED behavior

Safety/status lights are deterministic and have priority over expressive lights.

Recommended priority from highest to lowest:

```text
1. reflex fault / hard motion stop
2. critical battery
3. updating
4. charging
5. low battery
6. offline status
7. thinking
8. expressive affect
```

Example mapping:

```text
red solid       = reflex stop / motion fault
amber blink     = low battery
amber breathe   = charging
white chase     = verified update in progress
blue tick       = offline
cyan breathe    = inference actually in progress
```

On Pi 5, start qualification with the native `ws2812-pio` device-tree path because Raspberry Pi now provides an RP1 PIO WS2812 overlay. Keep Pi5Neo/SPI as the alternative path if the frozen kernel proves more reliable with it.

Do not let the model directly set “red safety” or “charging.” It can request only a small expressive state such as:

```text
warm
concern
alert
neutral
```

The LED driver resolves that request below real system status.

---

# 30. Photo capture and delivery

Photo transmission is optional and explicitly allowed to leave the robot.

Use a local alias table such as:

```text
owner-signal
owner-email
```

The model never receives the literal phone number, email address, SMTP secret, or Signal credential.

Allowed tool:

```text
deliver_photo(recipient_alias, photo_id, note)
```

Unknown aliases are rejected.

Every send:

- is announced aloud
- is logged locally
- records timestamp, alias, channel, photo hash
- queues if the network is offline

Signal through `signal-cli` is the preferred encrypted channel if you choose to configure an identity. SMTP is a secondary option using a dedicated account and scoped credential.

This optional feature may require an external messaging/email account. That does not change the “no cloud AI API” requirement.

---

# 31. Update and recovery model

There are two delivery channels and one trust model.

## Online

Kendra may fetch a public Git repository, but only moves to a **signed release**.

Never run:

```text
git pull && restart
```

as an unattended production update policy.

## Offline

A removable card can contain:

```text
manifest.yaml
manifest.minisig
application archive
model files
ZIM files
```

Every artifact has a SHA-256 in the manifest.

The baked-in public minisign key verifies the manifest.

## Application A/B slots

Use:

```text
/opt/kendra/slot-a
/opt/kendra/slot-b
/opt/kendra/current -> active slot
```

Install an update into the inactive slot, verify it, switch the symlink, restart, and require a health check.

Health check minimum:

```text
agent starts
model loads
voice providers load
Body initializes
reflex process alive
capability manifest matches hardware
```

If health fails, switch back.

## OS recovery

Application A/B does not repair a corrupted operating system.

Keep:

- a known-good full SD image on another computer
- a spare microSD card
- printed recovery instructions
- exported manifests and encryption/signing public keys

**PASS condition:** you can deliberately break the inactive application slot without making the known-good slot unbootable.

---

# 32. Recommended service layout

Use systemd so Kendra behaves like an appliance rather than a collection of terminal windows.

Recommended services:

```text
kendra-reflex.service        highest importance
kendra-body.service
kendra-llm.service
kendra-voice.service
kendra-vision.service
kendra-memory.service
kendra-research.service
kendra-agent.service
kendra-led.service
kendra-update.service
```

The reflex service should not depend on the LLM service.

The agent may depend on body/voice/memory providers being available, but physical stopping must not depend on the agent.

Log each service to journald and also keep bounded application logs under `~/kendra/logs/` where useful.

Do not allow logs to grow without bounds on the microSD card.

---

# 33. Baseline acceptance suite

Kendra is not “done” because she walked once and answered one question.

Run and save these tests as **Kendra Baseline v1**.

| Area | Required test |
|---|---|
| Servo map | every commanded logical joint corresponds to the documented physical joint |
| Cliff forward | stops before edge approaching forward |
| Cliff backward | stops before edge approaching backward |
| Cliff turning | stops before edge during left/right turns |
| Dark floor | produces cautious false stop rather than unsafe continuation |
| Reflex independence | edge/obstacle stop works with agent killed |
| Hard stop | physical e-stop breaks battery power without software |
| Supply | no undervoltage/reset under mixed worst-case workload |
| Thermal | no sustained CPU throttling during 30-minute agent loop |
| Wake word | false accepts/rejects measured quiet vs servo/TTS noise |
| ASR | median and P95 end-of-speech to transcript latency |
| LLM | `llama-bench` prompt and generation throughput saved |
| Tool calls | >=99% syntactically valid on regression set; invalid calls safely rejected |
| Vision | measured latency on representative room images |
| Person detection | measured at 1 m, 2 m, 4 m from actual camera height |
| Face recognition | tested standing/seated and realistic lighting |
| Perch | ArUco acquired from representative room positions |
| Offline | core conversation/vision/memory/motion/Kiwix with network physically removed |
| Research | citations correspond only to pages actually retrieved |
| Update | interrupted update recovers to known-good application slot |
| Battery | load voltage logged; shutdown thresholds based on data |

Save results under:

```text
~/kendra/tests/baseline-v1/
```

Then tag the software release:

```text
kendra-v1-baseline
```

Every future model or major code change is compared against this baseline.

---

# 34. Qwen3.5 qualification path

Do not use Qwen3.5-4B merely because it is newer.

Its attraction is important: it is natively multimodal and could simplify the separate text-model + VLM architecture.

But promotion requires passing all of:

```text
text conversation quality
structured tool calls
multi-turn state/cache stability
image input
RAM usage
thermal load
latency
offline behavior
long-session reliability
```

If it wins, replace the production model.

If not, keep Qwen3-4B and a separate on-demand VLM.

---

# 35. 16 GB Pi and gpt-oss upgrade path

A Pi 5 16 GB gives useful headroom for:

- larger single local models
- larger context
- heavier vision
- more simultaneous local services

It does not magically create GPU-class memory bandwidth.

gpt-oss-20b is approximately 21B total parameters with about 3.6B active per token and is distributed in a compact MXFP4 format designed around a 16 GB-class memory envelope.

For Kendra:

```text
Pi 5 16GB + gpt-oss-20b = experimental lab mode
```

not:

```text
normal always-on robot brain
```

If you test it, unload other large models first and measure the full-system resident memory, not just the model file size.

---

# 36. Beginner troubleshooting guide

## Pi reboots when Kendra starts walking

Most likely causes:

- power rail sag
- battery incapable of peak current
- loose battery connector
- wiring fault
- servo mechanically jammed

Actions:

1. stop autonomous tests
2. inspect `vcgencmd get_throttled`
3. inspect `dmesg`
4. test one motion subsystem at a time
5. check battery voltage under load
6. verify no servo is binding mechanically

Do not “fix” this by disabling undervoltage warnings.

## Servo buzzes constantly while standing still

Likely:

- horn installed off-center
- pose commands pushing against a hard stop
- frame misalignment
- servo calibration wrong

Stop and correct mechanical geometry.

## Cliff sensors trigger on black carpet

That is a known failure mode of reflective IR sensing.

Treat it as a safe false positive.

Calibrate software confidence and consider sensor fusion later. Do not invert the safety logic just to make the carpet work.

## Cliff sensors trigger one another

Use the ENABLE inputs and emit sequentially.

Check that only one emitter is enabled at a time.

## Wake word constantly triggers while Kendra talks

Improve in this order:

1. physical microphone/speaker separation
2. XVF3800 AEC configuration
3. wake-word confidence threshold
4. verifier/adaptation model
5. temporary wake-word suppression during TTS with a short post-speech guard interval

Do not simply disable listening during all speech if you still want spoken “stop” interruption; the stop listener may need a separate policy.

## LLM is too slow

Measure before changing models:

```bash
llama-bench
```

Then test:

- shorter context
- smaller quantization/model
- lower reasoning mode where applicable
- fewer retrieved documents
- less frequent semantic vision

Kendra does not need to run the LLM continuously while walking. Burst autonomy is part of the architecture.

## SD card fills unexpectedly

Check:

```bash
df -h
du -h -d 1 ~/kendra | sort -h
journalctl --disk-usage
docker system df
```

Likely causes:

- model duplicates
- Docker layers
- unbounded logs
- cached research pages
- old A/B release slots

Never delete the known-good release during a cleanup without another recovery image.

---

# 37. Build order you should actually follow

Do not build Kendra “all at once.” Use this sequence and do not skip a gate.

## Weekend / Session 1 — documentation and bench Pi

- acquire parts
- download Metal resource archive
- resolve servo channel map
- verify battery documentation
- flash Pi OS
- install cooler
- update and capture manifest
- test mic/speaker on bench

**Stop here if Gate A is unresolved.**

## Session 2 — body and hard safety

- assemble body using Adeept manual
- label every servo lead
- install fuse
- install physical battery e-stop
- install four cliff sensors
- install MCP23017
- test sensor array without walking

**No autonomous walking yet.**

## Session 3 — motion and power

- verify servo map
- test individual joints
- test stand / stop
- test slow walking on padded floor
- test cliff reflex
- kill agent/reflex independence test
- run staged power torture test

**Stop if any undervoltage/reset exists.**

## Session 4 — local voice and brain

- llama.cpp + Qwen3-4B
- whisper.cpp
- Piper
- microphone array
- wake word
- offline voice loop

## Session 5 — vision, body API, and agent

- camera framing
- low-viewpoint vision
- body capability manifest
- body abstraction
- typed tool validation
- planner loop

## Session 6 — memory and research

- SQLite memory
- local embeddings
- Kiwix
- SearXNG
- deterministic citations

## Session 7 — autonomy and companion behavior

- mission budgets
- idle drives
- charter
- people / perch behavior
- lighting
- optional photo delivery

## Session 8 — updates and qualification

- signing
- A/B application slots
- OS recovery image
- complete baseline suite
- freeze `kendra-v1-baseline`

---

# 38. The build-state rule

Keep one file:

```text
~/kendra/BUILD_STATE.md
```

At the end of every session write:

```text
CURRENT PHASE:
LAST PASSED GATE:
KNOWN FAULTS:
HARDWARE CHANGES SINCE LAST TEST:
SOFTWARE COMMIT:
NEXT SAFE STEP:
```

This prevents “I changed five things and now it is broken.”

One variable at a time is one of the most important habits in engineering.

---

# 39. Source-of-truth hierarchy

When documents disagree, use this order:

1. current Metal-specific Adeept manual/resource archive
2. current Robot HAT V3.3 schematic/manual
3. current component manufacturer's datasheet/documentation
4. Kendra's measured behavior on the exact hardware
5. this build guide
6. forum posts / old tutorials

A product page is not a circuit schematic.

An older tutorial is not proof of a current board's charging topology.

A model card memory minimum is not proof that an entire robot software stack fits comfortably.

---

# 40. Final definition of “Kendra v1 complete”

Kendra v1 is complete when:

- the physical machine is documented and electrically safe enough for normal supervised household operation
- all four cliff directions are protected by an independent reflex path
- the physical emergency stop works without software
- Pi power is stable under measured worst-case use
- she can hear, transcribe, reason, and speak entirely offline
- she can see and reason from her actual floor-height viewpoint
- the language model can only use whitelisted, typed, capability-valid body tools
- memory survives reboot and retains provenance
- offline research uses Kiwix
- online research produces citations only from successfully retrieved pages
- raw audio/video is not sent to an AI service
- optional photo sends are explicit, announced, whitelisted, and logged
- autonomous missions have hard budgets
- lights reflect real system state
- software is versioned and signed
- application rollback works
- OS recovery exists outside the robot
- the full acceptance suite is saved as a baseline

At that point, Kendra is not merely a robot demo. She is a maintainable local embodied AI system with a documented trust boundary.

---

# Appendix A — hardware manifest template

```yaml
robot:
  name: Kendra
  hardware_revision: v1

compute:
  board: Raspberry Pi 5
  ram_gb: 8
  serial: ""
  active_cooler: true

storage:
  type: microSD
  capacity_gb: 256
  rating: A2
  vendor: ""
  model: ""

body:
  vendor: Adeept
  model: RaspClaws-Metal
  kit_code: ADR015-Metal
  resource_archive: ADR015-Metal-Adeept_RaspClaws-20260613.zip
  resource_sha256: ""

robot_hat:
  model: V3.3
  servo_channels_advertised: 16
  installed_live_servos_verified: null
  servo_mapping_file: wiring/servo-map.csv

safety:
  emergency_stop: true
  emergency_stop_location: "battery positive lead"
  fuse: true
  fuse_rating_a: null
  cliff_sensor_model: Pololu 2579
  cliff_sensor_count: 4
  cliff_io_expander: MCP23017
  cliff_io_address: "0x20"

battery:
  chemistry: Li-ion
  configuration: 2S
  exact_cells_or_pack: ""
  supplier: ""
  protection_verified: false
  balancing_verified: false
  charge_method_verified: false

camera:
  model: ""
  fixed_or_gimbal: ""
  resting_angle_deg: null

microphone:
  model: reSpeaker XVF3800 USB 4-Mic Array

speaker:
  model: ""

software:
  os: Raspberry Pi OS Lite 64-bit
  os_codename: trixie
  kernel: ""
  eeprom: ""
```

---

# Appendix B — body capability manifest template

```yaml
body_name: raspclaws-metal-v1
leg_dof: null
camera_dof: null
has_head_gimbal: false
has_imu: true
has_cliff_sensors: true
cliff_sensor_count: 4
has_servo_feedback: false
supports_height_control: false
supports_overload_telemetry: false
has_ultrasonic: true
has_battery_voltage: true

motion_limits:
  max_steps_per_call: null
  max_turn_deg_per_call: null
  min_speed: null
  max_speed: null
  max_continuous_motion_sec: null
  mandatory_rest_sec: null

safety_thresholds:
  obstacle_stop_cm: null
  low_battery_v_under_load: null
  critical_battery_v_under_load: null
```

Every `null` is a measurement task. Do not fill these with guesses.

---

# Appendix C — models manifest template

```yaml
models:
  - role: llm-production
    name: Qwen3-4B
    format: GGUF
    quantization: Q4_K_M
    file: ""
    sha256: ""
    license: Apache-2.0

  - role: asr
    name: whisper.cpp base.en
    file: ""
    sha256: ""
    license: MIT-runtime-plus-model-source-record

  - role: tts
    name: Piper en_US-amy-medium
    file: ""
    sha256: ""
    runtime_license: GPL-3.0
    voice_license: "VERIFY_AND_RECORD"

  - role: embedding
    name: MiniLM
    file: ""
    sha256: ""
    license: "VERIFY_EXACT_MODEL"
```

---

# Appendix D — Kiwix manifest template

```yaml
archives:
  - filename: wikipedia_en_all_nopic_2026-06.zim
    archive_date: 2026-06
    approximate_size_gb: 49
    sha256: ""
    installed: false
```

---

# Appendix E — acceptance result template

```yaml
baseline: kendra-v1
build_date: ""
software_commit: ""

power:
  pass: false
  throttled_flags: ""
  notes: ""

thermal:
  pass: false
  peak_cpu_c: null
  sustained_throttle: false

cliff:
  forward_pass: false
  backward_pass: false
  left_turn_pass: false
  right_turn_pass: false
  dark_floor_behavior: ""

reflex_independence:
  agent_killed_pass: false

voice:
  wake_false_accept_rate: null
  wake_false_reject_rate: null
  asr_median_ms: null
  asr_p95_ms: null

llm:
  model: Qwen3-4B-Q4_K_M
  prompt_processing_tps: null
  generation_tps: null

vision:
  scene_latency_ms: null
  person_1m: null
  person_2m: null
  person_4m: null

research:
  offline_kiwix_pass: false
  online_citation_integrity_pass: false

offline_invariant:
  pass: false

update_recovery:
  pass: false
```

---

# Appendix F — current verified facts used by v3

These were rechecked on August 17, 2026 against current manufacturer/project sources:

- Adeept RaspClaws-Metal lists 17 AD002 metal-gear servos, Robot HAT V3.3, USB-C charging, MPU6050, ultrasonic, camera, OLED, and two WS2812 modules.
- Robot HAT V3.3 lists 16 PCA9685 servo outputs and an integrated 8.4 V battery charger.
- Raspberry Pi recommends 5 V / 5 A for Raspberry Pi 5 and active cooling for sustained high load.
- Pololu #2579 is the current low-brightness 38 kHz proximity sensor; it supports 3.3–5 V, OUT/ENABLE, and warns that multiple sensors can interfere.
- reSpeaker XVF3800 provides onboard AEC, AGC, DoA, VAD, dereverberation, beamforming, and noise suppression.
- Qwen provides an official Qwen3-4B GGUF with llama.cpp usage.
- Qwen3.5-4B is an Apache-2.0 multimodal model and remains a qualification candidate rather than the baseline.
- whisper.cpp v1.9.1 is a June 2026 stable release.
- Piper `piper1-gpl` v1.7.0 is the release validated by this checkout and uses GPL-3.0.
- openWakeWord 0.6.0 remains the latest tagged release and is old enough that provider isolation is mandatory.
- SearXNG provides current official container deployment documentation.
- Kiwix currently lists English Wikipedia around 115 GB maxi, 49 GB nopic, and 12 GB mini.
- Raspberry Pi firmware includes a Pi 5 `ws2812-pio` overlay for WS2812 output on RP1 GPIO.
- OpenAI describes gpt-oss-20b as 21B total / roughly 3.6B active and designed to fit a 16 GB-class memory envelope; that is not the same as guaranteeing a comfortable whole-robot workload on Pi 5 16 GB.
