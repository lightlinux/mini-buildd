# -*- coding: utf-8 -*-

import subprocess
import time
import tempfile
import threading
import os
import shutil
import logging

import mini_buildd.setup

LOG = logging.getLogger(__name__)


def taint_env(taint):
    env = os.environ.copy()
    for name in taint:
        env[name] = taint[name]
    return env


class Call(object):
    """Wrapper around python subprocess.

    When supplying ``stdout`` or ``stderr``, provide raw and
    'seekable' file-like object; i.e., use "w+" and standard
    python ``open`` like::

      mystdout = open(myoutputfile, "w+")

    >>> Call(["echo", "-n", "hallo"]).check().ustdout
    'hallo'
    >>> Call(["ls", "__no_such_file__"]).check()
    Traceback (most recent call last):
    ...
    Exception: Call failed with retval 2: 'ls __no_such_file__ '
    >>> Call(["printf stdin; printf stderr >&2"], stderr=subprocess.STDOUT, shell=True).ustdout
    'stdinstderr'
    """
    @classmethod
    def _call2shell(cls, call):
        """
        Convenience: Convert an argument sequence ("call") to a
        command line more human-readable and "likely-suitable"
        for cut and paste to a shell.
        """
        result = ""
        for arg in call:
            if " " in arg:
                result += "\"" + arg + "\""
            else:
                result += arg
            result += " "
        return result

    def __init__(self, call, run_as_root=False, **kwargs):
        self.call = ["sudo", "-n"] + call if run_as_root else call
        self.kwargs = kwargs.copy()

        # Generate stdout and stderr streams in kwargs, if not given explicitly
        for stream in ["stdout", "stderr"]:
            if stream not in self.kwargs:
                self.kwargs[stream] = tempfile.SpooledTemporaryFile()

        self.retval = subprocess.call(self.call, **self.kwargs)
        LOG.info("Called with retval {r}: {c}".format(r=self.retval, c=self._call2shell(self.call)))

        # Convenience 'label' for log output
        self.label = "{p} {c}..".format(p="#" if run_as_root else "?", c=call[0])

    @property
    def stdout(self):
        """Raw value as bytes."""
        self.kwargs["stdout"].seek(0)
        return self.kwargs["stdout"].read()

    @property
    def stderr(self):
        """Raw value as bytes."""
        if self.kwargs["stderr"] == subprocess.STDOUT:
            return b""
        else:
            self.kwargs["stderr"].seek(0)
            return self.kwargs["stderr"].read()

    @property
    def ustdout(self):
        """
        .. |docstr_uout| replace:: Value as unicode (decoding from :py:data:`mini_buildd.setup.CHAR_ENCODING`, replacing on error).

        |docstr_uout|
        """
        stdout = self.stdout
        return stdout if isinstance(stdout, str) else self.stdout.decode(mini_buildd.setup.CHAR_ENCODING, errors="replace")

    @property
    def ustderr(self):
        """|docstr_uout|"""
        stderr = self.stderr
        return stderr if isinstance(stderr, str) else self.stderr.decode(mini_buildd.setup.CHAR_ENCODING, errors="replace")

    def log(self):
        """Log calls output to mini-buildd's logging for debugging.

        On error, this logs with level ``warning``. On sucesss,
        this logs with level ``debug``.

        """
        olog = LOG.debug if self.retval == 0 else LOG.warning
        for prefix, output in [("stdout", self.ustdout), ("stderr", self.ustderr)]:
            for line in output.splitlines():
                olog("{label} ({p}): {l}".format(label=self.label, p=prefix, l=line.rstrip('\n')))

        return self

    def check(self):
        """Raise on unsuccessful (retval != 0) call."""
        if self.retval != 0:
            raise Exception("Call failed with retval {r}: '{c}'".format(r=self.retval, c=self._call2shell(self.call)))
        return self


def call_sequence(calls, run_as_root=False, rollback_only=False, **kwargs):
    """Run sequences of calls with rollback support.

    >>> call_sequence([(["echo", "-n", "cmd0"], ["echo", "-n", "rollback cmd0"])])
    >>> call_sequence([(["echo", "cmd0"], ["echo", "rollback cmd0"])], rollback_only=True)
    """

    def rollback(pos):
        for i in range(pos, -1, -1):
            if calls[i][1]:
                Call(calls[i][1], run_as_root=run_as_root, **kwargs).log()
            else:
                LOG.debug("Skipping empty rollback call sequent {i}".format(i=i))

    if rollback_only:
        rollback(len(calls) - 1)
    else:
        i = 0
        try:
            for l in calls:
                if l[0]:
                    Call(l[0], run_as_root=run_as_root, **kwargs).log().check()
                else:
                    LOG.debug("Skipping empty call sequent {i}".format(i=i))
                i += 1
        except BaseException:
            LOG.error("Sequence failed at: {i} (rolling back)".format(i=i))
            rollback(i)
            raise


def call_with_retry(call, retry_max_tries=5, retry_sleep=1, retry_failed_cleanup=None, **kwargs):
    for t in range(retry_max_tries):
        try:
            Call(call, **kwargs).log().check()
            break
        except BaseException as e:
            if t > retry_max_tries:
                raise
            LOG.error("Retrying call in {s} seconds [retry #{t}]: {e}".format(s=retry_sleep, t=t, e=e))
            if retry_failed_cleanup:
                retry_failed_cleanup()
            time.sleep(retry_sleep)


SBUILD_KEYS_WORKAROUND_LOCK = threading.Lock()


def sbuild_keys_workaround():
    "Create sbuild's internal key if needed (sbuild needs this one-time call, but does not handle it itself)."
    with SBUILD_KEYS_WORKAROUND_LOCK:
        if os.path.exists("/var/lib/sbuild/apt-keys/sbuild-key.pub"):
            LOG.debug("/var/lib/sbuild/apt-keys/sbuild-key.pub: Already exists, skipping")
        else:
            t = tempfile.mkdtemp()
            LOG.warning("One-time generation of sbuild keys (may take some time)...")
            Call(["sbuild-update", "--keygen"], env=taint_env({"HOME": t})).log().check()
            shutil.rmtree(t)
            LOG.info("One-time generation of sbuild keys done")
