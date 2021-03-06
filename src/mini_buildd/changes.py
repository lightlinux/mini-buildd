import os
import stat
import glob
import fnmatch
import logging
import tarfile
import socket
import ftplib
import urllib.parse
import re
import contextlib

import debian.deb822

import mini_buildd.config
import mini_buildd.misc
import mini_buildd.net
import mini_buildd.gnupg

import mini_buildd.models.repository
import mini_buildd.models.gnupg

LOG = logging.getLogger(__name__)


class Changes(debian.deb822.Changes):  # pylint: disable=too-many-ancestors
    class Options():
        """
        Uploader options in changes.

        >>> "{}".format(Changes("test-data/changes.options").options)
        "auto-ports=['jessie-test-unstable', 'squeeze-test-snasphot'], ignore-lintian=True, ignore-lintian[i386]=False, internal-apt-priority=543, run-lintian=True, run-lintian[i386]=False"

        >>> "{}".format(Changes("test-data/changes.magic").options)
        "auto-ports=['jessie-test-unstable', 'squeeze-test-snasphot'], ignore-lintian=True"
        """

        class Bool():
            _TRUE = ["true", "1"]
            _FALSE = ["false", "0"]
            _VALID = _TRUE + _FALSE

            def __init__(self, raw_value):
                if raw_value.lower() not in self._VALID:
                    raise Exception("Bool value must be one of {}".format(",".join(self._VALID)))
                self.value = raw_value.lower() in self._TRUE

        class Int():
            def __init__(self, raw_value):
                self.value = int(raw_value)

        class CSV():
            def __init__(self, raw_value):
                self.value = raw_value.split(",")

        KEYWORD = "MINI_BUILDD_OPTION"
        _OPTIONS = {"ignore-lintian": Bool,
                    "run-lintian": Bool,
                    "internal-apt-priority": Int,
                    "auto-ports": CSV}

        @classmethod
        def _get_top_changes(cls, upload_changes):
            """
            Filter only the first block from the changes (changelog) entry.

            Upload changes may include multiple version blocks from
            the changelog (internal porting does it, for example),
            but we must only consider values from the top one.
            """
            result = ""
            header_found = False
            for line in upload_changes.get("Changes", "").splitlines(True):
                if re.match(r"^ [a-z0-9]+", line):
                    if header_found:
                        break
                    header_found = True
                result += line
            return result

        def _compat_parse_magic(self):
            """Compat parse support for old style "magic" options."""
            def warning(magic, option):
                LOG.warning("Deprecated \"magic\" option \"{m}\" found. Please use new-style option \"{o}\" instead (see user manual).".format(m=magic, o=option))

            magic_auto_backports = re.search(r"\*\s*MINI_BUILDD:\s*AUTO_BACKPORTS:\s*([^*.\[\]]+)", self._top_changes)
            if magic_auto_backports:
                warning("AUTO_BACKPORTS", "auto-ports")
                self._set("auto-ports", magic_auto_backports.group(1))

            magic_backport_mode = re.search(r"\*\s*MINI_BUILDD:\s*BACKPORT_MODE", self._top_changes)
            if magic_backport_mode:
                warning("BACKPORT_MODE", "ignore-lintian")
                self._set("ignore-lintian", "true")

        def __init__(self, upload_changes):
            self._top_changes = self._get_top_changes(upload_changes)
            self._options = {}
            matches = re.findall(r"\*\s*{keyword}:\s*([^*.]+)=([^*.]+)".format(keyword=self.KEYWORD), self._top_changes)
            for m in matches:
                self._set(m[0], m[1])

            self._compat_parse_magic()

        def __str__(self):
            return ", ".join("{k}={v}".format(k=key, v=value) for key, value in sorted(self._options.items()))

        def _set(self, key, raw_value):
            base_key = key.partition("[")[0]
            value = re.sub(r"\s+", "", raw_value)

            # Validity check for key
            if base_key not in list(self._OPTIONS.keys()):
                raise Exception("Unknown upload option: {k}.".format(k=key))

            # Duplicity check
            if key in list(self._options.keys()):
                raise Exception("Duplicate upload option: {k}.".format(k=key))

            # Value conversion check
            converted_value = None
            try:
                converted_value = self._OPTIONS[base_key](value)
            except Exception as e:
                raise Exception("Invalid upload option value: {k}=\"{v}\" ({e})".format(k=key, v=value, e=e))

            self._options[key] = converted_value.value

            LOG.debug("Upload option set: {k}=\"{v}\"".format(k=key, v=value))

        def get(self, key, alt=None, default=None):
            """Get first existing option value in this order: key[a], key, default."""
            # Validity check for key
            if key not in list(self._OPTIONS.keys()):
                raise Exception("Internal error: Upload Options: Unknown key used for get(): {k}.".format(k=key))

            if alt:
                m_key = "{k}[{a}]".format(k=key, a=alt)
                if m_key in self._options:
                    return self._options.get(m_key, default)
            return self._options.get(key, default)

    # Extra mini-buildd changes file types we invent
    TYPE_DEFAULT = 0
    TYPE_BREQ = 1
    TYPE_BRES = 2
    TYPE2FILENAME_ID = {TYPE_DEFAULT: "",
                        TYPE_BREQ: "_mini-buildd-buildrequest",
                        TYPE_BRES: "_mini-buildd-buildresult"}

    TYPE2NAME = {TYPE_DEFAULT: "upload",
                 TYPE_BREQ: "buildrequest",
                 TYPE_BRES: "buildresult"}

    BUILDREQUEST_RE = re.compile("^.+" + TYPE2FILENAME_ID[TYPE_BREQ] + "_[^_]+.changes$")
    BUILDRESULT_RE = re.compile("^.+" + TYPE2FILENAME_ID[TYPE_BRES] + "_[^_]+.changes$")

    def _spool_hash_from_file(self):
        return None if not os.path.exists(self._file_path) else mini_buildd.misc.sha1_of_file(self._file_path)

    def __init__(self, file_path):
        self._file_path = file_path
        self._file_name = os.path.basename(file_path)
        self._new = not os.path.exists(file_path)
        # Instance might be produced from a temporary file, so we need to save the hash now.
        self._spool_hash = self._spool_hash_from_file()

        if self._new:
            super().__init__([])
        else:
            with mini_buildd.misc.open_utf8(file_path) as cf:
                super().__init__(cf)

        self._options = None
        if self.BUILDREQUEST_RE.match(self._file_name):
            self._type = self.TYPE_BREQ
        elif self.BUILDRESULT_RE.match(self._file_name):
            self._type = self.TYPE_BRES
        else:
            self._type = self.TYPE_DEFAULT

        # Be sure base dir is always available
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # This is just for stat/display purposes
        self.remote_http_url = None
        self.live_buildlog_url = None

    def __str__(self):
        if self.type == self.TYPE_BREQ:
            return "Buildrequest from '{h}': {i}".format(h=self.get("Upload-Result-To"), i=self.get_pkg_id(with_arch=True))
        if self.type == self.TYPE_BRES:
            return "Buildresult from '{h}': {i}".format(h=self.get("Built-By"), i=self.get_pkg_id(with_arch=True))
        return "User upload: {i}".format(i=self.get_pkg_id())

    @property
    def options(self):
        """note:: We can't parse this in constructor currently: Upload option error handling won't work properly, exceptions triggered too early in packager.py."""
        if not self._options:
            self._options = self.Options(self)
        return self._options

    @property
    def type(self):
        return self._type

    @classmethod
    def gen_changes_file_name(cls, package, version, arch, mbd_type=TYPE_DEFAULT):
        """
        Gen any changes file name.

        Always strip epoch from version, and handle special
        mini-buildd types.

        >>> Changes.gen_changes_file_name("mypkg", "1.2.3-1", "mips")
        'mypkg_1.2.3-1_mips.changes'
        >>> Changes.gen_changes_file_name("mypkg", "7:1.2.3-1", "mips")
        'mypkg_1.2.3-1_mips.changes'
        >>> Changes.gen_changes_file_name("mypkg", "7:1.2.3-1", "mips", mbd_type=Changes.TYPE_BREQ)
        'mypkg_1.2.3-1_mini-buildd-buildrequest_mips.changes'
        >>> Changes.gen_changes_file_name("mypkg", "7:1.2.3-1", "mips", mbd_type=Changes.TYPE_BRES)
        'mypkg_1.2.3-1_mini-buildd-buildresult_mips.changes'
        """
        return "{p}_{v}{x}_{a}.changes".format(p=package,
                                               v=mini_buildd.misc.strip_epoch(version),
                                               a=arch,
                                               x=cls.TYPE2FILENAME_ID[mbd_type])

    def gen_file_name(self, arch, mbd_type):
        return self.gen_changes_file_name(self["Source"], self["Version"], arch, mbd_type)

    @classmethod
    def gen_dsc_file_name(cls, package, version):
        return "{s}_{v}.dsc".format(s=package, v=mini_buildd.misc.strip_epoch(version))

    @property
    def dsc_name(self):
        return self.gen_dsc_file_name(self["Source"], self["Version"])

    @property
    def dsc_file_name(self):
        return os.path.join(os.path.dirname(self._file_path), self.dsc_name)

    @property
    def bres_stat(self):
        return "Build={build}, Lintian={lintian}".format(build=self.get("Sbuild-Status"), lintian=self.get("Sbuild-Lintian"))

    @property
    def file_name(self):
        return self._file_name

    @property
    def file_path(self):
        return self._file_path

    @property
    def buildlog_name(self):
        return "{s}_{v}_{a}.buildlog".format(s=self["Source"], v=self["Version"], a=self["Architecture"])

    @property
    def live_buildlog_name(self):
        return "{spool_id}.buildlog".format(spool_id=self.get_spool_id())

    def get_live_buildlog_loc(self):
        return "/mini_buildd/live-buildlogs/{logfile}".format(logfile=self.live_buildlog_name)

    def get_live_buildlog_url(self, base_url):
        return urllib.parse.urljoin(base_url, self.get_live_buildlog_loc())

    def get_pkglog_dir(self, installed, relative=True):
        """
        Get package log dir.

        Package log path for this changes file: REPOID/[_failed]/PACKAGE/VERSION/ARCH

        In case the changes is bogus (i.e, cannot produce a
        valid path for us, like a wrong distribution), None is
        returned.
        """
        try:
            return mini_buildd.misc.PkgLog.get_path(mini_buildd.misc.Distribution(mini_buildd.models.repository.map_incoming_distribution(self["Distribution"])).repository,
                                                    installed,
                                                    self["Source"],
                                                    self["Version"],
                                                    architecture=self["Architecture"],
                                                    relative=relative)
        except BaseException as e:
            mini_buildd.config.log_exception(LOG, "No package log dir for bogus changes: {f}".format(f=self.file_name), e, logging.DEBUG)

    def is_new(self):
        return self._new

    def get_spool_id(self):
        return "{type}-{hash}".format(type=self.TYPE2NAME[self._type], hash=self._spool_hash)

    def get_spool_dir(self):
        return os.path.join(mini_buildd.config.SPOOL_DIR, self.get_spool_id())

    def get_pkg_id(self, with_arch=False, arch_separator=":"):
        pkg_id = "{s}_{v}".format(s=self["Source"], v=self["Version"])
        if with_arch:
            pkg_id += "{s}{a}".format(s=arch_separator, a=self["Architecture"])
        return pkg_id

    def get_files(self, key=None):
        return [f[key] if key else f for f in self.get("Files", [])]

    def add_file(self, file_name):
        self.setdefault("Files", [])
        self["Files"].append({"md5sum": mini_buildd.misc.md5_of_file(file_name),
                              "size": os.path.getsize(file_name),
                              "section": "mini-buildd",
                              "priority": "extra",
                              "name": os.path.basename(file_name)})

    def save(self, gnupg=None):
        """
        Write to file (optionally signed).

        >>> import tempfile
        >>> t = tempfile.NamedTemporaryFile()
        >>> c = Changes(t.name)
        >>> c["key"] = "ASCII value"
        >>> c.save(None)
        >>> c["key"] = "Ünicöde «value»"
        >>> c.save(None)
        """
        try:
            LOG.info("Saving changes: {f}".format(f=self._file_path))
            with open(self._file_path, "w+", encoding=mini_buildd.config.CHAR_ENCODING) as f:
                f.write(self.dump())

            LOG.info("Signing changes: {f}".format(f=self._file_path))
            if gnupg:
                gnupg.sign(self._file_path)
            self._spool_hash = self._spool_hash_from_file()
        except BaseException:
            # Existence of the file name is used as flag
            if os.path.exists(self._file_path):
                os.remove(self._file_path)
            raise

    def upload(self, endpoint):
        upload = os.path.splitext(self._file_path)[0] + ".upload"
        if os.path.exists(upload):
            with mini_buildd.misc.open_utf8(upload) as uf:
                LOG.info("FTP: '{f}' already uploaded to '{h}'...".format(f=self._file_name, h=uf.read()))
        else:
            ftp = ftplib.FTP()
            host, port = endpoint.option("host"), endpoint.option("port")
            ftp.connect(host, int(port))
            ftp.login()
            ftp.cwd("/incoming")
            for fd in self.get_files() + [{"name": self._file_name}]:
                f = fd["name"]
                LOG.debug("FTP: Uploading file: '{f}'".format(f=f))
                with open(os.path.join(os.path.dirname(self._file_path), f), "rb") as fi:
                    ftp.storbinary("STOR {f}".format(f=f), fi)
            with mini_buildd.misc.open_utf8(upload, "w") as fi:
                fi.write("{h}:{p}".format(h=host, p=port))
            LOG.info("FTP: '{f}' uploaded to '{h}'...".format(f=self._file_name, h=host))

    def upload_buildrequest(self, local_endpoint):
        arch = self["Architecture"]
        codename = self["Base-Distribution"]

        remotes = {}

        def add_remote(remote, update):
            status = remote.mbd_get_status(update)
            status.url = remote.mbd_http2url()  # Not cool: Monkey patching status for url
            if status.running and status.has_chroot(codename, arch):
                remotes[status.load] = status
                LOG.debug("Remote[{load}]={remote}".format(load=status.load, remote=remote))

        def check_remote(remote):
            try:
                mini_buildd.models.gnupg.Remote.Admin.mbd_check(None, remote, force=True)
                add_remote(remote, False)
            except BaseException as e:
                mini_buildd.config.log_exception(LOG, "Builder check failed", e, logging.WARNING)

        # Always add our own instance as pseudo remote first
        add_remote(mini_buildd.models.gnupg.Remote(http="{proto}:{hopo}".format(proto=mini_buildd.config.HTTPD_ENDPOINTS[0].url_scheme, hopo=local_endpoint.hopo())), True)

        # Check all active or auto-deactivated remotes
        for r in mini_buildd.models.gnupg.Remote.mbd_get_active_or_auto_reactivate():
            check_remote(r)

        if not remotes:
            raise Exception("No builder found for {c}/{a}".format(c=codename, a=arch))

        for _load, remote in sorted(remotes.items()):
            try:
                self.upload(mini_buildd.net.ClientEndpoint(mini_buildd.net.Endpoint.hopo2desc(remote.ftp, server=False), mini_buildd.net.Protocol.FTP))
                self.remote_http_url = remote.url
                self.live_buildlog_url = self.get_live_buildlog_url(base_url=remote.url)
                return
            except BaseException as e:
                mini_buildd.config.log_exception(LOG, "Uploading to '{h}' failed".format(h=remote.ftp), e, logging.WARNING)

        raise Exception("Buildrequest upload failed for {a}/{c}".format(a=arch, c=codename))

    def tar(self, tar_path, add_files=None, exclude_globs=None):
        exclude_globs = exclude_globs if exclude_globs else []

        def exclude(file_name):
            for e in exclude_globs:
                if fnmatch.fnmatch(file_name, e):
                    return True
            return False

        with contextlib.closing(tarfile.open(tar_path, "w")) as tar:
            def tar_add(file_name):
                if exclude(file_name):
                    LOG.info("Excluding \"{f}\" from tar archive \"{tar}\".".format(f=file_name, tar=tar_path))
                else:
                    tar.add(file_name, arcname=os.path.basename(file_name))

            tar_add(self._file_path)
            for f in self.get_files():
                tar_add(os.path.join(os.path.dirname(self._file_path), f["name"]))
            if add_files:
                for f in add_files:
                    tar_add(f)

    def untar(self, path):
        tar_file = self._file_path + ".tar"
        if os.path.exists(tar_file):
            with contextlib.closing(tarfile.open(tar_file, "r")) as tar:
                tar.extractall(path=path)
        else:
            LOG.info("No tar file (skipping): {f}".format(f=tar_file))

    def move_to_pkglog(self, installed, rejected=False):
        logdir = None if rejected else self.get_pkglog_dir(installed, relative=False)

        if logdir and not os.path.exists(logdir):
            os.makedirs(logdir)

        LOG.info("Moving changes to package log: '{f}'->'{d}'".format(f=self._file_path, d=logdir))
        for fd in [{"name": self._file_name}] + self.get_files():
            f = fd["name"]
            f_abs = os.path.join(os.path.dirname(self._file_path), f)
            # If not installed, just move all files to log dir.
            # If installed, only save buildlogs and changes.
            if logdir and (not installed or re.match(r"(.*\.buildlog$|.*changes$)", f)):
                LOG.info("Moving '{f}' to '{d}'". format(f=f, d=logdir))
                os.rename(f_abs, os.path.join(logdir, f))
            else:
                LOG.info("Removing '{f}'". format(f=f))
                mini_buildd.misc.skip_if_keep_in_debug(os.remove, f_abs)

    def remove(self):
        LOG.info("Removing changes: '{f}'".format(f=self._file_path))
        for fd in [{"name": self._file_name}] + self.get_files():
            f = os.path.join(os.path.dirname(self._file_path), fd["name"])
            LOG.debug("Removing: '{f}'".format(f=fd["name"]))
            os.remove(f)

    def gen_buildrequests(self, daemon, repository, dist, suite_option):
        """
        Build buildrequest files for all architectures.

        .. todo:: **IDEA**: gen_buildrequests(): Instead of tar'ing ourselves (uploaded changes)
                  with exceptions (.deb, .buildinfo, .changes), add the *.dsc* and its files only!
        """
        # Extra check on all DSC/source package files
        # - Check md5 against possible pool files.
        # - Add missing from pool (i.e., orig.tar.gz).
        # - make sure all files from dsc are actually available
        files_from_pool = []
        with open(self.dsc_file_name) as dsc_file:
            dsc = debian.deb822.Dsc(dsc_file)

        for f in dsc["Files"]:
            in_changes = f["name"] in self.get_files(key="name")
            from_pool = False
            for p in glob.glob(os.path.join(repository.mbd_get_path(), "pool", "*", "*", self["Source"], f["name"])):
                if f["md5sum"] == mini_buildd.misc.md5_of_file(p):
                    if not in_changes:
                        files_from_pool.append(p)
                        from_pool = True
                        LOG.info("Buildrequest: File added from pool: {f}".format(f=p))
                else:
                    raise Exception("MD5 mismatch in uploaded dsc vs. pool: {f}".format(f=f["name"]))

            # Check that this file is available
            if not in_changes and not from_pool:
                raise Exception("Missing file '{f}' neither in upload, nor in pool (use '-sa' for uploads with new upstream)".format(f=f["name"]))

        breq_dict = {}
        for ao in dist.architectureoption_set.all():
            path = os.path.join(self.get_spool_dir(), ao.architecture.name)

            breq = Changes(os.path.join(path,
                                        self.gen_file_name(ao.architecture.name, self.TYPE_BREQ)))

            if breq.is_new():
                distribution = mini_buildd.misc.Distribution(mini_buildd.models.repository.map_incoming_distribution(self["Distribution"]))
                breq["Distribution"] = distribution.get()
                for v in ["Source", "Version"]:
                    breq[v] = self[v]

                # Generate files
                chroot_setup_script = os.path.join(path, "chroot_setup_script")
                with mini_buildd.misc.open_utf8(os.path.join(path, "apt_sources.list"), "w") as asl, \
                     mini_buildd.misc.open_utf8(os.path.join(path, "apt_preferences"), "w") as ap, \
                     mini_buildd.misc.open_utf8(os.path.join(path, "apt_keys"), "w") as ak, \
                     mini_buildd.misc.open_utf8(os.path.join(path, "ssl_cert"), "w") as ssl_cert, \
                     mini_buildd.misc.open_utf8(chroot_setup_script, "w") as css, \
                     mini_buildd.misc.open_utf8(os.path.join(path, "sbuildrc_snippet"), "w") as src:
                    asl.write(dist.mbd_get_apt_sources_list(repository, suite_option))
                    ap.write(dist.mbd_get_apt_preferences(repository, suite_option, self.options.get("internal-apt-priority")))
                    ak.write(repository.mbd_get_apt_keys(dist))
                    ssl_cert.write(mini_buildd.config.HTTPD_ENDPOINTS[0].get_certificate())
                    css.write(mini_buildd.misc.fromdos(dist.chroot_setup_script))  # Note: For some reason (python, django sqlite, browser?) the text field may be in DOS mode.
                    os.chmod(chroot_setup_script, stat.S_IRWXU)
                    src.write(dist.mbd_get_sbuildrc_snippet(ao.architecture.name))

                # Generate tar from original changes
                self.tar(tar_path=breq.file_path + ".tar",
                         add_files=[os.path.join(path, "apt_sources.list"),
                                    os.path.join(path, "apt_preferences"),
                                    os.path.join(path, "apt_keys"),
                                    os.path.join(path, "ssl_cert"),
                                    chroot_setup_script,
                                    os.path.join(path, "sbuildrc_snippet")] + files_from_pool,
                         exclude_globs=["*.deb", "*.changes", "*.buildinfo"])
                breq.add_file(breq.file_path + ".tar")

                breq["Upload-Result-To"] = daemon.mbd_get_ftp_endpoint().hopo()
                breq["Base-Distribution"] = dist.base_source.codename
                breq["Architecture"] = ao.architecture.name
                if ao.build_architecture_all:
                    breq["Arch-All"] = "Yes"
                breq["Build-Dep-Resolver"] = dist.get_build_dep_resolver_display()
                breq["Apt-Allow-Unauthenticated"] = "1" if dist.apt_allow_unauthenticated else "0"
                if dist.lintian_mode != dist.LINTIAN_DISABLED and self.options.get("run-lintian", alt=ao.architecture.name, default=True):
                    # Generate lintian options
                    modeargs = {
                        dist.LINTIAN_DISABLED: "",
                        dist.LINTIAN_RUN_ONLY: "",
                        dist.LINTIAN_FAIL_ON_ERROR: "",
                        dist.LINTIAN_FAIL_ON_WARNING: "--fail-on-warning"}
                    breq["Run-Lintian"] = modeargs[dist.lintian_mode] + " " + dist.lintian_extra_options
                breq["Deb-Build-Options"] = dist.mbd_get_extra_option("Deb-Build-Options", "")

                breq.save(daemon.mbd_gnupg)
            else:
                LOG.info("Re-using existing buildrequest: {b}".format(b=breq.file_name))
            breq_dict[ao.architecture.name] = breq

        return breq_dict

    def gen_buildresult(self, path=None):
        assert self.type == self.TYPE_BREQ
        if not path:
            path = self.get_spool_dir()

        bres = mini_buildd.changes.Changes(os.path.join(path,
                                                        self.gen_file_name(self["Architecture"], self.TYPE_BRES)))

        for v in ["Distribution", "Source", "Version", "Architecture"]:
            bres[v] = self[v]

        return bres

    def upload_failed_buildresult(self, gnupg, endpoint, retval, status, exception):
        with contextlib.closing(mini_buildd.misc.TmpDir()) as t:
            bres = self.gen_buildresult(path=t.tmpdir)

            bres["Sbuildretval"] = str(retval)
            bres["Sbuild-Status"] = status
            buildlog = os.path.join(t.tmpdir, self.buildlog_name)
            with mini_buildd.misc.open_utf8(buildlog, "w+") as log_file:
                log_file.write("""
Host: {h}
Build request failed: {r} ({s}): {e}
""".format(h=socket.getfqdn(), r=retval, s=status, e=exception))
            bres.add_file(buildlog)
            bres.save(gnupg)
            bres.upload(endpoint)
