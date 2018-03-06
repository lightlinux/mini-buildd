# -*- coding: utf-8 -*-

import os
import copy
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


class Argument(object):
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
        self.choices = choices
        if choices is not None:
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


class Command(object):
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
    COMMON_ARG_OPTIONS = MultiSelectArgument(["--options", "-O"], separator="|", default="ignore-lintian=true", doc="upload options (see user manual); separate multiple options by '|'")

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
        LOG.warning("No _update() function defined for: {}".format(self.COMMAND))

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
        "Log object cannot be pickled."
        pstate = copy.copy(self.__dict__)
        del pstate["msglog"]
        del pstate["request"]
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

        if (cls.AUTH == cls.LOGIN) and not chk_login():
            return "API: '{c}': Needs user login".format(c=cls.COMMAND)

        if (cls.AUTH == cls.STAFF) and not (chk_login() and user.is_staff):
            return "API: '{c}': Needs staff user login".format(c=cls.COMMAND)

        if (cls.AUTH == cls.ADMIN) and not (chk_login() and user.is_superuser):
            return "API: '{c}': Needs superuser login".format(c=cls.COMMAND)

        return ""


class Status(Command):
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
        self.http = self.daemon.model.mbd_get_http_hopo().string

        # hopo string
        self.ftp = self.daemon.model.mbd_get_ftp_hopo().string

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


class Start(Command):
    """Start the Daemon (engine)."""
    COMMAND = "start"
    AUTH = Command.ADMIN
    ARGUMENTS = [BoolArgument(["--force-check", "-C"], default=False, doc="run checks on instances even if already checked.")]

    def _run(self):
        if not self.daemon.start(force_check=self.has_flag("force_check"), msglog=self.msglog):
            raise Exception("Could not start Daemon (check logs and messages).")
        self._plain_result = "{d}\n".format(d=self.daemon)


class Stop(Command):
    """Stop the Daemon (engine)."""
    COMMAND = "stop"
    AUTH = Command.ADMIN
    ARGUMENTS = []

    def _run(self):
        if not self.daemon.stop(msglog=self.msglog):
            raise Exception("Could not stop Daemon (check logs and messages).")
        self._plain_result = "{d}\n".format(d=self.daemon)


class PrintUploaders(Command):
    """Print all GPG ids allowed to upload to repositories."""
    COMMAND = "printuploaders"
    AUTH = Command.ADMIN
    NEEDS_RUNNING_DAEMON = True
    ARGUMENTS = [StringArgument(["--repository", "-R"], default=".*", doc="repository name regex.")]

    def _uploader_lines(self):
        for r in self.daemon.get_active_repositories().filter(identity__regex=r"^{r}$".format(r=self.args["repository"].value)):
            yield "Uploader keys for repository '{r}':".format(r=r.identity)
            if r.allow_unauthenticated_uploads:
                yield " WARNING: Unauthenticated uploads allowed anyway"
            for u in self.daemon.keyrings.get_uploaders()[r.identity].get_pub_colons():
                yield " {u}".format(u=u)

    def _run(self):
        self._plain_result = "\n".join(self._uploader_lines()) + "\n"


class Meta(Command):
    """Call arbitrary meta functions for models; usually for internal use only."""
    COMMAND = "meta"
    AUTH = Command.ADMIN
    ARGUMENTS = [StringArgument(["model"], doc="Model path, for example 'source.Archive'"),
                 StringArgument(["function"], doc="Meta function to call, for example 'add_from_sources_list'")]

    def _run(self):
        self.daemon.meta(self.args["model"].value, self.args["function"].value, msglog=self.msglog)


class AutoSetup(Command):
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
        self.daemon.meta("source.Archive", "add_from_sources_list", msglog=self.msglog)
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


class GetKey(Command):
    """Get GnuPG public key."""
    COMMAND = "getkey"

    def _run(self):
        self._plain_result = self.daemon.model.mbd_get_pub_key()


class GetDputConf(Command):
    """Get recommended dput config snippet.

    Usually, this is for integration in your personal ~/.dput.cf.
    """
    COMMAND = "getdputconf"

    def _run(self):
        self._plain_result = self.daemon.model.mbd_get_dput_conf()


