.PHONY: install ui test dist clean

install:            ## editable install with dev tools
	pip install -e ".[dev]"

ui:                 ## build the web UI into frameloom/static
	cd frontend && npm ci && npm run build

test:
	pytest -q

dist: ui            ## wheel + sdist in dist/
	python -m build

clean:
	rm -rf dist build *.egg-info frameloom/static
