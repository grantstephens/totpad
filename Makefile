CIRCUITPY ?= /media/$(USER)/CIRCUITPY
BACKUP ?=
FILES = boot.py code.py totp.py usage.py README.md

.PHONY: help test install status check-secrets clean

help:
	@echo "Targets:"
	@echo "  test           Run the test suite"
	@echo "  install        Copy code to the MacroPad (BACKUP=x.2fas to install keys too)"
	@echo "  status         Show what is on the MacroPad"
	@echo "  check-secrets  Fail if a secrets file is staged for commit"
	@echo ""
	@echo "Variables: CIRCUITPY=$(CIRCUITPY) BACKUP=$(BACKUP)"

test:
	python3 -m unittest discover -s tests -v

install:
	@test -d "$(CIRCUITPY)" || { \
		echo "CIRCUITPY not mounted at $(CIRCUITPY)" >&2; \
		echo "Hold the top-left key while plugging in to expose the drive." >&2; \
		exit 1; }
	cp -v $(FILES) "$(CIRCUITPY)/"
	@if [ -n "$(BACKUP)" ]; then \
		case "$(BACKUP)" in \
			*.2fas) ;; \
			*) echo "BACKUP must be a *.2fas file" >&2; exit 1 ;; \
		esac; \
		cp -v "$(BACKUP)" "$(CIRCUITPY)/totp.2fas"; \
	fi
	@sync
	@echo "Installed. Replug without holding the key to hide the drive again."

status:
	@ls -la "$(CIRCUITPY)" 2>/dev/null || echo "CIRCUITPY not mounted"

check-secrets:
	@if git diff --cached --name-only \
		| grep -Ev '\.2fas\.example$$' \
		| grep -E '\.2fas$$|tokens\.json$$|^secrets\.py$$'; then \
		echo "Refusing: a secrets file is staged." >&2; \
		exit 1; \
	fi
	@echo "No secrets staged."

clean:
	rm -rf __pycache__ tests/__pycache__