class GetSourcesList(Command):
    """Get sources.list (apt lines).

    Usually, this output is put to a file like '/etc/sources.list.d/mini-buildd-xyz.list'.
    """
    COMMAND = "getsourceslist"
    ARGUMENTS = [
        StringArgument(["codename"], doc="codename (base distribution) to get apt lines for"),
        StringArgument(["--repository", "-R"], default=".*", doc="repository name regex."),
        StringArgument(["--suite", "-S"], default=".*", doc="suite name regex."),
        BoolArgument(["--with-deb-src", "-s"], default=False, doc="also list deb-src apt lines."),
        BoolArgument(["--with-extra-sources", "-x"], default=False, doc="also list extra sources needed.")
    ]

    def _run(self):
        self._plain_result = self.daemon.mbd_get_sources_list(self.args["codename"].value,
                                                              self.args["repository"].value,
                                                              self.args["suite"].value,
                                                              ["deb ", "deb-src "] if self.args["with_deb_src"].value else ["deb "],
                                                              self.args["with_extra_sources"].value)


class LogCat(Command):
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


class List(Command):
    """List packages matching a shell-like glob pattern; matches both source and binary package names."""

    COMMAND = "list"
    AUTH = Command.LOGIN
    ARGUMENTS = [
        StringArgument(["pattern"], doc="limit packages by name (glob pattern)"),
        BoolArgument(["--with-rollbacks", "-r"], default=False, doc="also list packages on rollback distributions"),
        StringArgument(["--distribution", "-D"], default="", doc="limit distributions by name (regex)"),
        StringArgument(["--type", "-T"], default="", doc="package type: dsc, deb or udeb (like reprepo --type)")
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repositories = {}

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


class Show(Command):
    """Show a source package."""

    COMMAND = "show"
    ARGUMENTS = [
        StringArgument(["package"], doc="source package name"),
        BoolArgument(["--verbose", "-v"], default=False, doc="verbose output")
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # List of tuples: (repository, result)
        self.repositories = []

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
            results.append("{s}\n{t}\n".format(s=sep0, t=fmt_tle.format(t="Repository '{r}'".format(r=repository))) +
                           "\n".join([p_codename(k, v) for k, v in codenames]) +
                           "\n")
        return "\n".join(results)


class Migrate(Command):
    """Migrate a source package (along with all binary packages)."""

    COMMAND = "migrate"
    AUTH = Command.STAFF
    CONFIRM = True
    ARGUMENTS = [
        StringArgument(["package"], doc="source package name"),
        StringArgument(["distribution"], doc="distribution to migrate from (if this is a '-rollbackN' distribution, this will perform a rollback restore)"),
        Command.COMMON_ARG_VERSION
    ]

    def _run(self):
        repository, distribution, suite, rollback = self.daemon.parse_distribution(self.args["distribution"].value)
        self._plain_result = repository.mbd_package_migrate(self.args["package"].value,
                                                            distribution,
                                                            suite,
                                                            rollback=rollback,
                                                            version=self.args["version"].false2none(),
                                                            msglog=self.msglog)


class Remove(Command):
    """Remove a source package (along with all binary packages)."""

    COMMAND = "remove"
    AUTH = Command.ADMIN
    CONFIRM = True
    ARGUMENTS = [
        StringArgument(["package"], doc="source package name"),
        StringArgument(["distribution"], doc="distribution to remove from"),
        Command.COMMON_ARG_VERSION
    ]

    def _run(self):
        repository, distribution, suite, rollback = self.daemon.parse_distribution(self.args["distribution"].value)
        self._plain_result = repository.mbd_package_remove(self.args["package"].value,
                                                           distribution,
                                                           suite,
                                                           rollback=rollback,
                                                           version=self.args["version"].false2none(),
                                                           msglog=self.msglog)


class Port(Command):
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
        StringArgument(["package"], doc="source package name"),
        StringArgument(["from_distribution"], doc="distribution to port from"),
        MultiSelectArgument(["to_distributions"], doc="comma-separated list of distributions to port to (when this equals the from-distribution, a rebuild will be done)"),
        Command.COMMON_ARG_VERSION,
        Command.COMMON_ARG_OPTIONS]

    def _update(self):
        if self.daemon and self.args["from_distribution"].value:
            repository, _distribution, suite, _rollback_no = self.daemon.parse_distribution(self.args["from_distribution"].value)
            self.args["to_distributions"].choices = repository.mbd_distribution_strings(uploadable=True, experimental=suite.experimental)

    def _run(self):
        # Parse and pre-check all dists
        for to_distribution in self.args["to_distributions"].value:
            info = "Port {p}/{d} -> {to_d}".format(p=self.args["package"].value, d=self.args["from_distribution"].value, to_d=to_distribution)
            self.msglog.info("Trying: {i}".format(i=info))
            self.daemon.port(self.args["package"].value,
                             self.args["from_distribution"].value,
                             to_distribution,
                             version=self.args["version"].false2none(),
                             options=self.args["options"].value)
            self.msglog.info("Requested: {i}".format(i=info))
            self._plain_result += to_distribution + " "


class PortExt(Command):
    """Port an external package.

    An external 'port' is a no-changes (i.e., only the changelog
    will be adapted) rebuild of any given source package.
    """
    COMMAND = "portext"
    AUTH = Command.STAFF
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        StringArgument(["dsc"], doc="URL of any Debian source package (dsc) to port"),
        MultiSelectArgument(["distributions"], doc="comma-separated list of distributions to port to"),
        Command.COMMON_ARG_OPTIONS
    ]

    def _run(self):
        # Parse and pre-check all dists
        for d in self.args["distributions"].value:
            info = "External port {dsc} -> {d}".format(dsc=self.args["dsc"].value, d=d)
            self.msglog.info("Trying: {i}".format(i=info))
            self.daemon.portext(self.args["dsc"].value, d, options=self.args["options"].value)
            self.msglog.info("Requested: {i}".format(i=info))
            self._plain_result += d + " "


