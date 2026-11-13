APP := build/WAExport.app
INSTALLED ?= /Applications/WAExport.app
REGISTER := uv run --locked --quiet python -I scripts/register.py
# rm -rf only ever touches a path that ends in /WAExport.app.
CHECK_INSTALLED := case "$(INSTALLED)" in */WAExport.app) ;; *) echo "INSTALLED must end in /WAExport.app" >&2; exit 1 ;; esac

.PHONY: help setup build install lint test uninstall register-code register-desktop register-codex show-mcp

help:
	@echo "whatsapp-context: read-only, policy-filtered WhatsApp for local AI assistants (macOS)"
	@echo ""
	@echo "  make setup             interactive install: checks, build, Full Disk Access, policy, clients"
	@echo "  make test              build the helper, lint and run the tests (synthetic data only)"
	@echo "  make build             compile and ad hoc sign build/WAExport.app"
	@echo "  make install           copy WAExport.app to $(INSTALLED) (re-grant Full Disk Access after)"
	@echo "  make register-code     register the MCP server in Claude Code"
	@echo "  make register-desktop  register in Claude Desktop (asks you to quit it with Cmd+Q)"
	@echo "  make register-codex    register in ChatGPT for Mac / Codex (~/.codex/config.toml)"
	@echo "  make show-mcp          print the server command for any other MCP client"
	@echo "  make uninstall         remove WAExport, the export and this repo's client entries"

setup:
	@INSTALLED="$(INSTALLED)" scripts/setup.sh

build: $(APP)/Contents/MacOS/WAExport

$(APP)/Contents/MacOS/WAExport: helper/main.swift helper/Info.plist
	mkdir -p $(APP)/Contents/MacOS
	cp helper/Info.plist $(APP)/Contents/Info.plist
	swiftc -O -o $@ helper/main.swift
	codesign --force --sign - --options runtime --identifier local.whatsapp-context.waexport $(APP)

# Ad hoc signature: Full Disk Access is tied to this exact build. After every install
# the old grant stops working silently: remove WAExport (-) and add it again (+).
install: build
	@$(CHECK_INSTALLED)
	rm -rf "$(INSTALLED)"
	ditto "$(APP)" "$(INSTALLED)"
	@echo "Installed $(INSTALLED). In System Settings > Privacy & Security > Full Disk Access: remove WAExport (-) if it was listed, then add it again (+)."

lint:
	uv run --locked ruff check .

test: build lint
	uv run --locked pytest -q

uninstall:
	@$(CHECK_INSTALLED)
	@$(REGISTER) remove
	rm -rf "$(INSTALLED)" "$(HOME)/.local/share/whatsapp-context"
	@echo "Now remove WAExport from System Settings > Privacy & Security > Full Disk Access."

register-code:
	@$(REGISTER) claude-code

register-desktop:
	@$(REGISTER) claude-desktop

# ChatGPT desktop app, Codex CLI and IDE extension share ~/.codex/config.toml.
register-codex:
	@$(REGISTER) codex

show-mcp:
	@$(REGISTER) show
