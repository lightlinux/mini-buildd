import os
import sys
import time
import copy
import inspect
import contextlib
import logging
import http.client

import mini_buildd.misc

LOG = logging.getLogger(__name__)

# mini-buildd API transfers log message via HTTP headers. The default (100) is sometimes too low.
http.client._MAXHEADERS = 5000  # pylint: disable=protected-access


def django_pseudo_configure():
    from mini_buildd.django_settings import pseudo_configure
    from mini_buildd.models import import_all
    from django.core.management import call_command

    pseudo_configure()
    import_all()
    call_command("migrate", interactive=False, run_syncdb=True, verbosity=0)


class Argument():
    def __init__(self, id_list, doc="Undocumented", default=None):
        """
        :param id_list: List like '['--with-rollbacks', '-R']' for option or '['distributions']' for positionals; 1st entry always denotes the id.

        >>> Argument(["--long-id", "-s"]).identity
        'long_id'
        >>> Argument(["posi-tional"]).identity
        'posi_tional'
        """
        self.id_list = id_list
        self.doc = doc
        # default, value: Always the str representation (as given on the command line)
        self.default = default
        self.raw_value = default
        self.given = False

        # identity: 1st of id_list with leading '--' removed and hyphens turned to underscores
        self.identity = id_list[0][2 if id_list[0].startswith("--") else 0:].replace("-", "_")

        # kvsargs for argparse
        self.argparse_kvsargs = {}
        self.argparse_kvsargs["help"] = doc
        if default is not None:
            self.argparse_kvsargs["default"] = default

    def _r2v(self):
        """Raw to value. Pre: self.raw_value is not None."""
        return self.raw_value

    @classmethod
    def _v2r(cls, value):
        """Value to raw."""
        return str(value)

    def set(self, value):
        if isinstance(value, str):
            self.raw_value = value
        else:
            self.raw_value = self._v2r(value)
        self.given = True

    @property
    def value(self):
        """Get value, including convenience transformations."""
        return self._r2v() if self.raw_value is not None else None

    # do we really need that?
    def false2none(self):
        return self.raw_value if self.raw_value else None


class StringArgument(Argument):
    TYPE = "string"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.argparse_kvsargs["action"] = "store"


class URLArgument(StringArgument):
    TYPE = "url"


class TextArgument(StringArgument):
    TYPE = "text"


class IntArgument(StringArgument):
    TYPE = "int"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.argparse_kvsargs["type"] = int

    def _r2v(self):
        return int(self.raw_value)


class BoolArgument(Argument):
    TYPE = "bool"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.argparse_kvsargs["action"] = "store_true"

    def _r2v(self):
        return self.raw_value in ("True", "true", "1")