class Retry(Command):
    """Retry a previously failed package."""

    COMMAND = "retry"
    AUTH = Command.STAFF
    NEEDS_RUNNING_DAEMON = True
    CONFIRM = True
    ARGUMENTS = [
        StringArgument(["package"], doc="source package name"),
        StringArgument(["version"], doc="source package's version"),
        StringArgument(["--repository", "-R"], default="*", doc="Repository name -- use only in case of multiple matches.")
    ]

    def _run(self):
        pkg_log = mini_buildd.misc.PkgLog(self.args["repository"].value, False, self.args["package"].value, self.args["version"].value)
        if not pkg_log.changes:
            raise Exception("No matching changes found for your retry query.")
        self.daemon.incoming_queue.put(pkg_log.changes)
        self.msglog.info("Retrying: {c}".format(c=os.path.basename(pkg_log.changes)))

        self._plain_result = os.path.basename(os.path.basename(pkg_log.changes))


class SetUserKey(Command):
    """Set a user's GnuPG public key."""

    COMMAND = "setuserkey"
    AUTH = Command.LOGIN
    CONFIRM = True
    ARGUMENTS = [
        StringArgument(["key"], doc="GnuPG public key; multiline inputs will be handled as ascii armored full key, one-liners as key ids")
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


class Subscription(Command):
    """Manage subscriptions to package notifications.

    A package subscription is a tuple 'PACKAGE:DISTRIBUTION',
    where both PACKAGE or DISTRIBUTION may be empty to denote
    all resp. items.
    """
    COMMAND = "subscription"
    AUTH = Command.LOGIN
    ARGUMENTS = [
        SelectArgument(["action"], doc="action to run", choices=["list", "add", "remove"]),
        StringArgument(["subscription"], doc="subscription pattern")
    ]

    def _run(self):
        package, _sep, distribution = self.args["subscription"].value.partition(":")

        def _filter():
            for s in self.daemon.get_subscription_objects().filter(subscriber=self.request.user):
                if (package == "" or s.package == package) and (distribution == "" or s.distribution == distribution):
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


COMMAND_GROUP = "__GROUP__"
COMMANDS = [(COMMAND_GROUP, "Daemon commands"),
            (Status.COMMAND, Status),
            (Start.COMMAND, Start),
            (Stop.COMMAND, Stop),
            (PrintUploaders.COMMAND, PrintUploaders),
            (Meta.COMMAND, Meta),
            (AutoSetup.COMMAND, AutoSetup),
            (COMMAND_GROUP, "Configuration convenience commands"),
            (GetKey.COMMAND, GetKey),
            (GetDputConf.COMMAND, GetDputConf),
            (GetSourcesList.COMMAND, GetSourcesList),
            (LogCat.COMMAND, LogCat),
            (COMMAND_GROUP, "Package management commands"),
            (List.COMMAND, List),
            (Show.COMMAND, Show),
            (Migrate.COMMAND, Migrate),
            (Remove.COMMAND, Remove),
            (Port.COMMAND, Port),
            (PortExt.COMMAND, PortExt),
            (Retry.COMMAND, Retry),
            (COMMAND_GROUP, "User management commands"),
            (SetUserKey.COMMAND, SetUserKey),
            (Subscription.COMMAND, Subscription),
           ]
COMMANDS_DICT = dict(COMMANDS)
COMMANDS_DEFAULTS = [(cmd, cls({}) if cmd != COMMAND_GROUP else cls) for cmd, cls in COMMANDS]
COMMANDS_DEFAULTS_DICT = dict(COMMANDS_DEFAULTS)
