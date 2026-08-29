.PHONY: help build-all build/% build-ray build-improc push-all push/% push-ray push-improc test-local test-agent-local test-ray test-improc-local test-host test-canfar test-canfar-agents test-canfar-session test-canfar-ray test-canfar-ray-gpu test-canfar-ray-autoscale clean clean-all lock-ray lock-astroai-lab lock-check lint lint-doc-quota sync-marimo-starter sync-notebook-starters

SHELL := bash
OWNER ?= astroai
REGISTRY ?= images.canfar.net
TAG ?= $(shell date -u +%y.%m)
BUILD_TAG ?= local
PYTHON_VERSION ?= 3.13

export OWNER REGISTRY PYTHON_VERSION

SESSION_IMAGES := base webterm ghostty-web notebook vscode marimo openresearch
RAY_IMAGES := ray-manager ray-worker
IMAGE_PREFIX := $(REGISTRY)/$(OWNER)

help:
	@echo "AstroAI session images (CANFAR Harbor: images.canfar.net/astroai)"
	@echo "========================="
	@echo "  make build-all          build session images (base → sessions)"
	@echo "  make build-ray          build ray-manager + ray-worker (+ base/slim chain)"
	@echo "  make build-improc       build improc + improc-webterm + improc-notebook (+ base)"
	@echo "  make build/vscode       build one image (+ parents)"
	@echo "  make push-all           push session images to Harbor"
	@echo "  make push-ray           push Ray images to Harbor"
	@echo "  make push-improc        push improc stack to Harbor"
	@echo "  make test-local         verify session images locally"
	@echo "  make test-improc-local  verify improc family locally (improc/webterm/notebook)"
	@echo "  make test-agent-local   agent command matrix + no ~/.local pollution (all session images)"
	@echo "  make test-ray           Ray container + local cluster + UI tests"
	@echo "  make test-ray SMOKE=1   fast smoke: skip cluster formation"
	@echo "  make test-canfar        post-push headless verify on CANFAR"
	@echo "  make test-canfar-agents post-push agent verb-surface probe (lightweight, no installs)"
	@echo "  make test-canfar-session post-push contributed/notebook HTTP smoke"
	@echo "  make test-canfar-ray    CANFAR: manager UI + 2-worker cluster lifecycle"
	@echo "  make test-canfar-ray-gpu CANFAR: 1 GPU worker cluster (production)"
	@echo "  make test-canfar-ray-autoscale CANFAR: Ray autoscaler scale-up/down proof"
	@echo "  make clean              remove local $(IMAGE_PREFIX)/* images"
	@echo "  make clean-all          clean + prune buildx cache"
	@echo "  make lock-ray           regenerate config/ray-deps.lock"
	@echo "  make lock-astroai-lab   regenerate config/astroai-lab.lock"
	@echo "  make lock-check         fail if a lockfile drifts from its source"
	@echo "  make lint-doc-quota     forbid false 'headless consumes quota' advice in test-canfar.sh"
	@echo "  make test-host          docker-free: peek/osc52 selfchecks + agent-wizard tests"
	@echo "  make lint               lock-check + lint-doc-quota + test-host"
	@echo "  make sync-marimo-starter copy marimo starter.py from ../astroai-lab"
	@echo "  make sync-notebook-starters copy starter.ipynb + ray_train.ipynb from lab"
	@echo ""
	@echo "  OWNER=$(OWNER)  REGISTRY=$(REGISTRY)  BUILD_TAG=$(BUILD_TAG)  TAG=$(TAG)"

# Canonical starters live in canfar-lab / astroai-lab; containers copies are build artifacts.
ASTROAI_LAB_DIR ?= $(shell test -d ../canfar-lab && echo ../canfar-lab || echo ../astroai-lab)
ASTROAI_LAB_STARTER ?= $(ASTROAI_LAB_DIR)/src/astroai_lab/data/notebooks/starter.py
ASTROAI_LAB_IPYNB ?= $(ASTROAI_LAB_DIR)/src/astroai_lab/data/notebooks/starter.ipynb
ASTROAI_LAB_RAY_NB ?= $(ASTROAI_LAB_DIR)/src/astroai_lab/data/notebooks/ray_train.ipynb

