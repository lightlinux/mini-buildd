import sys
import logging

DEBUG = []
FOREGROUND = False

HTTPD_ENDPOINTS = []

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

PACKAGE_TEMPLATES_DIR = "/usr/share/mini-buildd/package-templates"

#: This should never ever be changed
CHAR_ENCODING = "UTF-8"

# Compute python-version dependent install path
PY_PACKAGE_PATH = "/usr/lib/python{major}/dist-packages".format(major=sys.version_info[0])


def log_exception(log, message, exception, level=logging.ERROR):
    msg = "{m}: {e}".format(m=message, e=exception)
    log.log(level, msg)
    if "exception" in DEBUG:
        log.exception("Exception DEBUG ({m}):".format(m=msg))
