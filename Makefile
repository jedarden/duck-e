.PHONY: test test-smoke test-all

# Unit + mock tests (no live credentials needed)
test:
	pytest tests/ --ignore=tests/security --ignore=tests/integration/test_rate_limiting_integration.py -q

# Live smoke tests against the VPN endpoint (requires VPN access).
# Tests every tool handler and session init without a browser or microphone.
smoke:
	DUCK_E_URL=https://duck-e-ducke-ardenone-cluster-ts.ardenone.com:8444 \
	DUCK_E_ORIGIN=https://ducke.ardenone.com \
		pytest tests/test_smoke.py -v --timeout=60

# Both
test-all: test smoke
