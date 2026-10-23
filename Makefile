UV := .tools/bin/uv
LK := .tools/bin/lk
GIT := $(shell command -v git)
PROJECT ?= coding-buddy
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export UV_PROJECT_ENVIRONMENT := $(CURDIR)/.venv

.PHONY: setup check demo acceptance doctor status usage dev console job-worker jobs-probe github-setup vms-preflight call-report
setup:
	bash tools/bootstrap.sh
	$(UV) sync --locked
	$(UV) run python tools/init_demo_repo.py
	$(UV) run python tools/doctor.py --offline
check:
	$(UV) run ruff format --check src tools tests
	$(UV) run ruff check src tools tests
	$(UV) run pytest -q
	$(GIT) diff --check
demo:
	$(UV) run python tools/init_demo_repo.py
	$(UV) run python tools/offline_demo.py
acceptance:
	$(UV) run python tools/acceptance_demo.py --json
doctor:
	$(UV) run python tools/doctor.py
status:
	$(UV) run python tools/status.py
usage:
	$(UV) run python tools/report_usage.py
dev:
	$(LK) --project $(PROJECT) agent dev
console:
	$(LK) --project $(PROJECT) agent console
job-worker:
	$(UV) run python -u tools/jobs.py work --max-jobs 1
jobs-probe:
	$(UV) run python tools/jobs.py probe
github-setup:
	bash tools/bootstrap_github.sh
vms-preflight:
	$(UV) run python tools/vms_preflight.py --probe-home
call-report:
	$(UV) run python tools/report_call.py