sync-marimo-starter: ## copy marimo starter.py from sibling astroai-lab checkout
	@test -f "$(ASTROAI_LAB_STARTER)" || { echo "missing $(ASTROAI_LAB_STARTER)"; exit 1; }
	cp "$(ASTROAI_LAB_STARTER)" config/notebooks/starter.py
	@echo "updated config/notebooks/starter.py from $(ASTROAI_LAB_STARTER)"

sync-notebook-starters: sync-marimo-starter ## copy all lab notebooks into config/notebooks
	@test -f "$(ASTROAI_LAB_IPYNB)" || { echo "missing $(ASTROAI_LAB_IPYNB)"; exit 1; }
	@test -f "$(ASTROAI_LAB_RAY_NB)" || { echo "missing $(ASTROAI_LAB_RAY_NB)"; exit 1; }
	cp "$(ASTROAI_LAB_IPYNB)" config/notebooks/starter.ipynb
	cp "$(ASTROAI_LAB_RAY_NB)" config/notebooks/ray_train.ipynb
	@echo "updated config/notebooks/{starter.ipynb,ray_train.ipynb}"

build-all: ## build session images
	TAG=$(BUILD_TAG) docker buildx bake

build-ray: ## build Ray manager + worker (uses same base TAG)
	TAG=$(BUILD_TAG) docker buildx bake ray-manager ray-worker

build-improc: ## build improc + improc-webterm + improc-notebook (+ base)
	TAG=$(BUILD_TAG) docker buildx bake improc improc-webterm improc-notebook

build/%:
	TAG=$(BUILD_TAG) docker buildx bake $(notdir $@)

push-all: $(addprefix push/,$(SESSION_IMAGES))

push-ray: $(addprefix push/,$(RAY_IMAGES))

push-improc: push/improc push/improc-webterm push/improc-notebook ## push improc stack

# Production Ray push: bake TAG into manager env (RAY_IMAGE_TAG) — use BUILD_TAG=$(TAG).
#   make build-ray BUILD_TAG=26.08 TAG=26.08 && make push-ray TAG=26.08 BUILD_TAG=26.08

push/python:
	@echo "ERROR: python image is build-only (internal bake parent); never push to Harbor." >&2
	@exit 1

push/ray-base:
	@echo "ERROR: ray-base is build-only; push ray-manager and ray-worker." >&2
	@exit 1

# push/% sources from $(BUILD_TAG) — the same tag the image was BUILT with. A
# release build (e.g. BUILD_TAG=26.08) MUST be pushed with the same BUILD_TAG,
# otherwise the stale default :local image is silently re-tagged and pushed over
# the release tag. The guard fails loudly when the source image is missing, and
# refuses to push when the source is OLDER than the local :$(TAG) image (a
# downgrade — the exact stale-:local incident).
push/%:
	@src="$(IMAGE_PREFIX)/$(notdir $@):$(BUILD_TAG)"; \
	dst="$(IMAGE_PREFIX)/$(notdir $@):$(TAG)"; \
	if ! docker image inspect "$$src" >/dev/null 2>&1; then \
		echo "ERROR: $$src not found locally — build it first (make build/$(notdir $@) BUILD_TAG=$(BUILD_TAG))" >&2; \
		exit 1; \
	fi; \
	if docker image inspect "$$dst" >/dev/null 2>&1; then \
		src_ts="$$(docker image inspect --format '{{.Created}}' "$$src")"; \
		dst_ts="$$(docker image inspect --format '{{.Created}}' "$$dst")"; \
		if [[ "$$src_ts" < "$$dst_ts" ]]; then \
			echo "ERROR: $$src ($(BUILD_TAG)) is OLDER than local $$dst — push would downgrade $(notdir $@):$(TAG). Rebuild with BUILD_TAG=$(TAG) and push with the same." >&2; \
			exit 1; \
		fi; \
	fi; \
	echo "Pushing $$src ($(BUILD_TAG)) as $(notdir $@):$(TAG) and :latest"
	docker tag $(IMAGE_PREFIX)/$(notdir $@):$(BUILD_TAG) $(IMAGE_PREFIX)/$(notdir $@):$(TAG)
	docker push $(IMAGE_PREFIX)/$(notdir $@):$(TAG)
	docker tag $(IMAGE_PREFIX)/$(notdir $@):$(BUILD_TAG) $(IMAGE_PREFIX)/$(notdir $@):latest
	docker push $(IMAGE_PREFIX)/$(notdir $@):latest

