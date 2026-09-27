PYTHON ?= python3
PREFIX ?= /usr/local
DATAROOTDIR ?= $(PREFIX)/share
MANDIR ?= $(DATAROOTDIR)/man
DESTDIR ?=
INSTALL ?= install

.PHONY: man check-man install-man test

man:
	$(PYTHON) tools/build_manpage.py

check-man:
	$(PYTHON) tools/build_manpage.py --check

install-man: check-man
	$(INSTALL) -d "$(DESTDIR)$(MANDIR)/man1"
	$(INSTALL) -m 644 man/driftstamp.1 "$(DESTDIR)$(MANDIR)/man1/driftstamp.1"

test: check-man
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m unittest discover -s examples/redhat_plugin/tests -v
