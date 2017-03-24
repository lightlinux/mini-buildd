# -*- coding: utf-8 -*-

import os
import datetime
import shutil
import glob
import re
import subprocess
import logging

import mini_buildd.setup
import mini_buildd.misc
import mini_buildd.call
import mini_buildd.changes

LOG = logging.getLogger(__name__)


class Build(mini_buildd.misc.Status):
    FAILED = -1
    CHECKING = 0
    BUILDING = 1
    UPLOADING = 2
    UPLOADED = 10

    def __init__(self, breq, gnupg, sbuild_jobs):
        super(Build, self).__init__(
            stati={self.FAILED: "FAILED",
                   self.CHECKING: "CHECKING",
                   self.BUILDING: "BUILDING",
                   self.UPLOADING: "UPLOADING",
                   self.UPLOADED: "UPLOADED"})

        self._breq = breq
        self._gnupg = gnupg
        self._sbuild_jobs = sbuild_jobs

        self._build_dir = self._breq.get_spool_dir()
        self._chroot = "mini-buildd-{d}-{a}".format(d=self._breq["Base-Distribution"], a=self.architecture)
        # Always generate the now out-of-chroot 'libdir' (needed for ccache, ...).
        os.makedirs(mini_buildd.misc.chroot_libdir_path(self._breq["Base-Distribution"], self.architecture), exist_ok=True)

        self._bres = breq.gen_buildresult()

        self.started = self._get_started_stamp()
        if self.started:
            self.set_status(self.BUILDING)

        self.built = self._get_built_stamp()
        if self.built:
            self.set_status(self.UPLOADING)

        self.uploaded = None

        self.live_buildlog_url = breq.get_live_buildlog_loc()

    def __str__(self):
        date_format = "%Y-%b-%d %H:%M:%S"
        return "{s}: [{h}] {k} ({c}): Started {start} ({took} seconds), uploaded {uploaded}: {desc}".format(
            s=self.status,
            h=self.upload_result_to,
            k=self.key,
            c=self._chroot,
            start=self.started.strftime(date_format) if self.started else "n/a",
            took=self.took,
            uploaded=self.uploaded.strftime(date_format) if self.uploaded else "n/a",
            desc=self.status_desc)

    @property
    def key(self):
        return self._breq.get_pkg_id(with_arch=True)

    @property
    def package(self):
        return self._breq["Source"]

    @property
    def version(self):
        return self._breq["Version"]

    @property
    def architecture(self):
        return self._breq["Architecture"]

    @property
    def distribution(self):
        return self._breq["Distribution"]

    @property
    def upload_result_to(self):
        return self._breq["Upload-Result-To"]

    @property
    def sbuildrc_path(self):
        return os.path.join(self._build_dir, ".sbuildrc")

    def _get_started_stamp(self):
        if os.path.exists(self.sbuildrc_path):
            return datetime.datetime.fromtimestamp(os.path.getmtime(self.sbuildrc_path))

    def _get_built_stamp(self):
        if os.path.exists(self._bres.file_path):
            return datetime.datetime.fromtimestamp(os.path.getmtime(self._bres.file_path))

    @property
    def took(self):
        return round((self.built - self.started).total_seconds(), 1) if self.built else "n/a"

    def _generate_sbuildrc(self):
        """
        Generate .sbuildrc for a build request (not all is configurable via switches, unfortunately).
        """
        mini_buildd.misc.ConfFile(
            self.sbuildrc_path,
            """\
# We update sources.list on the fly via chroot-setup commands;
# this update occurs before, so we don't need it.
$apt_update = 0;

# Allow unauthenticated apt toggle
$apt_allow_unauthenticated = {apt_allow_unauthenticated};

{custom_snippet}

# don't remove this, Perl needs it:
1;
""".format(apt_allow_unauthenticated=self._breq["Apt-Allow-Unauthenticated"],
           custom_snippet=mini_buildd.misc.open_utf8(os.path.join(self._build_dir, "sbuildrc_snippet")).read())).save()

    def _buildlog_to_buildresult(self, buildlog):
        """
        Parse sbuild's summary status from build log.

        .. note:: This will iterate all lines of the build log,
        and parse out the selection of sbuild's summary status
        we need.  In case the build log above does write the
        same output like 'Status: xyz', sbuild's correct status
        at the bottom will override this later.  Best thing,
        though, would be if sbuild would eventually provide a
        better way to get these values.
        """
        regex = re.compile("^(Status|Lintian): [^ ]+$")
        with open(buildlog, encoding=mini_buildd.setup.CHAR_ENCODING, errors="replace") as f:
            for l in f:
                if regex.match(l):
                    LOG.debug("Build log line detected as build status: {l}".format(l=l.strip()))
                    s = l.split(":")
                    self._bres["Sbuild-" + s[0]] = s[1].strip()

    def build(self):
        self._breq.untar(path=self._build_dir)
        self._generate_sbuildrc()
        self.started = self._get_started_stamp()

        # Check if we need to support a https apt transport
        sources_list_file = "{p}/apt_sources.list".format(p=self._build_dir)
        apt_transports = []
        with mini_buildd.misc.open_utf8(sources_list_file) as sources_list:
            for apt_line in sources_list:
                if re.match("deb https", apt_line):
                    apt_transports = ["--chroot-setup-command", "apt-get --yes --option=APT::Install-Recommends=false install ca-certificates apt-transport-https"]
                    LOG.info("{p}: Found https source: {l}".format(p=self.key, l=apt_line))
                    break

        sbuild_cmd = ["sbuild",
                      "--dist", self.distribution,
                      "--arch", self.architecture,
                      "--chroot", self._chroot]
        sbuild_cmd += apt_transports

        # Workaround: We use apt-key explicitly later, so we need gnupg. https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=831749
        # Workaround: We need to do it early (with the source chroot's base source only) to avoid apt warnings.
        # Workaround: Maybe [trusted=yes] is an option for later (>=wheezy) (see man sources.list).
        sbuild_cmd += ["--chroot-setup-command", "apt-get update",
                       "--chroot-setup-command", "apt-get --yes --option=APT::Install-Recommends=false install gnupg"]

        sbuild_cmd += ["--chroot-setup-command", "cp {s} /etc/apt/sources.list".format(s=sources_list_file),
                       "--chroot-setup-command", "cat /etc/apt/sources.list",
                       "--chroot-setup-command", "cp {p}/apt_preferences /etc/apt/preferences".format(p=self._build_dir),
                       "--chroot-setup-command", "cat /etc/apt/preferences",
                       "--chroot-setup-command", "apt-key add {p}/apt_keys".format(p=self._build_dir),
                       "--chroot-setup-command", "apt-get --option=Acquire::Languages=none update",
                       "--chroot-setup-command", "{p}/chroot_setup_script".format(p=self._build_dir),
                       "--chroot-setup-command", "cat {p}/chroot_setup_script".format(p=self._build_dir),
                       "--chroot-setup-command", "apt-cache policy",
                       "--build-dep-resolver", self._breq["Build-Dep-Resolver"],
                       "--keyid", self._gnupg.get_first_sec_key(),
                       "--nolog",
                       "--log-external-command-output",
                       "--log-external-command-error"]

        if "Arch-All" in self._breq:
            sbuild_cmd += ["--arch-all"]

        if "Run-Lintian" in self._breq:
            sbuild_cmd += ["--run-lintian"]
            # Be sure not to use --suppress-tags when its not availaible (only >=squeeze).
            if mini_buildd.misc.Distribution(self.distribution).has_lintian_suppress():
                sbuild_cmd += ["--lintian-opts", "--suppress-tags=bad-distribution-in-changes-file"]
            sbuild_cmd += ["--lintian-opts", self._breq["Run-Lintian"]]

        if "sbuild" in mini_buildd.setup.DEBUG:
            sbuild_cmd += ["--debug"]

        sbuild_cmd += [self._breq.dsc_name]

        # Actually run sbuild
        mini_buildd.call.sbuild_keys_workaround()
        buildlog = os.path.join(self._build_dir, self._breq.buildlog_name)
        live_buildlog = os.path.join(mini_buildd.setup.SPOOL_DIR, self._breq.live_buildlog_name)
        with open(buildlog, "w+") as l:
            LOG.info("Adding live buildlog: {b}".format(b=live_buildlog))
            # The spool id/hash might the very same (retry a failed build, for example) as a previous one. So we need to be sure to remove before linking.
            # Note that this currently should never really happen as python's tarball abstraction cannot produce reproducible tarballs (https://bugs.python.org/issue24465).
            if os.path.exists(live_buildlog):
                os.remove(live_buildlog)
            os.link(buildlog, live_buildlog)
            sbuild_call = mini_buildd.call.Call(sbuild_cmd,
                                                cwd=self._build_dir,
                                                env=mini_buildd.call.taint_env({"HOME": self._build_dir,
                                                                                "GNUPGHOME": os.path.join(mini_buildd.setup.HOME_DIR, ".gnupg"),
                                                                                "DEB_BUILD_OPTIONS": "parallel={j}".format(j=self._sbuild_jobs)}),
                                                stdout=l, stderr=subprocess.STDOUT)
            retval = sbuild_call.retval

        # Add build results to build request object
        self._bres["Sbuildretval"] = str(retval)
        self._buildlog_to_buildresult(buildlog)

        LOG.info("{p}: Sbuild finished: Sbuildretval={r}, Status={s}".format(p=self.key, r=retval, s=self._bres.get("Sbuild-Status")))
        self._bres.add_file(buildlog)
        build_changes_file = os.path.join(self._build_dir,
                                          mini_buildd.changes.Changes.gen_changes_file_name(self.package,
                                                                                            self.version,
                                                                                            self.architecture))
        if os.path.exists(build_changes_file):
            build_changes = mini_buildd.changes.Changes(build_changes_file)
            build_changes.tar(tar_path=self._bres.file_path + ".tar")
            self._bres.add_file(self._bres.file_path + ".tar")

        self._bres.save(self._gnupg)
        self.built = self._get_built_stamp()

    def upload(self):
        hopo = mini_buildd.misc.HoPo(self.upload_result_to)
        self._bres.upload(hopo)
        self.uploaded = datetime.datetime.now()

    def clean(self):
        mini_buildd.misc.skip_if_keep_in_debug(shutil.rmtree, self._breq.get_spool_dir())
        self._breq.remove()