lock-ray: ## regenerate config/ray-deps.lock from config/ray-deps.txt (Python 3.13, Ray).
	@tmp=$$(mktemp); \
	UV_NO_CACHE=1 uv pip compile --python-version 3.13 --output-file "$$tmp" config/ray-deps.txt >/dev/null; \
	mv -f "$$tmp" config/ray-deps.lock

lock-astroai-lab: ## regenerate config/astroai-lab.lock from unpinned main in config/astroai-lab.in.
	@tmp=$$(mktemp); \
	uv pip compile --python-version 3.13 --output-file "$$tmp" config/astroai-lab.in >/dev/null; \
	mv -f "$$tmp" config/astroai-lab.lock

lock-check: ## fail CI if a lockfile's package body drifts from its source. The uv-generated header (3 lines) is stripped before comparison so output paths in the embedded command line don't cause false-positive drift.
	@# Compile into fresh mktemp paths — uv treats an existing --output-file as a
	@# preference lock, so reusing /tmp/__ray.lock across runs hides real drift.
	@tmp_ray=$$(mktemp); tmp_lab=$$(mktemp); \
	UV_NO_CACHE=1 uv pip compile --python-version 3.13 --output-file "$$tmp_ray" config/ray-deps.txt >/dev/null; \
	tail -n +4 "$$tmp_ray" > /tmp/__ray.body; \
	tail -n +4 config/ray-deps.lock > /tmp/__ray.committed.body; \
	rm -f "$$tmp_ray"; \
	cmp -s /tmp/__ray.body /tmp/__ray.committed.body || { echo "ray-deps.lock drift — run make lock-ray" >&2; exit 1; }; \
	uv pip compile --python-version 3.13 --output-file "$$tmp_lab" config/astroai-lab.in >/dev/null; \
	tail -n +4 "$$tmp_lab" > /tmp/__lab.body; \
	tail -n +4 config/astroai-lab.lock > /tmp/__lab.committed.body; \
	rm -f "$$tmp_lab"; \
	cmp -s /tmp/__lab.body /tmp/__lab.committed.body || { echo "astroai-lab.lock drift — run make lock-astroai-lab" >&2; exit 1; }; \
	echo "lockfile package bodies match their source constraints"

test-local: ## verify session images (parallel)
	@fails=0; pids=(); \
	for img in webterm ghostty-web notebook vscode marimo openresearch base; do \
		./scripts/test-local.sh "$$img" --verify-only & pids+=($$!); \
	done; \
	for pid in "$${pids[@]}"; do wait "$$pid" || fails=$$((fails + 1)); done; \
	if [[ "$$fails" -gt 0 ]]; then echo "$$fails image(s) failed." >&2; exit 1; fi
	./scripts/test-work-overlay.sh
	./scripts/test-status-arc-project.sh

# Runs ALL session images — SESSION_IMAGES is the canonical list (pass it so
# adding/removing an image stays single-sourced). For a single-image dev run
# use the script directly after building it: make build/openresearch &&
# ./scripts/test-agent-local.sh openresearch (no build-all dependency).
test-agent-local: build-all ## agent command matrix on all session images (local, mounted fresh home)
	@chmod +x scripts/test-agent-local.sh
	@TAG=$(BUILD_TAG) ./scripts/test-agent-local.sh $(if $(IMAGE),$(IMAGE),$(SESSION_IMAGES))
	@echo "agent-local E2E passed for $(if $(IMAGE),$(IMAGE),all session images)"

