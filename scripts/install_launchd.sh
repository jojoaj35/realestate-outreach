#!/bin/bash
# Install launchd agents for this project (replies poller + outreach batches).
# Generates plists with absolute paths for THIS machine and loads them.
#
#   bash scripts/install_launchd.sh           # install + load
#   bash scripts/install_launchd.sh uninstall  # unload + remove
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
REPLIES_LABEL="com.sarealestate.replies"
OUTREACH_LABEL="com.sarealestate.outreach"
REPLIES_PLIST="$AGENTS_DIR/$REPLIES_LABEL.plist"
OUTREACH_PLIST="$AGENTS_DIR/$OUTREACH_LABEL.plist"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl unload "$REPLIES_PLIST" 2>/dev/null || true
  launchctl unload "$OUTREACH_PLIST" 2>/dev/null || true
  rm -f "$REPLIES_PLIST" "$OUTREACH_PLIST"
  echo "Uninstalled launchd agents."
  exit 0
fi

mkdir -p "$AGENTS_DIR" "$PROJECT_DIR/logs"

# Replies poller — every 15 minutes.
cat > "$REPLIES_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$REPLIES_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/scripts/run_replies.sh</string>
  </array>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/replies.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/replies.err.log</string>
</dict>
</plist>
PLIST

# Outreach batches — at 10am, 12pm, 2pm, 4pm local.
cat > "$OUTREACH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$OUTREACH_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/scripts/run_outreach.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/outreach.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/outreach.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$REPLIES_PLIST" 2>/dev/null || true
launchctl unload "$OUTREACH_PLIST" 2>/dev/null || true
launchctl load "$REPLIES_PLIST"
launchctl load "$OUTREACH_PLIST"

echo "Installed and loaded:"
echo "  $REPLIES_LABEL   (every 15 min)"
echo "  $OUTREACH_LABEL  (10/12/14/16 daily, real sends, capped by DAILY_SEND_CAP)"
echo
echo "NOTE: grant Full Disk Access to /bin/bash (or your shell) so the replies"
echo "poller can read chat.db. Disable sends anytime: bash scripts/install_launchd.sh uninstall"
