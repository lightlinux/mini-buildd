# -*- coding: utf-8 -*-

import sys
import logging

DEBUG = []
FOREGROUND = False

HTTPD_BIND = None

#: Global directory paths
HOME_DIR = None

INCOMING_DIR = None
REPOSITORIES_DIR = None

SPOOL_DIR = None
TMP_DIR = None
LOG_DIR = None
LOG_FILE = None
ACCESS_LOG_FILE = None
CHROOTS_DIR = None
CHROOTS_LIBDIR = None

MANUAL_DIR = None

#: This should never ever be changed
CHAR_ENCODING = "UTF-8"

# Compute python-version dependent install path
PY_PACKAGE_PATH = "/usr/lib/python{major}/dist-packages".format(major=sys.version_info[0])

# Static, and used by httpd*.py only. Should maybe moved back there when feasable.
DOC_MISSING_HTML_TEMPLATE = """\
<html><body>
<h1>{status} (<tt>mini-buildd-doc</tt> not installed?)</h1>
Maybe package <b><tt>mini-buildd-doc</tt></b> needs to be installed to make the manual available.
</body></html>
"""


def log_exception(log, message, exception, level=logging.ERROR):
    msg = "{m}: {e}".format(m=message, e=exception)
    log.log(level, msg)
    if "exception" in DEBUG:
        log.exception("Exception DEBUG ({m}):".format(m=msg))
