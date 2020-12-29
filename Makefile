# vi: noet
PREFIX = usr
SYSCONFDIR = etc/apkvitrine
WEBAPPDIR = $(PREFIX)/share/webapps/apkvitrine

PYTHON = python3
DESTDIR = target

-include config.mk

PYLINT = pylint
SETUP.PY = $(PYTHON) src/setup.py
# These are needed by setup.py
export SYSCONFDIR WEBAPPDIR

PYLINT_TARGETS = \
	apkvitrine

CLEAN_TARGETS = \
	MANIFEST \
	apkvitrine.egg-info \
	build \
	dist \
	target

.PHONY: all
all:
	$(SETUP.PY) build

.PHONY: paths
paths:
	@printf 'CONF: SYSCONFDIR = "%s"\n' '$(SYSCONFDIR)'
	@sed -i \
		-e '/^SYSCONFDIR = /s@= .*@= Path("/$(SYSCONFDIR)")@' \
		apkvitrine/__init__.py

.PHONY: install
install: paths all
	$(SETUP.PY) install \
		--root="$(DESTDIR)" \
		--prefix="/$(PREFIX)"
	mv "$(DESTDIR)/$(PREFIX)/bin/apkvitrine.cgi" \
		"$(DESTDIR)/$(WEBAPPDIR)"

.PHONY: clean
clean:
	rm -rf $(CLEAN_TARGETS)

#
# Maintainer targets:
#

.PHONY: dist
dist: clean
	$(SETUP.PY) sdist -u root -g root -t src/MANIFEST.in

.PHONY: pylint
pylint:
	-$(PYLINT) --rcfile src/pylintrc $(PYLINT_TARGETS)

.PHONY: lint
lint: pylint

.PHONY: setup
setup:
	@$(SETUP.PY) $(SETUP_ARGS)
