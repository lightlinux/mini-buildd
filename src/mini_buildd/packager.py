import os
import shutil
import logging

import django.utils.timezone

import mini_buildd.misc


LOG = logging.getLogger(__name__)


class Package(mini_buildd.misc.Status):
    FAILED = -2
    REJECTED = -1
    CHECKING = 0
    BUILDING = 1
    INSTALLING = 2
    INSTALLED = 10

    def __init__(self, daemon, changes):
        super().__init__(
            stati={self.FAILED: "FAILED",
                   self.REJECTED: "REJECTED",
                   self.CHECKING: "CHECKING",
                   self.BUILDING: "BUILDING",
                   self.INSTALLING: "INSTALLING",
                   self.INSTALLED: "INSTALLED"})

        self.started = django.utils.timezone.now()
        self.finished = None

        self.daemon = daemon
        self.changes = changes
        self.pid = changes.get_pkg_id()
        self.repository, self.distribution, self.suite, self.distribution_string = None, None, None, None
        self.requests, self.success, self.failed = {}, {}, {}
        self.port_report = {}

    def __str__(self):
        def arch_status():
            result = []
            for key, _r in list(self.requests.items()):
                p = ""
                if key in self.success:
                    p = "+"
                elif key in self.failed:
                    p = "-"
                result.append("{p}{a}".format(p=p, a=key))
            return result

        return mini_buildd.misc.pkg_fmt(self.status,
                                        self.distribution_string,
                                        self.changes["Source"],
                                        self.changes["Version"],
                                        extra=" ".join(arch_status()),
                                        message=self.status_desc)

    @property
    def took(self):
        return round((self.finished - self.started).total_seconds(), 1) if self.finished else "n/a"

    def precheck(self):
        # Get/check repository, distribution and suite for changes
        self.repository, self.distribution, self.suite, rollback = self.daemon.parse_distribution(self.changes["Distribution"])
        # Actual full distribution string; A shortcut for
        # 'self.changes["Distribution"]', but may also differ if there's a meta distribution like unstable in changes.
        self.distribution_string = self.suite.mbd_get_distribution_string(self.repository, self.distribution)

        if rollback is not None:
            raise Exception("Rollback distribution are not uploadable")

        if not self.suite.uploadable:
            raise Exception("Suite '{s}' is not uploadable".format(s=self.suite))

        if not self.repository.mbd_is_active():
            raise Exception("Repository '{r}' is not active".format(r=self.repository))

        # Authenticate
        if self.repository.allow_unauthenticated_uploads:
            LOG.warning("Unauthenticated uploads allowed. Using '{c}' unchecked".format(c=self.changes.file_name))
        else:
            self.daemon.keyrings.get_uploaders()[self.repository.identity].verify(self.changes.file_path)

        # Repository package prechecks
        self.repository.mbd_package_precheck(self.distribution, self.suite, self.changes["Source"], self.changes["Version"])

        # Generate build requests
        self.requests = self.changes.gen_buildrequests(self.daemon.model, self.repository, self.distribution, self.suite)

        # Upload buildrequests
        for _key, breq in list(self.requests.items()):
            try:
                breq.upload_buildrequest(self.daemon.model.mbd_get_http_endpoint())
            except BaseException as e:
                mini_buildd.setup.log_exception(LOG,
                                                "{i}: Buildrequest upload failed".format(i=breq.get_pkg_id()),
                                                e)
                # Upload failure build result to ourselves
                breq.upload_failed_buildresult(self.daemon.model.mbd_gnupg, self.daemon.model.mbd_get_ftp_endpoint(), 100, "upload-failed", e)

    def add_buildresult(self, bres):
        self.daemon.keyrings.get_remotes().verify(bres.file_path)

        arch = bres["Architecture"]

        # Retval and status must be the same with sbuild in mode user, so status is not really needed
        # status may also be none in case some non-sbuild build error occurred
        retval = int(bres["Sbuildretval"])
        status = bres.get("Sbuild-Status")
        lintian = bres.get("Sbuild-Lintian")

        LOG.info("{p}: Got build result for '{a}': {r}={s}, lintian={lintian}".format(p=self.pid, a=arch, r=retval, s=status, lintian=lintian))

        def check_lintian(arch):
            return lintian == "pass" or \
                self.suite.experimental or \
                self.distribution.lintian_mode < self.distribution.LINTIAN_FAIL_ON_ERROR or \
                self.changes.options.get("ignore-lintian", alt=arch, default=False)

        if retval == 0 and (status == "skipped" or check_lintian(arch)):
            self.success[arch] = bres
        else:
            self.failed[arch] = bres

        missing = len(self.requests) - len(self.success) - len(self.failed)
        if missing <= 0:
            self.finished = django.utils.timezone.now()

    def install(self):
        """
        Install package to repository.

        This may throw on error, and if so, no changes should be
        done to the repo.
        """
        # Install to reprepro repository
        self.repository.mbd_package_install(self.distribution, self.suite, self.changes, self.success)

        # Installed. Finally, try to serve auto ports
        for to_dist_str in self.changes.options.get("auto-ports", default=[]):
            try:
                self.daemon.port(self.changes["Source"],
                                 self.distribution_string,
                                 to_dist_str,
                                 self.changes["Version"])
                self.port_report[to_dist_str] = "Requested"
            except BaseException as e:
                self.port_report[to_dist_str] = "FAILED: {e}".format(e=e)
                mini_buildd.setup.log_exception(LOG, "{i}: Automatic package port failed for: {d}".format(i=self.changes.get_pkg_id(), d=to_dist_str), e)

    def move_to_pkglog(self):
        # Archive build results and request
        for _arch, c in list(self.success.items()) + list(self.failed.items()) + list(self.requests.items()):
            c.move_to_pkglog(self.get_status() == self.INSTALLED, rejected=self.get_status() == self.REJECTED)
        # Archive incoming changes
        self.changes.move_to_pkglog(self.get_status() == self.INSTALLED, rejected=self.get_status() == self.REJECTED)

        # Purge complete package spool dir (if precheck failed, spool dir will not be present, so we need to ignore errors here)
        mini_buildd.misc.skip_if_keep_in_debug(mini_buildd.misc.rmdirs, self.changes.get_spool_dir())

        # Hack: In case the changes comes from a temporary directory (ports!), we take care of purging that tmpdir here
        tmpdir = mini_buildd.misc.TmpDir.file_dir(self.changes.file_path)
        if tmpdir:
            mini_buildd.misc.TmpDir(tmpdir).close()

        # On installed: In case there is a "failed" log of the same version, remove it.
        if self.get_status() == self.INSTALLED:
            # The pkglog_dir must be non-None on INSTALLED status
            failed_logdir = os.path.dirname(self.changes.get_pkglog_dir(installed=False, relative=False))
            LOG.debug("Purging failed log dir: {f}".format(f=failed_logdir))
            shutil.rmtree(failed_logdir, ignore_errors=True)

    def notify(self):
        def header(title, underline="-"):
            return "{t}\n{u}\n".format(t=title, u=underline * len(title))

        def bres_result(arch, bres):
            return "{a} ({s}): {b}\n".format(
                a=arch,
                s=bres.bres_stat,
                b=os.path.join(self.daemon.model.mbd_get_http_url(),
                               "log",
                               bres.get_pkglog_dir(self.get_status() == self.INSTALLED),
                               bres.buildlog_name))

        results = header(self.__str__(), "=")
        results += "\n"

        if self.failed:
            results += header("Failed builds")
            for arch, bres in list(self.failed.items()):
                results += bres_result(arch, bres)
            results += "\n"

        if self.success:
            results += header("Successful builds")
            for arch, bres in list(self.success.items()):
                results += bres_result(arch, bres)
            results += "\n"

        results += header("Changes")
        results += self.changes.dump()

        if self.port_report:
            results += "\n"
            results += header("Port Report")
            results += "\n".join(("{d:<25}: {r}".format(d=d, r=r) for d, r in list(self.port_report.items())))

        self.daemon.model.mbd_notify(
            self.__str__(),
            results,
            self.repository,
            self.changes)


