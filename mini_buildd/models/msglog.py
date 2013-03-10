# -*- coding: utf-8 -*-
"""
Message log: Logs that should also go to the end user.

An instance of MsgLog may replace the standard (python) log;
logs will also go to the django messaging system, and it also
stores the logs so they might used for other (non-django) uses.

Logs done via MsgLog (aka "messages") are intended for the end
user, to be shown in an UI (for us, the django web app or the
command line client).

Log coding idioms to be used::

  # Optional: Alias for MsgLog class in modules where we need it
  from mini_buildd.models.msglog import MsgLog

  # Always: Global standard LOG object, directly after imports)
  LOG = logging.getLogger(__name__)

  # Standard log
  LOG.info("blah blah")

  # Message log
  MsgLog(LOG, request).info("Dear user: blah blah")
"""
from __future__ import unicode_literals

import logging
import inspect

import django.contrib.messages

import mini_buildd.setup

LOG = logging.getLogger(__name__)


class MsgLog(object):
    def __init__(self, pylog, request):
        self.pylog = pylog
        self.request = request
        self.plain = ""

    @classmethod
    def _level2python(cls, level):
        "Map default django log levels to python's."
        return {django.contrib.messages.DEBUG: logging.DEBUG,
                django.contrib.messages.INFO: logging.INFO,
                django.contrib.messages.SUCCESS: logging.INFO,
                django.contrib.messages.WARNING: logging.WARN,
                django.contrib.messages.ERROR: logging.ERROR}[level]

    @classmethod
    def _level2prefix(cls, level):
        "Map default django log levels to prefixes (for text-only output)."
        return {django.contrib.messages.DEBUG: "D",
                django.contrib.messages.INFO: "I",
                django.contrib.messages.SUCCESS: "I",
                django.contrib.messages.WARNING: "W",
                django.contrib.messages.ERROR: "E"}[level]

    def _msg(self, level, msg):
        if self.request:
            django.contrib.messages.add_message(self.request, level, msg)

        # Ouch: Try to get the actual log call's meta flags
        # Ideally, we should be patching the actual line and mod used for standard formatting (seems non-trivial...)
        actual_mod = "n/a"
        actual_line = "n/a"
        try:
            # The actual log call is two frames up
            frame = inspect.stack()[2]
            actual_mod = inspect.getmodulename(frame[1])
            actual_line = frame[2]
        except:
            pass

        self.pylog.log(self._level2python(level), "{m} [{mod}:{l}]".format(m=msg, mod=actual_mod, l=actual_line))
        self.plain += "{p}: {m}\n".format(p=self._level2prefix(level), m=msg)

    def debug(self, msg):
        self._msg(django.contrib.messages.DEBUG, msg)

    def info(self, msg):
        self._msg(django.contrib.messages.INFO, msg)

    def warn(self, msg):
        self._msg(django.contrib.messages.WARNING, msg)

    def error(self, msg):
        self._msg(django.contrib.messages.ERROR, msg)

    def exception(self, msg, exception, level=django.contrib.messages.ERROR):
        if self.request:
            django.contrib.messages.add_message(self.request, level, "{m}: {e}".format(m=msg, e=exception))
        mini_buildd.setup.log_exception(self.pylog, msg, exception, level=self._level2python(level))