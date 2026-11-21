.PHONY: build verify test render
build:
	python3 tools/archive.py build
verify:
	python3 tools/archive.py verify
test:
	python3 -m unittest discover -s tests -v
render:
	python3 tools/archive.py render all