class LastPackage(mini_buildd.misc.API):
    """Subset of 'Package' for pickled statistics."""

    __API__ = -99

    def __init__(self, package):
        super().__init__()

        self.identity = package.__str__()

        self.started = package.started
        self.took = package.took
        self.log = os.path.join("/mini_buildd/log", os.path.dirname(package.changes.get_pkglog_dir(installed=True)))

        self.changes = {}
        for k in ["source", "distribution", "version"]:
            self.changes[k] = package.changes[k]

        self.status = package.status
        self.status_desc = package.status_desc

        self.requests = {}
        for a, _r in list(package.requests.items()):
            self.requests[a] = {}

        def cp_bres(src, dst):
            for a, r in list(src.items()):
                dst[a] = {"bres_stat": r.bres_stat,
                          "log": os.path.join("/log", r.get_pkglog_dir(package.get_status() == package.INSTALLED), r.buildlog_name)}

        self.success = {}
        cp_bres(package.success, self.success)

        self.failed = {}
        cp_bres(package.failed, self.failed)

    def __str__(self):
        return self.identity


def package_close(daemon, package):
    """Close package. Just continue on errors, but log them; guarantee to remove it from the packages dict."""
    try:
        package.move_to_pkglog()
        package.notify()
        daemon.last_packages.appendleft(LastPackage(package))
    except BaseException as e:
        mini_buildd.setup.log_exception(LOG, "Error closing package '{p}'".format(p=package.pid), e, level=logging.CRITICAL)
    finally:
        del daemon.packages[package.pid]


def run(daemon, changes):
    pid = changes.get_pkg_id()

    if changes.type == changes.TYPE_BRES:
        if pid not in daemon.packages:
            raise Exception("{p}: Stray build result (not building here).".format(p=pid))

        package = daemon.packages[pid]

        try:
            package.add_buildresult(changes)
            if package.finished:
                package.install()
                package.set_status(package.INSTALLED)
                package_close(daemon, package)
        except BaseException as e:
            package.set_status(package.FAILED, str(e))
            package_close(daemon, package)
            mini_buildd.setup.log_exception(LOG, "Package '{p}' FAILED".format(p=pid), e)

    else:  # User upload
        if pid in daemon.packages:
            raise Exception("Internal error: Uploaded package already in packages list.")

        package = mini_buildd.packager.Package(daemon, changes)
        daemon.packages[pid] = package
        try:
            package.precheck()
            package.set_status(package.BUILDING)
        except BaseException as e:
            package.set_status(package.REJECTED, str(e))
            package_close(daemon, package)
            mini_buildd.setup.log_exception(LOG, "Package '{p}' REJECTED".format(p=pid), e)
