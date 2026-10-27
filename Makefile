# oryx-overlay: Oryx for the visual layout, QMK for everything else.
# Run `make` for the list of targets.

SHELL := bash
.DEFAULT_GOAL := help
PYTHON ?= python3
DOCKER ?= docker
IMAGE ?= oryx-overlay-env
REF ?= HEAD

# Settings the environment may override, read from scripts/lib.sh so the two can't drift.
MAKEFILE_DIR := $(dir $(lastword $(MAKEFILE_LIST)))
CONFIG_KEYS := $(shell sed -n 's/^CONFIG_KEYS="\(.*\)"$$/\1/p' '$(MAKEFILE_DIR)scripts/lib.sh')
# Git identity for the container (docker-sync commits to the oryx branch). An identity
# already in the environment wins; otherwise take the host's git config.
GIT_AUTHOR_NAME ?= $(shell git config user.name 2>/dev/null)
GIT_AUTHOR_EMAIL ?= $(shell git config user.email 2>/dev/null)
GIT_COMMITTER_NAME ?= $(GIT_AUTHOR_NAME)
GIT_COMMITTER_EMAIL ?= $(GIT_AUTHOR_EMAIL)
GIT_KEYS := GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL

.PHONY: help sync build render diff lint doctor setup check test update-qmk self-update clean docker-image

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
	@source scripts/lib.sh && rm -rf "$$CACHE_DIR"/qmk_firmware-* "$$CACHE_DIR"/userspace-* \
	  && echo "Removed cached QMK trees in $$CACHE_DIR; the next build clones fresh."

self-update: ## Merge a newer template release into the tooling (TO=<tag-or-ref>, default newest)
	@TO="$(TO)" FROM="$(FROM)" scripts/self-update.sh

clean: ## Remove build output
	rm -rf build

## ---- docker (no local toolchain needed) --------------------------------------

docker-image: ## Build the toolchain image
	$(DOCKER) build -t $(IMAGE) -f docker/Dockerfile .

# Exported for this target only: `-e NAME` makes docker read the value from the environment,
# so names with spaces or apostrophes need no quoting. Only non-empty values are forwarded.
docker-%: export GIT_AUTHOR_NAME := $(GIT_AUTHOR_NAME)
docker-%: export GIT_AUTHOR_EMAIL := $(GIT_AUTHOR_EMAIL)
docker-%: export GIT_COMMITTER_NAME := $(GIT_COMMITTER_NAME)
docker-%: export GIT_COMMITTER_EMAIL := $(GIT_COMMITTER_EMAIL)
docker-%: docker-image ## Run any target in the container, e.g. make docker-build
	$(if $(CONFIG_KEYS),,$(error CONFIG_KEYS not found in scripts/lib.sh; docker-* cannot forward overrides))
	$(DOCKER) run --rm -t -v "$(CURDIR)":/work -w /work \
	  --user "$$(id -u):$$(id -g)" -e HOME=/tmp \
	  $(foreach k,$(CONFIG_KEYS) $(GIT_KEYS),$(if $($(k)),-e $(k))) \
	  $(IMAGE) make $*

## ---- tooling -------------------------------------------------------------------

check: test ## Lint the repo's own scripts and workflows (shellcheck, actionlint) and run tests
	shellcheck -x scripts/*.sh
	actionlint

test: ## Run the Python unit tests
	$(PYTHON) -m pytest -q tests
