# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import absolute_import

import subprocess
import tempfile
import threading
import os
import shutil
import logging

import mini_buildd.setup

LOG = logging.getLogger(__name__)


def create_and_open(file_name, *args, **kwargs):
    """Helper to open  a new file for writing and reading."""
    return os.fdopen(os.open(file_name, os.O_RDWR | os.O_CREAT), *args, **kwargs)


def taint_env(taint):
    env = os.environ.copy()
    for name in taint:
        env[name] = taint[name]
    return env


def log_call_output(log, prefix, output):
    output.seek(0)
    for line in output:
        log("{p}: {l}".format(p=prefix, l=line.decode(mini_buildd.setup.CHAR_ENCODING).rstrip('\n')))


def args2shell(args):
    """
    Convenience: Convert an argument sequence to a command line maybe-suitable for cut and paste to a shell.
    """
    result = ""
    for a in args:
        if " " in a:
            result += "\"" + a + "\""
        else:
            result += a
        result += " "
    return result


class Call(object):
    """
    Wrapper around subprocess.
    """
    @classmethod
    def _call2shell(cls, call):
        """
        Convenience: Convert an argument sequence ("call") to a command line maybe-suitable for cut and paste to a shell.
        """
        result = ""
        for a in call:
            if " " in a:
                result += "\"" + a + "\""
            else:
                result += a
            result += " "
        return result

    @classmethod
    def _log_call_output(cls, log, prefix, output):
        for line in output.splitlines():
            log("{p}: {l}".format(p=prefix, l=line.decode(mini_buildd.setup.CHAR_ENCODING).rstrip('\n')))

    def __init__(self, call, run_as_root=False, **kwargs):
        self.call = ["sudo", "-n"] + call if run_as_root else call
        self.kwargs = kwargs.copy()

        # Generate stdout and stderr streams in kwargs, if not given explicitly
        for stream in ["stdout", "stderr"]:
            if stream not in self.kwargs:
                self.kwargs[stream] = tempfile.TemporaryFile()

        self.retval = subprocess.call(self.call, **self.kwargs)
        LOG.info("Called with retval {r}: {c}".format(r=self.retval, c=self._call2shell(self.call)))

    @property
    def stdout(self):
        self.kwargs["stdout"].seek(0)
        return self.kwargs["stdout"].read().decode(mini_buildd.setup.CHAR_ENCODING)

    @property
    def stderr(self):
        if self.kwargs["stderr"] == subprocess.STDOUT:
            return ""
        else:
            self.kwargs["stderr"].seek(0)
            return self.kwargs["stderr"].read().decode(mini_buildd.setup.CHAR_ENCODING)

    def log(self, always=True):
        olog = LOG.info if self.retval == 0 else LOG.warning
        if self.retval != 0 or always:
            self._log_call_output(olog, "Call stdout", self.stdout)
            self._log_call_output(olog, "Call stderr", self.stderr)
        return self

    def check(self):
        if self.retval != 0:
            raise Exception("Call failed with retval {r}: '{c}'".format(r=self.retval, c=self._call2shell(self.call)))
        return self


def call(args, run_as_root=False, value_on_error=None, log_output=True, error_log_on_fail=True, **kwargs):
    """Wrapper around subprocess.call().

    >>> call(["echo", "-n", "hallo"])
    u'hallo'
    >>> call(["id", "-syntax-error"], value_on_error="Kapott")
    u'Kapott'
    """

    if run_as_root:
        args = ["sudo", "-n"] + args

    def _set_stream(name):
        if name in kwargs:
            return kwargs[name]
        else:
            stream = tempfile.TemporaryFile()
            kwargs[name] = stream
            return stream

    stdout = _set_stream("stdout")
    stderr = _set_stream("stderr")

    LOG.info("Calling: {a}".format(a=args2shell(args)))
    try:
        olog = LOG.debug
        try:
            subprocess.check_call(args, **kwargs)
        except:
            if error_log_on_fail:
                olog = LOG.error
            raise
        finally:
            try:
                if log_output:
                    log_call_output(olog, "Call stdout", stdout)
                    log_call_output(olog, "Call stderr", stderr)
            except Exception as e:
                mini_buildd.setup.log_exception(LOG, "Output logging failed (char enc?)", e)
    except:
        if error_log_on_fail:
            LOG.error("Call failed: {a}".format(a=args2shell(args)))
        if value_on_error is not None:
            return value_on_error
        else:
            raise
    LOG.debug("Call successful: {a}".format(a=args2shell(args)))
    stdout.seek(0)
    return stdout.read().decode(mini_buildd.setup.CHAR_ENCODING)


def sose(*args, **kwargs):
    """
    >>> sose(["echo", "-n", "hallo"])
    u'hallo'
    >>> sose(["ls", "__no_such_file__"])
    Traceback (most recent call last):
    ...
    CalledProcessError: Command '[u'ls', u'__no_such_file__']' returned non-zero exit status 2

    >>> sose(["printf stdin; printf stderr >&2"], shell=True)
    u'stdinstderr'
    """
    return call(*args, stderr=subprocess.STDOUT, **kwargs)


def call_sequence(calls, run_as_root=False, value_on_error=None, log_output=True, rollback_only=False, **kwargs):
    """Run sequences of calls with rollback support.

    >>> call_sequence([(["echo", "-n", "cmd0"], ["echo", "-n", "rollback cmd0"])])
    >>> call_sequence([(["echo", "cmd0"], ["echo", "rollback cmd0"])], rollback_only=True)
    """

    def rollback(pos):
        for i in range(pos, -1, -1):
            if calls[i][1]:
                call(calls[i][1], run_as_root=run_as_root, value_on_error="", log_output=log_output, **kwargs)
            else:
                LOG.debug("Skipping empty rollback call sequent {i}".format(i=i))

    if rollback_only:
        rollback(len(calls) - 1)
    else:
        i = 0
        try:
            for l in calls:
                if l[0]:
                    call(l[0], run_as_root=run_as_root, value_on_error=value_on_error, log_output=log_output, **kwargs)
                else:
                    LOG.debug("Skipping empty call sequent {i}".format(i=i))
                i += 1
        except:
            LOG.error("Sequence failed at: {i} (rolling back)".format(i=i))
            rollback(i)
            raise


SBUILD_KEYS_WORKAROUND_LOCK = threading.Lock()


def sbuild_keys_workaround():
    "Create sbuild's internal key if needed (sbuild needs this one-time call, but does not handle it itself)."
    with SBUILD_KEYS_WORKAROUND_LOCK:
        if os.path.exists("/var/lib/sbuild/apt-keys/sbuild-key.pub"):
            LOG.debug("/var/lib/sbuild/apt-keys/sbuild-key.pub: Already exists, skipping")
        else:
            t = tempfile.mkdtemp()
            LOG.warn("One-time generation of sbuild keys (may take some time)...")
            call(["sbuild-update", "--keygen"], env=taint_env({"HOME": t}))
            shutil.rmtree(t)
            LOG.info("One-time generation of sbuild keys done")
