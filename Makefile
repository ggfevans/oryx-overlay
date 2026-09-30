# oryx-overlay: Oryx for the visual layout, QMK for everything else.
# Run `make` for the list of targets.

SHELL := bash
.DEFAULT_GOAL := help
PYTHON ?= python3
DOCKER ?= docker
IMAGE ?= oryx-overlay-env
REF ?= HEAD

.PHONY: help sync build render diff lint doctor setup check test update-qmk clean docker-image

help: ## Show this help
	@printf 'Usage: make <target>\n\n'
	@grep -hE '^[a-zA-Z0-9_%-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[1m%-14s\033[0m %s\n",$$1,$$2}'

## ---- everyday -------------------------------------------------------------

sync: ## Pull the latest layout from Oryx and merge it into this branch
	@scripts/sync.sh

build: ## Compile firmware into build/ (also refreshes docs/)
	@scripts/build.sh

render: ## Redraw docs/keymap.svg and docs/keymap.md from layout/
	@scripts/render.sh

diff: ## Key-by-key changes since REF (default HEAD), e.g. make diff REF=HEAD~3
	@scripts/layout.sh diff $(REF)

lint: ## Check the layout for unreachable layers and dangling layer keys
	@scripts/layout.sh lint

## ---- setup -----------------------------------------------------------------

doctor: ## Check which tools are installed
	@scripts/doctor.sh

setup: ## Create .venv with the qmk CLI and keymap-drawer (needs Python 3.10+)
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { \
	  echo "make setup needs Python 3.10+ (keymap-drawer requires it); $(PYTHON) is $$($(PYTHON) -V 2>&1)."; \
	  echo "Install a newer Python (e.g. brew install python) and run: make setup PYTHON=python3.12"; exit 1; }
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --quiet --upgrade pip setuptools wheel
	.venv/bin/pip install --quiet -r requirements.txt
	@echo "Python tools ready. You still need the ARM toolchain: see README > Building locally."

update-qmk: ## Re-download ZSA's QMK fork (e.g. after ZSA patches a firmware branch)
	@rm -rf .cache/qmk_firmware-*
	@echo "Removed cached QMK trees; the next build clones fresh."

clean: ## Remove build output
	rm -rf build

## ---- docker (no local toolchain needed) --------------------------------------

docker-image: ## Build the toolchain image
	$(DOCKER) build -t $(IMAGE) -f docker/Dockerfile .

docker-%: docker-image ## Run any target in the container, e.g. make docker-build
	$(DOCKER) run --rm -t -v "$(CURDIR)":/work -w /work \
	  --user "$$(id -u):$$(id -g)" -e HOME=/tmp \
	  $(IMAGE) make $*

## ---- tooling -------------------------------------------------------------------

check: test ## Lint the repo's own scripts and workflows (shellcheck, actionlint) and run tests
	shellcheck -x scripts/*.sh
	actionlint

test: ## Run the Python unit tests
	$(PYTHON) -m pytest -q tests
