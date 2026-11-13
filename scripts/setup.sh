#!/bin/bash
# Interactive installer: checks, build, Full Disk Access for WAExport, policy and client registration.
# Safe to run again: every step skips what is already done.
# It never grants permissions itself: macOS only lets you do that in System Settings.
# Compatible with the bash 3.2 that ships with macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

INSTALLED=/Applications/WAExport.app
WHATSAPP_DATA="$HOME/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
BOLD=""; PLAIN=""
if [ -t 1 ]; then BOLD=$'\033[1m'; PLAIN=$'\033[0m'; fi

say()  { printf '\n%s%s%s\n' "$BOLD" "$*" "$PLAIN"; }
ok()   { printf '[ok] %s\n' "$*"; }
fail() { printf '\n[error] %s\n' "$*" >&2; exit 1; }
# Yes/no question; defaults to No, and to No when there is no terminal to answer.
ask()  {
  local answer=""
  [ -t 0 ] || return 1
  read -r -p "$1 [y/N] " answer || return 1
  case "$answer" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# Registration problems are reported by register.py and never stop the installer.
register() { uv run --locked --quiet python -I scripts/register.py "$1" || true; }

whatsapp_installed() {
  [ -d /Applications/WhatsApp.app ] && return 0
  [ -d /Applications/WhatsApp.localized/WhatsApp.app ] && return 0
  [ -n "$(mdfind "kMDItemCFBundleIdentifier == 'net.whatsapp.WhatsApp'" 2>/dev/null | head -n 1)" ]
}

say "1/6 Checking requirements"
[ "$(uname -s)" = "Darwin" ] || fail "This tool only works on macOS."
whatsapp_installed \
  || fail "WhatsApp for Mac is not installed. Install it from the App Store, sign in, let it sync, then run make setup again."
[ -d "$WHATSAPP_DATA" ] \
  || fail "WhatsApp for Mac has no local data yet. Open WhatsApp on this Mac once, let it sync, then run make setup again."
xcode-select -p >/dev/null 2>&1 \
  || fail "Xcode Command Line Tools are missing. Run: xcode-select --install   then run make setup again."
command -v swiftc >/dev/null 2>&1 \
  || fail "swiftc not found. Run: xcode-select --install   then run make setup again."
command -v uv >/dev/null 2>&1 \
  || fail "uv is missing. Install it with: brew install uv   (or see https://docs.astral.sh/uv/) then run make setup again."
ok "macOS, WhatsApp for Mac, Command Line Tools and uv"

say "2/6 Python dependencies"
uv sync --locked --quiet
ok ".venv is ready"

say "3/6 Building WAExport (the only program that reads WhatsApp's database)"
make -s build
if [ -x "$INSTALLED/Contents/MacOS/WAExport" ] \
   && cmp -s build/WAExport.app/Contents/MacOS/WAExport "$INSTALLED/Contents/MacOS/WAExport"; then
  ok "$INSTALLED is already up to date; left untouched (its Full Disk Access stays valid)"
else
  make -s install
  ok "Installed $INSTALLED"
fi

say "4/6 Policy (which chats the assistant may read)"
if [ -f config/policy.toml ]; then
  ok "config/policy.toml already exists; left untouched"
else
  (umask 077 && cp config/policy.example.toml config/policy.toml)
  ok "Created config/policy.toml from the example. It grants no message text until you edit it."
fi

say "5/6 Full Disk Access for WAExport"
if out=$(uv run --locked --quiet wa refresh 2>&1); then
  ok "WAExport can already read WhatsApp"
elif ! printf '%s' "$out" | grep -q "Full Disk Access"; then
  printf '%s\n' "$out"
  fail "wa refresh failed for a reason other than Full Disk Access (see above)."
else
  cat <<EOF
System Settings and a Finder window with WAExport selected will open now.
  1. Under "Full Disk Access", click + (or drag WAExport from the Finder window into the list).
  2. Choose $INSTALLED and switch it on.
  3. If WAExport was already in the list from an earlier install, remove it (-) and add it again (+).
Do NOT give this permission to Terminal, Claude, ChatGPT or any other program.
EOF
  open "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles" || true
  open -R "$INSTALLED" || true
  until uv run --locked --quiet wa refresh; do
    echo
    ask "Try again now that WAExport is switched on?" \
      || fail "Without that permission WAExport cannot read WhatsApp. Run make setup again whenever you are ready."
  done
  ok "WAExport read WhatsApp successfully"
fi

say "6/6 Connecting assistants"
if [ ! -t 0 ]; then
  echo "Client registration skipped (no terminal to answer). Run any of: make register-code," \
       "make register-desktop, make register-codex"
else
  if command -v claude >/dev/null 2>&1 && ask "Register with Claude Code?"; then
    register claude-code
  fi
  if [ -d /Applications/Claude.app ] && ask "Register with Claude Desktop (the Claude app)?"; then
    register claude-desktop
  fi
  if { [ -d /Applications/ChatGPT.app ] || [ -d /Applications/Codex.app ] || command -v codex >/dev/null 2>&1; } \
     && ask "Register with ChatGPT / Codex (~/.codex/config.toml)?"; then
    register codex
  fi
fi
echo "For any other MCP client: make show-mcp"

say "Done"
cat <<'EOF'
Next step: choose which chats the assistant may read.
  uv run wa chats --all --grep <text>   -> exact chat names
  edit config/policy.toml               -> [content] chats, or a [projects."..."] table
  uv run wa status                      -> check the result
You can also ask the assistant: "give access to the group X in project Y".
EOF
