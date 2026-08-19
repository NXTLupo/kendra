# Kendra's private iMac dashboard

The dashboard is a soft local visualization and control surface for Virtual Kendra. It does not replace Webots: `config/webots.yaml` remains the profile that connects Kendra's normal Body API to the articulated **3D Virtual Kendra body**.

## What the dashboard shows

- all nine core services plus the local voice service;
- text-LLM and semantic-VLM presence;
- reflex lock, cliff array, front range, battery model, body state, and selected body profile;
- recent user/Kendra turns and cognitive events;
- the latest iMac webcam frame and local semantic description;
- active Second Brain memories, provenance, goals, questions, and storage size;
- the local Git checkout and signed intelligence-release policy.

The desktop app is an Electron/Vite application, not a hosted website. There is no dashboard HTTP API, no `127.0.0.1:8766` bridge, and no `127.0.0.1:3000` server. The sandboxed renderer (`contextIsolation`, `sandbox`, no Node integration) can invoke only an explicit allowlist of named commands through Electron IPC; the main process forwards them to a Python `dashboard-bridge` child over stdin/stdout. File and body operations are validated by the Python services, never by the renderer.

## Start the full Virtual Kendra experience

1. Open `simulator/webots/worlds/kendra_virtual.wbt` in Webots.
2. Start the text model with `scripts/start_llm_intel_macos.sh`.
3. Start the semantic vision model with `scripts/start_vlm_intel_macos.sh`.
4. Start the services with `.venv/bin/python -m kendra --config config/webots.yaml dev start --voice`.
5. Launch the desktop app: double-click `Kendra.app`, or run `scripts/kendra_desktop_launcher.sh`.

After changing desktop or Python source, run `scripts/refresh_kendra_desktop.sh` to rebuild, verify, and relaunch.

macOS may ask for microphone and camera permission the first time Python opens each device. Grant access only to the local Terminal/Python process you are using for Kendra.

## Voice and webcam

The normal wake phrase remains **Kendra**. The **Talk with Kendra** dashboard control provides a deliberate push-to-talk turn using the same VAD, Moonshine ASR, local llama.cpp, and Piper path. Wake listening is paused during that capture to prevent competing microphone streams.

The webcam control uses the configured OpenCV camera index. Captured images stay under the ignored local `photos/` directory. The dashboard serves only validated image names from that directory. Semantic descriptions use only the configured loopback VLM.

## Retrieve the Second Brain

### Cable or removable drive

On Kendra's robot computer:

```bash
/opt/kendra/current/.venv/bin/kendra --config /etc/kendra/production.yaml brain export-jsonl
```

Copy the resulting `.jsonl` file to the iMac, open **Memory**, and choose **Import from her body**. The importer accepts Kendra Brain format version 1, caps the file at 25 MiB/50,000 rows, imports active memories only, re-embeds them locally, and skips duplicates. The separate biometric identity database is never part of this export/import path.

### Encrypted local Wi-Fi

1. Configure key-based SSH from the iMac to `kendra@kendra.local`.
2. Record the robot's host key in the iMac's normal `known_hosts`; strict checking is mandatory.
3. Keep the private key outside this repository with mode `0600`. Optionally set `brain.sync.ssh_identity_file` in ignored `config/local.yaml`.
4. Use **Retrieve memories now** in the dashboard.

The sync command is fixed to Kendra's production `brain export-jsonl --stdout` command. Hostnames and usernames are validated; passwords and arbitrary remote commands are not accepted.

## Improve and upgrade Kendra's intelligence

The development side is ordinary reviewed Git work on the iMac. Before allowing Kendra's body to install by voice:

1. Create a minisign key pair on the iMac. Keep the secret key outside Git and mode `0600`.
2. Replace the placeholder `config/minisign.pub` with the public key.
3. Commit and fully test the intelligence change.
4. Set `KENDRA_MINISIGN_SECRET_KEY` to the secret-key path and run `scripts/build_signed_intelligence_release.sh`.
5. Review and commit `releases/latest/`, then push `main`.
6. On the robot's ignored production overlay, set `updates.allow_voice_install: true`. Keep `updates.activate_after_stage: false` until the A/B slot and reboot recovery path are physically qualified.

Kendra can then respond to “check for an intelligence upgrade.” The install request requires the exact phrase **install signed intelligence upgrade**. She downloads only the pinned HTTPS paths, verifies the manifest signature and archive hash, extracts with traversal/link/device protections, builds and compile-checks the inactive slot, and refuses any failure. Activation is a separate gated policy so a voice misunderstanding cannot replace the running slot.