class LastBuild(mini_buildd.misc.API):
    """
    Subset of 'Build' for pickled statistics.
    """
    __API__ = -99

    def __init__(self, build):
        super(LastBuild, self).__init__()
        self.identity = build.__str__()
        self.status = build.status
        self.status_desc = build.status_desc

        self.started = build.started
        self.took = build.took
        self.package = build.package
        self.version = build.version
        self.distribution = build.distribution
        self.architecture = build.architecture
        self.uploaded = build.uploaded
        self.upload_result_to = build.upload_result_to
        self.live_buildlog_url = build.live_buildlog_url

    def __str__(self):
        return self.identity


def _expire_live_buildlogs(**kwargs):
    """
    Expire live buildlogs older than timedelta. Arguments are given as-is to the datetime.timedelta constructor.
    """
    valid_until = datetime.datetime.now() - datetime.timedelta(**kwargs)
    for buildlog in glob.glob(os.path.join(mini_buildd.setup.SPOOL_DIR, "*.buildlog")):
        LOG.debug("Checking if live build log is older than {d}: {logfile}".format(d=valid_until, logfile=buildlog))
        if datetime.datetime.fromtimestamp(os.path.getmtime(buildlog)) < valid_until:
            LOG.info("Expiring live build log: {logfile}".format(logfile=buildlog))
            os.unlink(buildlog)


