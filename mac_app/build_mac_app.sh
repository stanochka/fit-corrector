#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="$ROOT_DIR/FIT Corrector.app"
CONTENTS="$APP_PATH/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RES_DIR="$CONTENTS/Resources"
EXEC_PATH="$MACOS_DIR/FITCorrectorLauncher"
PLIST_PATH="$CONTENTS/Info.plist"
APP_RES="$RES_DIR/app"
PY_LAUNCHER="$APP_RES/mac_app/launch_fit_corrector.py"

rm -rf "$APP_PATH"
mkdir -p "$MACOS_DIR" "$RES_DIR" "$APP_RES/mac_app"

cp "$ROOT_DIR/streamlit_app.py" "$APP_RES/streamlit_app.py"
cp "$ROOT_DIR/treadmill_fit_corrector.py" "$APP_RES/treadmill_fit_corrector.py"
cp "$ROOT_DIR/requirements.txt" "$APP_RES/requirements.txt"
cp "$ROOT_DIR/mac_app/launch_fit_corrector.py" "$APP_RES/mac_app/launch_fit_corrector.py"
chmod +x "$APP_RES/mac_app/launch_fit_corrector.py"

cat > "$EXEC_PATH" <<EOF
#!/bin/bash
exec /usr/bin/python3 "${PY_LAUNCHER}" >> "/tmp/fit_corrector_launcher.log" 2>&1
EOF
chmod +x "$EXEC_PATH"

cat > "$PLIST_PATH" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>FIT Corrector</string>
  <key>CFBundleDisplayName</key>
  <string>FIT Corrector</string>
  <key>CFBundleIdentifier</key>
  <string>com.stanochka.fitcorrector</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>FITCorrectorLauncher</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

echo "Built: $APP_PATH"
