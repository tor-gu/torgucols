export PYTHONPATH = src

# The local venv when there is one (make venv); CI has none and uses python
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)

src_dirs := src tests

style:
	$(PYTHON) -m isort --profile black $(src_dirs)
	$(PYTHON) -m black --target-version py312 $(src_dirs)
	$(PYTHON) -m flake8 --ignore E501,E203,W503 $(src_dirs)

test:
	$(PYTHON) -m pytest -sv tests/

build:
	$(PYTHON) -m build

venv:
	python3.12 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements-dev.txt

.PHONY: style test build venv all

all: style test build