test-ray: build-ray build/base ## Ray image checks + local cluster join + UI
	chmod +x scripts/test-ray-*.sh scripts/test-astroai-lab-loop.sh scripts/ray-head-start.sh \
		scripts/startup-ray-manager.sh scripts/ray-network-probe.sh ray/worker/start-worker.sh
	./scripts/test-ray-containers.sh $(if $(filter 1,$(SMOKE)),--smoke,)
	./scripts/test-ray-local.sh $(if $(filter 1,$(SMOKE)),--smoke,)
	./scripts/test-ray-cluster-local.sh $(if $(filter 1,$(SMOKE)),--smoke,)
	./scripts/test-ray-ui-local.sh $(if $(filter 1,$(SMOKE)),--smoke,)
	./scripts/test-astroai-lab-loop.sh $(if $(filter 1,$(SMOKE)),--smoke,)

test-improc-local: ## verify improc CLIs (build first: make build-improc)
	chmod +x scripts/test-improc-local.sh
	./scripts/test-improc-local.sh $(BUILD_TAG)
test-canfar:
	./scripts/test-canfar.sh $(or $(IMAGE),base) $(TAG)

test-canfar-agents: ## post-push agent verb-surface probe on CANFAR (lightweight, no installs)
	CANFAR_TEST_AGENTS=1 ./scripts/test-canfar.sh $(or $(IMAGE),base) $(TAG)
	@echo "CANFAR agent verb-surface verification passed for $(or $(IMAGE),base):$(TAG)"

test-canfar-session: ## contributed/notebook Running + connectURL HTTP smoke
	chmod +x scripts/test-canfar-session.sh
	./scripts/test-canfar-session.sh $(or $(IMAGE),webterm) $(TAG)

test-canfar-ray: ## CANFAR manager UI + 2-worker cluster lifecycle
	chmod +x scripts/test-canfar-ray.sh
	./scripts/test-canfar-ray.sh $(TAG)

test-canfar-ray-gpu: ## CANFAR 1-worker cluster with gpu=1
	chmod +x scripts/test-canfar-ray.sh
	CANFAR_RAY_GPUS=1 CANFAR_RAY_WORKER_COUNT=1 CANFAR_RAY_MIN_JOINED=1 \
		./scripts/test-canfar-ray.sh $(TAG)

test-canfar-ray-autoscale: ## CANFAR Ray autoscaler: bootstrap env + scale-up/down
	chmod +x scripts/test-canfar-ray.sh scripts/bootstrap-ray-manager-env.sh
	CANFAR_RAY_AUTOSCALING=1 ./scripts/test-canfar-ray.sh $(TAG)

clean:
	@imgs=($$(docker images --format '{{.Repository}}:{{.Tag}}' '$(IMAGE_PREFIX)/*' 2>/dev/null || true)); \
	if [[ $${#imgs[@]} -eq 0 || -z "$${imgs[0]}" ]]; then \
		echo "No $(IMAGE_PREFIX)/* images to remove."; \
	else \
		for img in "$${imgs[@]}"; do \
			docker rmi -f "$$img" 2>/dev/null || true; \
		done; \
		echo "Removed $(IMAGE_PREFIX)/* images."; \
	fi

clean-all: clean
	docker buildx prune -f

lint-doc-quota: ## forbid test-canfar.sh from reintroducing the false 'headless consumes quota' claim
	chmod +x scripts/lint-doc-quota.sh
	./scripts/lint-doc-quota.sh

test-host: ## docker-free checks (selfchecks + agent-wizard unit tests)
	chmod +x scripts/peek_selfcheck.sh scripts/osc52_copy_selfcheck.sh
	./scripts/peek_selfcheck.sh
	./scripts/osc52_copy_selfcheck.sh
	python3 scripts/lib/test_agent_wizard_verbs.py
	python3 scripts/lib/test_orx_canfar_proxy.py
	python3 scripts/lib/test_session_title.py
	python3 scripts/lib/test_canfar_marimo_env.py
	@! grep -F '[AstroAI]' config/starship.toml
	@! grep -E 'format = "in ' config/starship.toml
	@grep -q 'disabled = true' config/starship.toml

lint: ## lockfile drift + doc-quota guard + docker-free host tests
	$(MAKE) lock-check
	$(MAKE) lint-doc-quota
	$(MAKE) test-host

.PHONY: ray-launch
ray-launch:
	./scripts/ray-launch.sh
