#!/bin/bash
# The one step macOS keeps for the user: giving WAExport Full Disk Access. No app can request or grant
# that permission, so this opens the right places, copies the path and waits until WAExport can read.
# Compatible with the bash 3.2 that ships with macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

INSTALLED="${INSTALLED:-/Applications/WAExport.app}"
WAIT="${GRANT_WAIT_SECONDS:-600}"
EVERY="${GRANT_POLL_SECONDS:-5}"
out=""

refresh() { out=$(uv run --locked --quiet wa refresh 2>&1); }
# The canary WARNING mentions Full Disk Access too: the reason is the first line that is not a WARNING.
denied() { case "$(printf '%s\n' "$out" | grep -v '^WARNING' | head -n 1 || true)" in *"Full Disk Access"*) ;; *) return 1 ;; esac; }
other_failure() {
  printf '%s\n' "$out" >&2
  printf '[error] wa refresh failed for a reason other than Full Disk Access (see above).\n' >&2
  exit 1
}
warnings() { printf '%s\n' "$out" | grep '^WARNING' || true; }
done_ok() { warnings; printf '[ok] WAExport can read WhatsApp\n'; exit 0; }

[ -d "$INSTALLED" ] || { printf '[error] %s not found: run make setup first.\n' "$INSTALLED" >&2; exit 1; }
deadline=$((SECONDS + WAIT))  # wall clock, checks included
if refresh; then done_ok; fi
denied || other_failure
warnings  # e.g. this terminal has Full Disk Access itself: say so before anything else

printf '%s' "$INSTALLED" | pbcopy 2>/dev/null || true
open "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles" || true
open -R "$INSTALLED" || true
cat <<EOF
macOS lets only you give this permission. System Settings and a Finder window with WAExport are open:
  1. Drag WAExport from the Finder window into the "Full Disk Access" list and switch it on.
     (Or click +, press Cmd+Shift+G, paste with Cmd+V and click Open: the path is on your clipboard.)
  2. If WAExport was already in the list, remove it (-) first, then add it again.
Only WAExport needs it: never Terminal, Claude, ChatGPT or any other app.
Waiting until it works (up to $((WAIT / 60)) min); nothing to press here...
EOF
until refresh; do
  denied || other_failure
  if [ "$SECONDS" -ge "$deadline" ]; then
    warnings >&2
    printf '[error] WAExport still has no Full Disk Access. Run make grant again when you are ready.\n' >&2
    exit 1
  fi
  sleep "$EVERY"
done
done_ok