class SelectArgument(Argument):
    TYPE = "select"

    def __init__(self, *args, choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.choices = [] if choices is None else choices
        if choices:
            self.argparse_kvsargs["choices"] = choices


class MultiSelectArgument(SelectArgument):
    TYPE = "multiselect"

    def __init__(self, *args, separator=",", **kwargs):
        super().__init__(*args, **kwargs)
        self.separator = separator

    def _r2v(self):
        return self.raw_value.split(self.separator)

    def _v2r(self, value):
        return self.separator.join(value)


class Command():
    COMMAND = None

    # Auth
    NONE = 0
    LOGIN = 1
    STAFF = 2
    ADMIN = 3
    AUTH = NONE

    CONFIRM = False
    NEEDS_RUNNING_DAEMON = False
    ARGUMENTS = []

    # Used in: migrate, remove, port
    COMMON_ARG_VERSION = StringArgument(["--version", "-V"], default="", doc="""
limit command to that version. Use it for the rare case of
multiple version of the same package in one distribution (in
different components), or just as safeguard
""")

    # Used in: port, portext
    COMMON_ARG_OPTIONS = StringArgument(["--options", "-O"],
                                        default="ignore-lintian=true",
                                        doc="""list of upload options, separated by '|' (see user manual for complete docs). Some useful examples:

ignore-lintian=true:
  Ignore lintian failures (install anyway).
run-lintian=false:
  Avoid lintian run in the 1st place.
internal-apt-priority=500:
  Install newer versions from our repo (even if deps don't require it).
auto-ports=buster-test-unstable
  List of distributions (comma-separated) to automatically run ports for after successful install.
""")

    def __init__(self, given_args, daemon=None, request=None, msglog=LOG):
        self.args = {}
        for arg in self.ARGUMENTS:
            self.args[arg.identity] = copy.copy(arg)

        self.daemon = daemon
        self.request = request
        self.msglog = msglog
        self._plain_result = ""

        self.update(given_args)

    def update(self, given_args):
        def _get(key):
            try:
                # django request.GET args. Only consider non-empty values.
                return ",".join([v for v in given_args.getlist(key) if v])
            except BaseException:
                # dict arg (like from get_default_args)
                return given_args[key]

        for argument in self.args.values():
            if argument.identity in given_args:
                argument.set(_get(argument.identity))

        self._update()

    def _update(self):
        pass

    def run(self):
        # Sanity checks
        for argument in self.args.values():
            if argument.raw_value is None:
                raise Exception("Missing required argument '{a}'".format(a=argument.identity))

        # Run
        self._run()

    def _run(self):
        raise Exception("No _run() function defined for: {}".format(self.COMMAND))

    def __getstate__(self):
        """This is a workaround so objects of this class can be pickled.

        .. note:: This must be removed eventually. RoadMap: 1.11: API: Fix up result handling, and use json instead of pickle/python to interchange computable data.
        """
        pstate = copy.copy(self.__dict__)
        del pstate["msglog"]
        del pstate["request"]
        del pstate["args"]
        del pstate["daemon"]
        return pstate

    def __str__(self):
        return self._plain_result

    @classmethod
    def docstring(cls):
        auth_strings = {cls.NONE: "anonymous",
                        cls.LOGIN: "any user login",
                        cls.STAFF: "staff user login",
                        cls.ADMIN: "super user login"}
        return "{doc}\n\n[auth level {auth_level}: {auth_string}]".format(doc=cls.__doc__, auth_level=cls.AUTH, auth_string=auth_strings[cls.AUTH])

    def has_flag(self, flag):
        return self.args.get(flag, "False") == "True"

    @classmethod
    def auth_err(cls, user):
        """Check if django user is authorized to call command. Empty string
        means user is authorized.
        """
        def chk_login():
            return user.is_authenticated and user.is_active

        if user is None:
            return "API: '{c}': Internal Error: No user information available".format(c=cls.COMMAND)
        if (cls.AUTH == cls.LOGIN) and not chk_login():
            return "API: '{c}': Please login to run this command".format(c=cls.COMMAND)
        if (cls.AUTH == cls.STAFF) and not (chk_login() and user.is_staff):
            return "API: '{c}': Please login as 'staff' user to run this command".format(c=cls.COMMAND)
        if (cls.AUTH == cls.ADMIN) and not (chk_login() and user.is_superuser):
            return "API: '{c}': Please login as superuser to run this command".format(c=cls.COMMAND)
        return ""  # Auth OK


class DaemonCommand(Command):
    """Daemon commands"""

    def _upload_template_package(self, template_package, dist):
        """Used for keyringpackages and testpackages."""
        with contextlib.closing(template_package) as package:
            dsc_url = "file://" + package.dsc  # pylint: disable=no-member; see https://github.com/PyCQA/pylint/issues/1437
            info = "Port for {d}: {p}".format(d=dist, p=os.path.basename(dsc_url))
            try:
                self.msglog.info("Requesting: {i}".format(i=info))
                return self.daemon.portext(dsc_url, dist)
            except BaseException as e:
                mini_buildd.setup.log_exception(self.msglog, "FAILED: {i}".format(i=info), e)


class Status(DaemonCommand):
    """Show the status of the mini-buildd instance."""

    COMMAND = "status"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.version = "-1"
        self.http = ""
        self.ftp = ""
        self.running = False
        self.load = 0.0
        self.chroots = {}
        self.repositories = {}
        self.remotes = {}
        self.packaging = []
        self.building = []

    def _run(self):
        # version string
        self.version = mini_buildd.__version__

        # hopo string
        self.http = self.daemon.model.mbd_get_http_endpoint().hopo()

        # hopo string
        self.ftp = self.daemon.model.mbd_get_ftp_endpoint().hopo()

        # bool
        self.running = self.daemon.is_running()

        # float value: 0 =< load <= 1+
        self.load = self.daemon.build_queue.load

        # chroots: {"squeeze": ["i386", "amd64"], "wheezy": ["amd64"]}
        for c in self.daemon.get_active_chroots():
            self.chroots.setdefault(c.source.codename, [])
            self.chroots[c.source.codename].append(c.architecture.name)

        # repositories: {"repo1": ["sid", "wheezy"], "repo2": ["squeeze"]}
        for r in self.daemon.get_active_repositories():
            self.repositories[r.identity] = [d.base_source.codename for d in r.distributions.all()]

        # remotes: ["host1.xyz.org:8066", "host2.xyz.org:8066"]
        self.remotes = [r.http for r in self.daemon.get_active_or_auto_reactivate_remotes()]

        # packaging/building: string/unicode
        self.packaging = ["{0}".format(p) for p in list(self.daemon.packages.values())]
        self.building = ["{0}".format(b) for b in list(self.daemon.builds.values())]

        self._plain_result = """\
http://{h} ({v}):

Daemon: {ds}: ftp://{f} (load {l})

Repositories: {r}
Chroots     : {c}
Remotes     : {rm}

Packager: {p_len} packaging
{p}
Builder: {b_len} building
{b}""".format(h=self.http,
              v=self.version,
              ds="UP" if self.running else "DOWN",
              f=self.ftp,
              l=self.load,
              r=self.repositories_str(),
              c=self.chroots_str(),
              rm=", ".join(self.remotes),
              p_len=len(self.packaging),
              p="\n".join(self.packaging) + "\n" if self.packaging else "",
              b_len=len(self.building),
              b="\n".join(self.building) + "\n" if self.building else "")

    def repositories_str(self):
        return ", ".join(["{i}: {c}".format(i=identity, c=" ".join(codenames)) for identity, codenames in list(self.repositories.items())])

    def chroots_str(self):
        return ", ".join(["{a}: {c}".format(a=arch, c=" ".join(codenames)) for arch, codenames in list(self.chroots.items())])

    def has_chroot(self, codename, arch):
        return codename in self.chroots and arch in self.chroots[codename]

    def __test_msglog(self):
        self.msglog.debug("DEBUG USER MESSAGE")
        self.msglog.info("INFO USER MESSAGE")
        self.msglog.warning("WARN USER MESSAGE")
        self.msglog.error("ERROR USER MESSAGE")
        self.msglog.critical("CRITICAL USER MESSAGE")


class Start(DaemonCommand):
    """Start the Daemon (engine)."""
    COMMAND = "start"
    AUTH = Command.ADMIN
    ARGUMENTS = [BoolArgument(["--force-check", "-C"], default=False, doc="run checks on instances even if already checked.")]

    def _run(self):
        if not self.daemon.start(force_check=self.has_flag("force_check"), msglog=self.msglog):
            raise Exception("Could not start Daemon (check logs and messages).")
        self._plain_result = "{d}\n".format(d=self.daemon)


class Stop(DaemonCommand):
    """Stop the Daemon (engine)."""
    COMMAND = "stop"
    AUTH = Command.ADMIN
    ARGUMENTS = []

    def _run(self):
        if not self.daemon.stop(msglog=self.msglog):
            raise Exception("Could not stop Daemon (check logs and messages).")
        self._plain_result = "{d}\n".format(d=self.daemon)


class PrintUploaders(DaemonCommand):
    """Print all GPG ids allowed to upload to repositories."""
    COMMAND = "printuploaders"
    AUTH = Command.ADMIN
    NEEDS_RUNNING_DAEMON = True
    ARGUMENTS = [SelectArgument(["--repository", "-R"], default=".*", doc="repository name regex.")]

    def _update(self):
        if self.daemon:
            self.args["repository"].choices = [r.identity for r in self.daemon.get_active_repositories()]

    def _uploader_lines(self):
        for r in self.daemon.get_active_repositories().filter(identity__regex=r"^{r}$".format(r=self.args["repository"].value)):
            yield "Uploader keys for repository '{r}':".format(r=r.identity)
            if r.allow_unauthenticated_uploads:
                yield " WARNING: Unauthenticated uploads allowed anyway"
            for u in self.daemon.keyrings.get_uploaders()[r.identity].get_pub_colons():
                yield " {u}".format(u=u)

    def _run(self):
        self._plain_result = "\n".join(self._uploader_lines()) + "\n"


class Meta(DaemonCommand):
    """Call arbitrary meta functions for models; usually for internal use only."""
    COMMAND = "meta"
    AUTH = Command.ADMIN
    ARGUMENTS = [StringArgument(["model"], doc="Model path, for example 'source.Archive'"),
                 StringArgument(["function"], doc="Meta function to call, for example 'add_local'")]

    def _run(self):
        self.daemon.meta(self.args["model"].value, self.args["function"].value, msglog=self.msglog)


class AutoSetup(DaemonCommand):
    """Auto setup / bootstrap."""
    COMMAND = "autosetup"
    AUTH = Command.ADMIN
    CONFIRM = True
    ARGUMENTS = [
        MultiSelectArgument(["--vendors", "-V"], default="debian", choices=["debian", "ubuntu"], doc="comma-separated list of vendors to auto-setup for."),
        MultiSelectArgument(["--repositories", "-R"], default="test", choices=["test", "debdev"], doc="comma-separated list of repositories to auto-setup for."),
        SelectArgument(["--chroot-backend", "-C"], default="Dir", choices=["Dir", "File", "LVM", "LoopLVM", "BtrfsSnapshot"], doc="chroot backend to use, or empty string to not create chroots.")
    ]

    def _run(self):
        self.daemon.stop()

        # Daemon
        self.daemon.meta("daemon.Daemon", "pca_all", msglog=self.msglog)

        # Sources
        self.daemon.meta("source.Archive", "add_local", msglog=self.msglog)
        for v in self.args["vendors"].value:
            self.daemon.meta("source.Archive", "add_{}".format(v), msglog=self.msglog)
            self.daemon.meta("source.Source", "add_{}".format(v), msglog=self.msglog)
        self.daemon.meta("source.PrioritySource", "add_extras", msglog=self.msglog)
        self.daemon.meta("source.Source", "pca_all", msglog=self.msglog)

        # Repositories
        self.daemon.meta("repository.Layout", "create_defaults", msglog=self.msglog)
        self.daemon.meta("repository.Distribution", "add_base_sources", msglog=self.msglog)
        for r in self.args["repositories"].value:
            self.daemon.meta("repository.Repository", "add_{}".format(r), msglog=self.msglog)
        self.daemon.meta("repository.Repository", "pca_all", msglog=self.msglog)

        # Chroots
        if self.args["chroot_backend"].value:
            cb_class = "chroot.{}Chroot".format(self.args["chroot_backend"].value)
            self.daemon.meta(cb_class, "add_base_sources", msglog=self.msglog)
            self.daemon.meta(cb_class, "pca_all", msglog=self.msglog)

        self.daemon.start()


class KeyringPackages(DaemonCommand):
    """Build keyring packages for all active repositories."""
    COMMAND = "keyringpackages"
    AUTH = Command.ADMIN
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        MultiSelectArgument(["--distributions", "-D"], doc="comma-separated list of distributions to act on (defaults to all 'build_keyring_package distributions')."),
        BoolArgument(["--no-migration", "-N"], default=False, doc="don't migrate packages."),
    ]

    def _update(self):
        if self.daemon:
            # Possible choices
            self.args["distributions"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["distributions"].choices += r.mbd_distribution_strings(build_keyring_package=True)

            # Reasonable default
            if not self.args["distributions"].given:
                self.args["distributions"].set(self.args["distributions"].choices)

    def _run(self):
        uploaded = set()
        for d in self.args["distributions"].value:
            repository, distribution, suite, _rollback = self.daemon.parse_distribution(d)
            if not suite.build_keyring_package:
                raise Exception("Port failed: Keyring package to non-keyring suite requested (see 'build_keyring_package' flag): '{d}'".format(d=d))

            uploaded.add(self._upload_template_package(mini_buildd.daemon.KeyringPackage(self.daemon.model), d))

        built = set()
        for dist, package, version in uploaded:
            pkg_info = "{}-{} in {}...".format(package, version, dist)

            tries, max_tries, sleep = 0, 50, 15
            while tries <= max_tries:
                if repository.mbd_package_find(package, dist, version):
                    built.add((dist, package, version))
                    break
                self.msglog.info("Waiting for {} ({}/{})".format(pkg_info, tries, max_tries))
                tries += 1
                time.sleep(sleep)

        if uploaded != built:
            self.msglog.warning("Timed out waiting for these packages (skipping migrate): {}".format(uploaded - built))

        if not self.args["no_migration"].value:
            for dist, package, version in built:
                repository, distribution, suite, _rollback = self.daemon.parse_distribution(dist)
                self.msglog.info("Migrating {}...".format(pkg_info))
                repository.mbd_package_migrate(package, distribution, suite, full=True, version=version, msglog=self.msglog)


class TestPackages(DaemonCommand):
    """Build internal test packages.

    Per default, we build all test packages for all active
    distributions ending on 'experimental'.

    """
    COMMAND = "testpackages"
    AUTH = Command.ADMIN
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        MultiSelectArgument(["--packages", "-P"],
                            default="mbd-test-archall,mbd-test-cpp,mbd-test-ftbfs",
                            choices=["mbd-test-archall", "mbd-test-cpp", "mbd-test-ftbfs"],
                            doc="what test packages to use."),
        MultiSelectArgument(["--distributions", "-D"], doc="comma-separated list of distributions to upload to (defaults to all distributions ending in 'experimental')."),
    ]

    def _update(self):
        if self.daemon:
            # Possible choices
            self.args["distributions"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["distributions"].choices += r.mbd_distribution_strings(uploadable=True)

            # Reasonable default
            # Default layout has two (snapshot and experimental) suites flagged as experimental.
            # So we go here for the string "experimental" (not the flag) to avoid double testing in the standard case.
            if not self.args["distributions"].given:
                self.args["distributions"].set([d for d in self.args["distributions"].choices if d.endswith("experimental")])

    def _run(self):
        for d in self.args["distributions"].value:
            for p in self.args["packages"].value:
                self._upload_template_package(mini_buildd.daemon.TestPackage(p), d)


class ConfigCommand(Command):
    """Configuration convenience commands"""


class GetKey(ConfigCommand):
    """Get GnuPG public key."""
    COMMAND = "getkey"

    def _run(self):
        self._plain_result = self.daemon.model.mbd_get_pub_key()


class GetDputConf(ConfigCommand):
    """Get recommended dput config snippet.

    Usually, this is for integration in your personal ~/.dput.cf.
    """
    COMMAND = "getdputconf"

    def _run(self):
        self._plain_result = self.daemon.model.mbd_get_dput_conf()


class GetSourcesList(ConfigCommand):
    """Get sources.list (apt lines).

    Usually, this output is put to a file like '/etc/sources.list.d/mini-buildd-xyz.list'.
    """
    COMMAND = "getsourceslist"
    ARGUMENTS = [
        SelectArgument(["codename"], doc="codename (base distribution) to get apt lines for"),
        SelectArgument(["--repository", "-R"], default=".*", doc="repository name regex."),
        SelectArgument(["--suite", "-S"], default=".*", doc="suite name regex."),
        BoolArgument(["--with-deb-src", "-s"], default=False, doc="also list deb-src apt lines."),
        BoolArgument(["--with-extra-sources", "-x"], default=False, doc="also list extra sources needed.")
    ]

    def _update(self):
        if self.daemon:
            self.args["codename"].choices = self.daemon.get_active_codenames()
            self.args["repository"].choices = [r.identity for r in self.daemon.get_active_repositories()]
            self.args["suite"].choices = [s.name for s in self.daemon.get_suites()]

    def _run(self):
        self._plain_result = self.daemon.mbd_get_sources_list(self.args["codename"].value,
                                                              self.args["repository"].value,
                                                              self.args["suite"].value,
                                                              ["deb ", "deb-src "] if self.args["with_deb_src"].value else ["deb "],
                                                              self.args["with_extra_sources"].value)


class LogCat(ConfigCommand):
    """Cat last n lines of the mini-buildd's log."""

    COMMAND = "logcat"
    AUTH = Command.STAFF
    ARGUMENTS = [
        IntArgument(["--lines", "-n"], default=500, doc="cat (approx.) the last N lines")
    ]

    def _run(self):
        self._plain_result = self.daemon.logcat(lines=self.args["lines"].value)


def _get_table_format(dct, cols):
    tlen = {}
    for _r, values in list(dict(dct).items()):
        for value in values:
            for k, v in cols:
                if k in tlen:
                    tlen[k] = max(tlen[k], len(value[k]))
                else:
                    tlen[k] = max(len(v), len(value[k]))

    fmt = " | ".join(["{{{k}:{l}}}".format(k=k, l=tlen[k]) for k, v in cols])
    hdr = fmt.format(**dict(cols))
    fmt_tle = "{{t:^{l}}}".format(l=len(hdr))
    sep0 = "{{r:=^{l}}}".format(l=len(hdr)).format(r="")
    sep1 = "{{r:-^{l}}}".format(l=len(hdr)).format(r="")

    return (fmt, hdr, fmt_tle, sep0, sep1)


class PackageCommand(Command):
    """Package management commands"""


class List(PackageCommand):
    """List packages matching a shell-like glob pattern; matches both source and binary package names."""

    COMMAND = "list"
    AUTH = Command.LOGIN
    ARGUMENTS = [
        SelectArgument(["pattern"], doc="limit packages by name (glob pattern)"),
        BoolArgument(["--with-rollbacks", "-r"], default=False, doc="also list packages on rollback distributions"),
        SelectArgument(["--distribution", "-D"], default="", doc="limit distributions by name (regex)"),
        SelectArgument(["--type", "-T"], default="", choices=["dsc", "deb", "udeb"], doc="package type: dsc, deb or udeb (like reprepo --type)")
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repositories = {}

    def _update(self):
        if self.daemon:
            self.args["distribution"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["distribution"].choices += r.mbd_distribution_strings()
            self.args["pattern"].choices = self.daemon.get_last_packages()

    def _run(self):
        # Save all results of all repos in a top-level dict (don't add repos with empty results).
        for r in self.daemon.get_active_repositories():
            r_result = r.mbd_package_list(self.args["pattern"].value,
                                          typ=self.args["type"].false2none(),
                                          with_rollbacks=self.args["with_rollbacks"].value,
                                          dist_regex=self.args["distribution"].value)
            if r_result:
                self.repositories[r.identity] = r_result

    def __str__(self):
        if not self.repositories:
            return "No packages found."

        fmt, hdr, fmt_tle, sep0, sep1 = _get_table_format(self.repositories,
                                                          [("package", "Package"),
                                                           ("type", "Type"),
                                                           ("architecture", "Architecture"),
                                                           ("distribution", "Distribution"),
                                                           ("component", "Component"),
                                                           ("version", "Version"),
                                                           ("source", "Source")])

        def p_table(repository, values):
            return """\
{s0}
{t}
{s0}
{h}
{s1}
{p}
""".format(t=fmt_tle.format(t=" Repository '{r}' ".format(r=repository)),
           h=hdr,
           s0=sep0,
           s1=sep1,
           p="\n".join([fmt.format(**p) for p in values]))

        return "\n".join([p_table(k, v) for k, v in list(self.repositories.items())])


class Show(PackageCommand):
    """Show a source package."""

    COMMAND = "show"
    ARGUMENTS = [
        SelectArgument(["package"], doc="source package name"),
        BoolArgument(["--verbose", "-v"], default=False, doc="verbose output")
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # List of tuples: (repository, result)
        self.repositories = []

    def _update(self):
        if self.daemon:
            self.args["package"].choices = self.daemon.get_last_packages()

    def _run(self):
        # Save all results of all repos in a top-level dict (don't add repos with empty results).
        for r in self.daemon.get_active_repositories():
            r_result = r.mbd_package_show(self.args["package"].value)
            if r_result:
                self.repositories.append((r, r_result))

    def __str__(self):
        if not self.repositories:
            return "No package found."

        def p_codename(codename, values):
            return """\
{s0}
{t}
{s0}
{h}
{s1}
{p}""".format(t=fmt_tle.format(t=" Basedist '{c}' ".format(c=codename)),
              h=hdr,
              s0=sep0,
              s1=sep1,
              p="\n".join([fmt.format(**p) for p in values]))

        rows = [("distribution", "Distribution"),
                ("sourceversion", "Version"),
                ("migrates_to", "Migrates to")]
        if self.has_flag("verbose"):
            rows.append(("dsc_path", "Source package path"))
            rows.append(("rollbacks_str_verbose", "Rollbacks"))
        else:
            rows.append(("rollbacks_str", "Rollbacks"))

        results = []
        for repository, codenames in self.repositories:
            # Add rollback_str
            for _k, v in codenames:
                for d in v:
                    d["rollbacks_str"] = "{n}/{m}".format(n=len(d["rollbacks"]), m=d["rollback"])
                    d["rollbacks_str_verbose"] = d["rollbacks_str"] + \
                        ": " + " ".join(["{n}:{v}".format(n=r["no"], v=r["sourceversion"]) for r in d["rollbacks"]])

            fmt, hdr, fmt_tle, sep0, sep1 = _get_table_format(codenames, rows)
            results.append("{s}\n{t}\n".format(s=sep0, t=fmt_tle.format(t="Repository '{r}'".format(r=repository)))
                           + "\n".join([p_codename(k, v) for k, v in codenames]) + "\n")
        return "\n".join(results)


class Migrate(PackageCommand):
    """Migrate a source package (along with all binary packages)."""

    COMMAND = "migrate"
    AUTH = Command.STAFF
    CONFIRM = True
    ARGUMENTS = [
        SelectArgument(["package"], doc="source package name"),
        SelectArgument(["distribution"], doc="distribution to migrate from (if this is a '-rollbackN' distribution, this will perform a rollback restore)"),
        BoolArgument(["--full", "-F"], default=False, doc="migrate all 'migrates_to' suites up (f.e. unstable->testing->stable)."),
        Command.COMMON_ARG_VERSION
    ]

    def _update(self):
        if self.daemon:
            self.args["package"].choices = self.daemon.get_last_packages()
            self.args["distribution"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["distribution"].choices += r.mbd_distribution_strings(migrates_to__isnull=False)

    def _run(self):
        repository, distribution, suite, rollback = self.daemon.parse_distribution(self.args["distribution"].value)
        self._plain_result = repository.mbd_package_migrate(self.args["package"].value,
                                                            distribution,
                                                            suite,
                                                            full=self.args["full"].value,
                                                            rollback=rollback,
                                                            version=self.args["version"].false2none(),
                                                            msglog=self.msglog)


class Remove(PackageCommand):
    """Remove a source package (along with all binary packages)."""

    COMMAND = "remove"
    AUTH = Command.ADMIN
    CONFIRM = True
    ARGUMENTS = [
        SelectArgument(["package"], doc="source package name"),
        SelectArgument(["distribution"], doc="distribution to remove from"),
        Command.COMMON_ARG_VERSION
    ]

    def _update(self):
        if self.daemon:
            self.args["package"].choices = self.daemon.get_last_packages()
            self.args["distribution"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["distribution"].choices += r.mbd_distribution_strings()

    def _run(self):
        repository, distribution, suite, rollback = self.daemon.parse_distribution(self.args["distribution"].value)
        self._plain_result = repository.mbd_package_remove(self.args["package"].value,
                                                           distribution,
                                                           suite,
                                                           rollback=rollback,
                                                           version=self.args["version"].false2none(),
                                                           msglog=self.msglog)


class Port(PackageCommand):
    """Port an internal package.

    An internal 'port' is a no-changes (i.e., only the changelog
    will be adapted) rebuild of the given locally-installed
    package.
    """
    COMMAND = "port"
    AUTH = Command.STAFF
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        SelectArgument(["package"], doc="source package name"),
        SelectArgument(["from_distribution"], doc="distribution to port from"),
        MultiSelectArgument(["to_distributions"], doc="comma-separated list of distributions to port to (when this equals the from-distribution, a rebuild will be done)"),
        Command.COMMON_ARG_VERSION,
        Command.COMMON_ARG_OPTIONS]

    def _update(self):
        if self.daemon:
            self.args["package"].choices = self.daemon.get_last_packages()
            self.args["from_distribution"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["from_distribution"].choices += r.mbd_distribution_strings()
            if self.args["from_distribution"].value:
                repository, _distribution, suite, _rollback_no = self.daemon.parse_distribution(self.args["from_distribution"].value)
                self.args["to_distributions"].choices = repository.mbd_distribution_strings(uploadable=True, experimental=suite.experimental)
            else:
                for r in self.daemon.get_active_repositories():
                    self.args["to_distributions"].choices += r.mbd_distribution_strings(uploadable=True)

    def _run(self):
        # Parse and pre-check all dists
        for to_distribution in self.args["to_distributions"].value:
            info = "Port {p}/{d} -> {to_d}".format(p=self.args["package"].value, d=self.args["from_distribution"].value, to_d=to_distribution)
            self.msglog.info("Trying: {i}".format(i=info))
            self.daemon.port(self.args["package"].value,
                             self.args["from_distribution"].value,
                             to_distribution,
                             version=self.args["version"].false2none(),
                             options=self.args["options"].value.split("|"))
            self.msglog.info("Requested: {i}".format(i=info))
            self._plain_result += to_distribution + " "


class PortExt(PackageCommand):
    """Port an external package.

    An external 'port' is a no-changes (i.e., only the changelog
    will be adapted) rebuild of any given source package.
    """
    COMMAND = "portext"
    AUTH = Command.STAFF
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        URLArgument(["dsc"], doc="URL of any Debian source package (dsc) to port"),
        MultiSelectArgument(["distributions"], doc="comma-separated list of distributions to port to"),
        Command.COMMON_ARG_OPTIONS
    ]

    def _update(self):
        if self.daemon:
            self.args["distributions"].choices = []
            for r in self.daemon.get_active_repositories():
                self.args["distributions"].choices += r.mbd_distribution_strings(uploadable=True)

    def _run(self):
        # Parse and pre-check all dists
        for d in self.args["distributions"].value:
            info = "External port {dsc} -> {d}".format(dsc=self.args["dsc"].value, d=d)
            self.msglog.info("Trying: {i}".format(i=info))
            self.daemon.portext(self.args["dsc"].value, d, options=self.args["options"].value.split("|"))
            self.msglog.info("Requested: {i}".format(i=info))
            self._plain_result += d + " "


class Retry(PackageCommand):
    """Retry a previously failed package."""

    COMMAND = "retry"
    AUTH = Command.STAFF
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        SelectArgument(["package"], doc="source package name"),
        SelectArgument(["version"], doc="source package's version"),
        SelectArgument(["--repository", "-R"], default="*", doc="Repository name -- use only in case of multiple matches.")
    ]

    def _update(self):
        if self.daemon:
            self.args["repository"].choices = [r.identity for r in self.daemon.get_active_repositories()]
            self.args["package"].choices = self.daemon.get_last_packages()
            if self.args["package"].value:
                self.args["version"].choices = self.daemon.get_last_versions(self.args["package"].value)

    def _run(self):
        pkg_log = mini_buildd.misc.PkgLog(self.args["repository"].value, False, self.args["package"].value, self.args["version"].value)
        if not pkg_log.changes:
            raise Exception("No matching changes found for your retry query.")
        self.daemon.incoming_queue.put(pkg_log.changes)
        self.msglog.info("Retrying: {c}".format(c=os.path.basename(pkg_log.changes)))

        self._plain_result = os.path.basename(os.path.basename(pkg_log.changes))


class UserCommand(Command):
    """User management commands"""


class SetUserKey(UserCommand):
    """Set a user's GnuPG public key."""

    COMMAND = "setuserkey"
    AUTH = Command.LOGIN
    CONFIRM = True
    ARGUMENTS = [
        TextArgument(["key"], doc="GnuPG public key; multiline inputs will be handled as ascii armored full key, one-liners as key ids")
    ]

    def _run(self):
        uploader = self.request.user.uploader
        uploader.Admin.mbd_remove(self.request, uploader)
        key = self.args["key"].value

        if "\n" in key:
            self.msglog.info("Using given key argument as full ascii-armored GPG key")
            uploader.key_id = ""
            uploader.key = key
        else:
            self.msglog.info("Using given key argument as key ID")
            uploader.key_id = key
            uploader.key = ""

        uploader.Admin.mbd_prepare(self.request, uploader)
        uploader.Admin.mbd_check(self.request, uploader)
        self.msglog.info("Uploader profile changed: {u}".format(u=uploader))
        self.msglog.warning("Your uploader profile must be (re-)activated by the mini-buildd staff before you can actually use it.")


class Subscription(UserCommand):
    """Manage subscriptions to package notifications.

    A package subscription is a tuple 'PACKAGE:DISTRIBUTION',
    where both PACKAGE or DISTRIBUTION may be empty to denote
    all resp. items.
    """
    COMMAND = "subscription"
    AUTH = Command.LOGIN
    ARGUMENTS = [
        SelectArgument(["action"], doc="action to run", choices=["list", "add", "remove"]),
        SelectArgument(["subscription"], doc="subscription pattern")
    ]

    def _update(self):
        if self.daemon and self.args["subscription"].value:
            self.args["subscription"].choices = [self.args["subscription"].value]
            package, _sep, _distribution = self.args["subscription"].value.partition(":")
            for r in self.daemon.get_active_repositories():
                self.args["subscription"].choices += ["{}:{}".format(package, d) for d in r.mbd_distribution_strings()]

    def _run(self):
        package, _sep, distribution = self.args["subscription"].value.partition(":")

        def _filter():
            for s in self.daemon.get_subscription_objects().filter(subscriber=self.request.user):
                if package in ("", s.package) and distribution in ("", s.distribution):
                    yield s

        def _delete(subscription):
            result = "{s}".format(s=subscription)
            subscription.delete()
            return result

        if self.args["action"].value == "list":
            self._plain_result = "\n".join(["{s}.".format(s=subscription) for subscription in _filter()])

        elif self.args["action"].value == "add":
            subscription, created = self.daemon.get_subscription_objects().get_or_create(subscriber=self.request.user,
                                                                                         package=package,
                                                                                         distribution=distribution)
            self._plain_result = "{a}: {s}.".format(a="Added" if created else "Exists", s=subscription)

        elif self.args["action"].value == "remove":
            self._plain_result = "\n".join(["Removed: {s}.".format(s=_delete(subscription)) for subscription in _filter()])

        else:
            raise Exception("Unknown action '{c}': Use one of 'list', 'add' or 'remove'.".format(c=self.args["action"].value))

        # For convenience, say something if nothing matched
        if not self._plain_result:
            self._plain_result = "No matching subscriptions ({s}).".format(s=self.args["subscription"].value)


# COMMANDS: (Ordered) list of tuples: [(<name>, <class>)] (<name>=COMMAND_GROUP denotes the start of a group)
COMMAND_GROUP = "__GROUP__"
_PREVIOUS_GROUP = None
COMMANDS = []
for _C in [c for c in sys.modules[__name__].__dict__.values() if inspect.isclass(c) and issubclass(c, Command) and c.COMMAND is not None]:
    _GROUP = inspect.getmro(_C)[1]
    if _GROUP is not _PREVIOUS_GROUP:
        COMMANDS.append((COMMAND_GROUP, inspect.getdoc(_GROUP)))
        _PREVIOUS_GROUP = _GROUP
    COMMANDS.append((_C.COMMAND, _C))
COMMANDS_DICT = dict(COMMANDS)
COMMANDS_DEFAULTS = [(cmd, cls({}) if cmd != COMMAND_GROUP else cls) for cmd, cls in COMMANDS]
COMMANDS_DEFAULTS_DICT = dict(COMMANDS_DEFAULTS)
