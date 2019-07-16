"""Run reprepro commands."""

import os
import shutil
import threading

import logging

import mini_buildd.call

LOG = logging.getLogger(__name__)

_LOCKS = {}


class Reprepro():
    """
    Abstraction to reprepro repository commands.

    *Locking*

    This implicitly provides a locking mechanism to avoid
    parallel calls to the same repository from mini-buildd
    itself. This rules out any failed call due to reprepro
    locking errors in the first place.

    For the case that someone else is using reprepro
    manually, we also always run it with '--waitforlock'.

    *Ignoring 'unusedarch' check*

    Known broken use case is linux' 'make deb-pkg' up to version 4.13.

    linux' native 'make deb-pkg' is the recommended and documented way to
    produce custom kernels on Debian systems.

    Up to linux version 4.13 (see [#l1]_, [#l2]_), this would also produce
    firmware packages, flagged "arch=all" in the control file, but
    actually producing "arch=any" firmware *.deb. The changes file
    produced however would still list "all" in the Architecture field,
    making the reprepro "unsusedarch" check fail (and thusly, installation
    on mini-buildd will fail).

    While this is definitely a bug in 'make deb-pkg' (and also not an
    issue 4.14 onwards or when you use it w/o producing a firmware
    package), the check is documented as "safe to ignore" in reprepro, so
    I think we should allow these cases to work.

    .. [l1] https://github.com/torvalds/linux/commit/cc18abbe449aafc013831a8e0440afc336ae1cba
    .. [l2] https://github.com/torvalds/linux/commit/5620a0d1aacd554ebebcff373e31107bb1ef7769
    """

    def __init__(self, basedir):
        self._basedir = basedir
        self._cmd = ["reprepro", "--verbose", "--waitforlock", "10", "--ignore", "unusedarch", "--basedir", "{b}".format(b=basedir)]
        self._lock = _LOCKS.setdefault(self._basedir, threading.Lock())
        LOG.debug("Lock for reprepro repository '{r}': {o}".format(r=self._basedir, o=self._lock))

    def _call(self, args, show_command=False):
        return "{command}{output}".format(command="Running {command}\n".format(command=" ".join(self._cmd + args)) if show_command else "",
                                          output=mini_buildd.call.Call(self._cmd + args).log().check().stdout)

    def _call_locked(self, args, show_command=False):
        with self._lock:
            return self._call(args, show_command)

    def reindex(self):
        with self._lock:
            # Update reprepro dbs, and delete any packages no longer in dists.
            self._call(["--delete", "clearvanished"])

            # Purge all indices under 'dists/' (clearvanished does not remove indices of vanished distributions)
            shutil.rmtree(os.path.join(self._basedir, "dists"), ignore_errors=True)

            # Finally, rebuild all indices
            self._call(["export"])

    def check(self):
        return self._call_locked(["check"])

    def list(self, pattern, distribution, typ=None, list_max=50):
        result = []
        for item in self._call_locked(["--list-format", "${package}|${$type}|${architecture}|${version}|${$source}|${$sourceversion}|${$codename}|${$component};",
                                       "--list-max", "{m}".format(m=list_max)]
                                      + (["--type", "{t}".format(t=typ)] if typ else [])
                                      + ["listmatched",
                                         distribution,
                                         pattern]).split(";"):
            if item:
                item_split = item.split("|")
                result.append({"package": item_split[0],
                               "type": item_split[1],
                               "architecture": item_split[2],
                               "version": item_split[3],
                               "source": item_split[4],
                               "sourceversion": item_split[5],
                               "distribution": item_split[6],
                               "component": item_split[7],
                               })
        return result

    def show(self, package):
        result = []
        # reprepro ls format: "${$source} | ${$sourceversion} |    ${$codename} | source\n"
        for item in self._call_locked(["--type", "dsc",
                                       "ls",
                                       package]).split("\n"):
            if item:
                item_split = item.split("|")
                result.append({"source": item_split[0].strip(),
                               "sourceversion": item_split[1].strip(),
                               "distribution": item_split[2].strip(),
                               })
        return result

    def migrate(self, package, src_distribution, dst_distribution, version=None):
        return self._call_locked(["copysrc", dst_distribution, src_distribution, package] + ([version] if version else []), show_command=True)

    def remove(self, package, distribution, version=None):
        return self._call_locked(["removesrc", distribution, package] + ([version] if version else []), show_command=True)

    def install(self, changes, distribution):
        return self._call_locked(["include", distribution, changes], show_command=True)

    def install_dsc(self, dsc, distribution):
        return self._call_locked(["includedsc", distribution, dsc], show_command=True)
