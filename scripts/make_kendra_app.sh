#!/usr/bin/env bash
# Build a double-clickable Kendra.app launcher for this checkout.
#
# This is a launcher bundle, not a distributable application: it starts the
# Kendra desktop app from this repository so code changes take effect without
# repackaging. A real signed/notarized bundle is a separate, later step.
#
#   scripts/make_kendra_app.sh                       # install to ~/Applications
#   scripts/make_kendra_app.sh /Applications         # install elsewhere
#   scripts/make_kendra_app.sh ~/Applications webots # bake in the Webots profile
#
# PLATFORM: Intel iMac (x86_64 macOS) ONLY, by design.
# Kendra's robot body has no desktop, no Electron, and no display server. The
# Raspberry Pi 5 runs the same Python runtime under the systemd units in
# systemd/ -- see docs/INSTALL_PI.md. Nothing in this script is required for,
# or ever runs on, the robot. Kendra's brain, voice, vision, reflex, and body
# services are all platform-portable; only this desktop shell is not.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="${1:-$HOME/Applications}"
PROFILE="${2:-pc}"
CONFIG="config/${PROFILE}.yaml"
APP="$DESTINATION/Kendra.app"

[ "$(uname -s)" = "Darwin" ] || { echo "macOS only." >&2; exit 2; }
[ -f "$ROOT/$CONFIG" ] || { echo "Unknown Kendra profile: $CONFIG" >&2; exit 2; }

mkdir -p "$DESTINATION"
rm -rf "$APP"

# Build the bundle with osacompile rather than hand-rolling a shell-script
# bundle. A script-only bundle is registered by Launch Services but silently
# refused when it touches a TCC-protected location (this repository lives under
# ~/Desktop), because there is no real executable for macOS to attribute the
# permission prompt to. The AppleScript runner is a genuine Mach-O, so macOS
# prompts properly and the launch actually happens.
osacompile -o "$APP" -e "do shell script \"KENDRA_GUI=1 '$ROOT/scripts/kendra_desktop_launcher.sh' '$CONFIG' >/dev/null 2>&1 &\"" \
  || { echo "osacompile failed" >&2; exit 2; }

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Kendra</string>
  <key>CFBundleDisplayName</key><string>Kendra</string>
  <key>CFBundleIdentifier</key><string>now.nxthumans.kendra.launcher</string>
  <key>CFBundleExecutable</key><string>applet</string>
  <key>CFBundleIconFile</key><string>kendra</string>
  <key>WSApplicationName</key><string>Kendra</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundleVersion</key><string>0.2.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><false/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Kendra listens locally for the wake phrase and your spoken turns. Audio is transcribed on this machine and never sent to a cloud service.</string>
  <key>NSCameraUsageDescription</key>
  <string>Kendra uses the camera to see the room and recognize people you have consented to enroll. Frames are processed locally and never sent to a cloud service.</string>
</dict>
</plist>
PLIST

# Kendra's face as the app icon. Regenerate it when the reference image or the
# crop changes; otherwise reuse the committed .icns so this script does not
# need Pillow.
ICNS="$ROOT/dashboard/public/kendra.icns"
if [ ! -f "$ICNS" ] || [ "$ROOT/dashboard/public/kendra-reference.png" -nt "$ICNS" ]; then
  echo "Building Kendra's app icon..."
  "$ROOT/.venv/bin/python" "$ROOT/scripts/make_kendra_icon.py" \
    || echo "Could not regenerate the icon; using whatever is already there." >&2
fi
if [ -f "$ICNS" ]; then
  cp "$ICNS" "$APP/Contents/Resources/kendra.icns"
  rm -f "$APP/Contents/Resources/applet.icns"
  echo "Icon: Kendra's face ($(basename "$ICNS"))"
fi

# Ad-hoc sign so macOS has a stable identity to attach the Desktop/microphone/
# camera permissions to; without it every rebuild looks like a new app.
codesign -s - --force --deep "$APP" >/dev/null 2>&1 || true

# Place a copy on the Desktop so Kendra's face is visible without opening the
# Applications folder. A plain copy rather than a Finder alias: making an alias
# needs Finder automation permission, and a symlink loses the custom icon. The
# bundle is a few megabytes and both copies point at the same checkout, so
# re-running this script keeps them identical.
if [ "${KENDRA_DESKTOP_COPY:-1}" = "1" ] && [ -d "$HOME/Desktop" ]; then
  rm -rf "$HOME/Desktop/Kendra.app"
  if cp -R "$APP" "$HOME/Desktop/Kendra.app" 2>/dev/null; then
    codesign -s - --force --deep "$HOME/Desktop/Kendra.app" >/dev/null 2>&1 || true
    touch "$HOME/Desktop/Kendra.app"
    echo "Desktop copy: ~/Desktop/Kendra.app"
  else
    echo "Could not place a copy on the Desktop; ~/Applications/Kendra.app still works." >&2
  fi
fi

# Refresh Launch Services so Finder picks up the new bundle immediately.
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true
touch "$APP"

cat <<EOF

Kendra.app installed.

  Location: $APP
  Repository: $ROOT
  Profile: $CONFIG

Double-click it in Finder, or run:  open "$APP"
Keep it in the Dock by launching it once and choosing Options > Keep in Dock.

Notes:
  * The bundle points at this checkout. Re-run this script if you move the repo.
  * macOS attributes microphone and camera prompts to the Electron runtime that
    the launcher starts. Approve them once under System Settings > Privacy &
    Security; do not approve anything you did not just launch.
  * Startup logs: $ROOT/logs/desktop-launcher.log
EOF
