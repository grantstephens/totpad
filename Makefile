CIRCUITPY ?= /media/$(USER)/CIRCUITPY
BOOTLOADER ?= /media/$(USER)/RPI-RP2
BACKUP ?=

# CircuitPython and library bundle versions. The .mpy bytecode format is tied
# to the major version, so mpy-cross, /lib and the firmware move together.
CP_VERSION ?= 10.3.0
BUNDLE_DATE ?= 20260905
BOARD ?= adafruit_macropad_rp2040

TOOLS = .tools
MPY_CROSS = $(TOOLS)/mpy-cross-$(CP_VERSION)
UF2 = $(TOOLS)/$(BOARD)-$(CP_VERSION).uf2
BUNDLE = $(TOOLS)/bundle-$(BUNDLE_DATE).zip
BUNDLE_URL = https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/$(BUNDLE_DATE)/adafruit-circuitpython-bundle-10.x-mpy-$(BUNDLE_DATE).zip

# Modules compiled to bytecode. code.py and boot.py must stay as source,
# because CircuitPython only looks for those two by name.
MODULES = totp.py usage.py
SOURCES = boot.py code.py README.md
BUILD = build

.PHONY: help test install install-src libs firmware flash tools mpy status check-secrets clean distclean

help:
	@echo "Targets:"
	@echo "  test           Run the test suite"
	@echo "  mpy            Compile $(MODULES) to .mpy"
	@echo "  install        Copy code to the MacroPad as .mpy (BACKUP=x.2fas for keys)"
	@echo "  install-src    Copy code as plain .py instead, for debugging"
	@echo "  libs           Install the required libraries from the bundle"
	@echo "  firmware       Download CircuitPython $(CP_VERSION) for $(BOARD)"
	@echo "  flash          Copy the firmware to a MacroPad in bootloader mode"
	@echo "  status         Show what is on the MacroPad"
	@echo "  check-secrets  Fail if a secrets file is staged for commit"
	@echo ""
	@echo "CircuitPython $(CP_VERSION), bundle $(BUNDLE_DATE)"

test:
	python3 -m unittest discover -s tests -v

tools: $(MPY_CROSS)

$(MPY_CROSS):
	@mkdir -p $(TOOLS)
	curl -sfL -o $@ "https://adafruit-circuit-python.s3.amazonaws.com/bin/mpy-cross/linux-amd64/mpy-cross-linux-amd64-$(CP_VERSION).static"
	chmod +x $@
	@$@ --version

mpy: $(MPY_CROSS)
	@mkdir -p $(BUILD)
	@for src in $(MODULES); do \
		echo "mpy-cross $$src"; \
		$(MPY_CROSS) -o $(BUILD)/$${src%.py}.mpy $$src || exit 1; \
	done
	@ls -l $(BUILD)

define require_circuitpy
	@test -d "$(CIRCUITPY)" || { \
		echo "CIRCUITPY not mounted at $(CIRCUITPY)" >&2; \
		echo "Hold the top-left key while plugging in to expose the drive." >&2; \
		exit 1; }
endef

define copy_backup
	@if [ -n "$(BACKUP)" ]; then \
		case "$(BACKUP)" in \
			*.2fas) ;; \
			*) echo "BACKUP must be a *.2fas file" >&2; exit 1 ;; \
		esac; \
		cp -v "$(BACKUP)" "$(CIRCUITPY)/totp.2fas"; \
	fi
	@sync
endef

install: mpy
	$(require_circuitpy)
	cp -v $(SOURCES) "$(CIRCUITPY)/"
	cp -v $(BUILD)/*.mpy "$(CIRCUITPY)/"
	@# a leftover .py would shadow the compiled module
	@for src in $(MODULES); do rm -fv "$(CIRCUITPY)/$$src"; done
	$(copy_backup)
	@echo "Installed (.mpy). Replug without holding the key to hide the drive."

install-src:
	$(require_circuitpy)
	cp -v $(SOURCES) $(MODULES) "$(CIRCUITPY)/"
	@for src in $(MODULES); do rm -fv "$(CIRCUITPY)/$${src%.py}.mpy"; done
	$(copy_backup)
	@echo "Installed (source). Replug without holding the key to hide the drive."

$(BUNDLE):
	@mkdir -p $(TOOLS)
	curl -sfL -o $@ "$(BUNDLE_URL)"

libs: $(BUNDLE)
	$(require_circuitpy)
	@rm -rf $(BUILD)/lib && mkdir -p $(BUILD)/lib
	@unzip -q -j -o $(BUNDLE) "*/lib/neopixel.mpy" "*/lib/adafruit_pixelbuf.mpy" \
		"*/lib/adafruit_ds3231.mpy" -d $(BUILD)/lib
	@for pkg in adafruit_bitmap_font adafruit_display_text adafruit_progressbar \
	            adafruit_hid adafruit_bus_device adafruit_register adafruit_hashlib; do \
		mkdir -p $(BUILD)/lib/$$pkg; \
		unzip -q -j -o $(BUNDLE) "*/lib/$$pkg/*" -d $(BUILD)/lib/$$pkg; \
	done
	rm -rf "$(CIRCUITPY)/lib"
	cp -r $(BUILD)/lib "$(CIRCUITPY)/lib"
	@sync
	@du -sh "$(CIRCUITPY)/lib"
	@echo "Libraries installed for CircuitPython $(CP_VERSION)."

firmware: $(UF2)

$(UF2):
	@mkdir -p $(TOOLS)
	curl -sfL -o $@ "https://downloads.circuitpython.org/bin/$(BOARD)/en_US/adafruit-circuitpython-$(BOARD)-en_US-$(CP_VERSION).uf2"
	@ls -lh $@

flash: $(UF2)
	@test -d "$(BOOTLOADER)" || { \
		echo "MacroPad is not in bootloader mode." >&2; \
		echo "Double-tap RESET, wait for $(BOOTLOADER) to mount, then rerun." >&2; \
		exit 1; }
	cp -v $(UF2) "$(BOOTLOADER)/"
	@sync
	@echo "Flashed CircuitPython $(CP_VERSION). The board reboots on its own."
	@echo "Run 'make libs install' once CIRCUITPY comes back."

status:
	@ls -la "$(CIRCUITPY)" 2>/dev/null || echo "CIRCUITPY not mounted"
	@cat "$(CIRCUITPY)/boot_out.txt" 2>/dev/null || true

check-secrets:
	@if git diff --cached --name-only \
		| grep -Ev '\.2fas\.example$$' \
		| grep -E '\.2fas$$|tokens\.json$$|^secrets\.py$$'; then \
		echo "Refusing: a secrets file is staged." >&2; \
		exit 1; \
	fi
	@echo "No secrets staged."

clean:
	rm -rf $(BUILD) __pycache__ tests/__pycache__

distclean: clean
	rm -rf $(TOOLS)
