import subprocess
import time
import os
import logging

import mini_buildd.setup

LOG = logging.getLogger(__name__)


def taint_env(taint):
    env = os.environ.copy()
    for name in taint:
        env[name] = taint[name]
    return env


class Call():
    """Wrapper around python subprocess.

    When supplying ``stdout`` or ``stderr``, provide raw and
    'seekable' file-like object; i.e., use "w+" and standard
    python ``open`` like::

      mystdout = open(myoutputfile, "w+")

    >>> Call(["echo", "-n", "hallo"]).check().stdout
    'hallo'
    >>> Call(["ls", "__no_such_file__"]).check()
    Traceback (most recent call last):
    ...
    Exception: Call failed with returncode 2: 'ls __no_such_file__ '
    >>> Call(["printf stdin; printf stderr >&2"], stderr=subprocess.STDOUT, shell=True).stdout
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
            result += "{arg} ".format(arg="\"" + arg + "\"" if " " in arg else arg)
        return result

    def __init__(self, call, run_as_root=False, **kwargs):
        self.call = ["sudo", "-n"] + call if run_as_root else call
        self.kwargs = kwargs.copy()

        # Generate stdout and stderr args if not given explicitly, and remember streams given explicitly
        self._given_stream = {}
        for stream in ["stdout", "stderr"]:
            if stream not in self.kwargs:
                self.kwargs[stream] = subprocess.PIPE
            elif not isinstance(self.kwargs[stream], int):
                self._given_stream[stream] = self.kwargs[stream]

        self.result = subprocess.run(self.call, **self.kwargs)

        # Convenience 'label' for log output
        self.label = "{p} {c}..".format(p="#" if run_as_root else "?", c=call[0])

        LOG.info("Called with returncode {r}: {c}".format(r=self.result.returncode, c=self._call2shell(self.call)))

    @classmethod
    def _bos2str(cls, value, errors="replace"):
        """return str(), regardless if value is of type bytes or str."""
        return value if isinstance(value, str) else value.decode(encoding=mini_buildd.setup.CHAR_ENCODING, errors=errors)

    def _stdx(self, value, key):
        """stdin or stdout value as str."""
        if value:
            return self._bos2str(value)
        if key in self._given_stream:
            self._given_stream[key].seek(0)
            return self._bos2str(self._given_stream[key].read())
        return ""

    @property
    def stdout(self):
        """stdout value (empty string if none)"""
        return self._stdx(self.result.stdout, "stdout")

    @property
    def stderr(self):
        """stderr value (empty string if none)"""
        return self._stdx(self.result.stderr, "stderr")

    def log(self):
        """Log calls output to mini-buildd's logging for debugging.

        On error, this logs with level ``warning``. On sucesss,
        this logs with level ``debug``.

        """
        olog = LOG.debug if self.result.returncode == 0 else LOG.warning
        for prefix, output in [("stdout", self.stdout), ("stderr", self.stderr)]:
            for line in output.splitlines():
                olog("{label} ({prefix}): {line}".format(label=self.label, prefix=prefix, line=line.rstrip('\n')))
        return self

    def check(self):
        """Raise on unsuccessful (returncode != 0) call."""
        if self.result.returncode != 0:
            raise Exception("Call failed with returncode {r}: '{c}'".format(r=self.result.returncode, c=self._call2shell(self.call)))
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
    """Run call repeatedly until it succeeds (retval 0). In case
    retry_max_tries is reached, the error from the last try is raised.

    >>> call_with_retry(["/bin/true"])
    >>> call_with_retry(["/bin/false"])
    Traceback (most recent call last):
      ...
    Exception: Call failed with returncode 1: '/bin/false '
    """
    for t in range(retry_max_tries):
        try:
            Call(call, **kwargs).log().check()
            return
        except BaseException as e:
            if t >= retry_max_tries - 1:
                raise
            LOG.error("Retrying call in {s} seconds [retry #{t}]: {e}".format(s=retry_sleep, t=t, e=e))
            if retry_failed_cleanup:
                retry_failed_cleanup()
            time.sleep(retry_sleep)