def build_close(daemon, build):
    """
    Close build. Just continue on errors, but log them; guarantee to remove it from the builds dict.
    """
    try:
        build.clean()
        daemon.last_builds.appendleft(LastBuild(build))
        _expire_live_buildlogs(days=5)
    except Exception as e:
        mini_buildd.setup.log_exception(LOG, "Error closing build '{p}'".format(p=build.key), e, level=logging.CRITICAL)
    finally:
        if build.key in daemon.builds:
            del daemon.builds[build.key]


def build(daemon_, breq):
    build = None
    try:
        # First, get build object. This will automagically set the status right.
        build = Build(breq, daemon_.model.mbd_gnupg, daemon_.model.sbuild_jobs)
        daemon_.builds[build.key] = build

        # Authorization
        daemon_.keyrings.get_remotes().verify(breq.file_path)

        # Build if needed (may be just an upload-pending build)
        if build.get_status() < build.BUILDING:
            build.set_status(build.BUILDING)
            build.build()
            build.set_status(build.UPLOADING)

        # Try upload
        try:
            build.upload()
            build.set_status(build.UPLOADED)
        except Exception as e:
            mini_buildd.setup.log_exception(LOG, "Upload failed (retry later)", e, logging.WARN)
            build.set_status(build.UPLOADING, str(e))

    except Exception as e:
        # Try to upload failure build result to remote
        if build:
            build.set_status(build.FAILED)
        breq.upload_failed_buildresult(daemon_.model.mbd_gnupg, mini_buildd.misc.HoPo(breq["Upload-Result-To"]), 101, "builder-failed", e)
        mini_buildd.setup.log_exception(LOG, "Internal error building", e)

    finally:
        if build:
            build_close(daemon_, build)
        daemon_.build_queue.task_done()


def run(daemon_):
    while True:
        event = daemon_.build_queue.get()
        if event == "SHUTDOWN":
            break

        LOG.info("Builder status: {s}.".format(s=daemon_.build_queue))

        mini_buildd.misc.run_as_thread(
            build,
            name="building {pkg}".format(pkg=os.path.basename(event)),
            daemon=True,
            daemon_=daemon_,
            breq=mini_buildd.changes.Changes(event))
