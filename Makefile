.PHONY: bootstrap check lock qualify ui ui-check
bootstrap:
	./scripts/bootstrap.sh
check:
	./scripts/test.sh
qualify:
	npm ci --prefix tools/qualification --ignore-scripts
	uv run python scripts/qualify-protocol.py
# After editing dependencies in pyproject.toml: refresh uv.lock and the image's hash lock.
lock:
	uv lock
	uv export --frozen --no-dev --no-emit-project --no-header --quiet -o requirements-runtime.lock

# Python runtime ships the prebuilt App; Node is needed only to develop/verify it.
ui:
	npm ci --prefix tools/ui --ignore-scripts
	npm run build --prefix tools/ui
ui-check:
	npm ci --prefix tools/ui --ignore-scripts
	node tools/ui/build.mjs --check
	npm test --prefix tools/ui
