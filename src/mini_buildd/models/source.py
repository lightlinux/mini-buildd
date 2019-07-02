import tempfile
import urllib.request
import urllib.parse
import urllib.error
import logging
import contextlib
import copy
import datetime
import socket

import dateutil.parser

import django.db.models
import django.contrib.admin
import django.contrib.messages
import django.utils.timezone

import debian.deb822

import mini_buildd.misc
import mini_buildd.net
import mini_buildd.call
import mini_buildd.gnupg

import mini_buildd.models.base
import mini_buildd.models.gnupg

from mini_buildd.models.msglog import MsgLog
LOG = logging.getLogger(__name__)


class Archive(mini_buildd.models.base.Model):
    url = django.db.models.URLField(primary_key=True, max_length=512,
                                    default="http://ftp.debian.org/debian/",
                                    help_text="""
The URL of an apt archive (there must be a 'dists/' infrastructure below).

Use the 'directory' notation with exactly one trailing slash (like 'http://example.org/path/').
""")
    ping = django.db.models.FloatField(default=-1.0, editable=False)

    class Meta(mini_buildd.models.base.Model.Meta):
        ordering = ["url"]

    class Admin(mini_buildd.models.base.Model.Admin):
        search_fields = ["url"]
        exclude = ("extra_options",)

        @classmethod
        def _mbd_get_or_create(cls, msglog, url):
            if url:
                Archive.mbd_get_or_create(msglog, url=url)

        @classmethod
        def mbd_meta_add_local(cls, msglog):
            """
            Local scan for archives.

            This currently scans the local sources list and tries to detect a local apt-cacher-ng.
            """
            try:
                import aptsources.sourceslist
                for src in aptsources.sourceslist.SourcesList():
                    # These URLs come from the user. 'normalize' the uri first to have exactly one trailing slash.
                    cls._mbd_get_or_create(msglog, src.uri.rstrip("/") + "/")
                    msglog.info("Archive added from local source: {}".format(src))

            except BaseException as e:
                mini_buildd.setup.log_exception(LOG,
                                                "Failed to scan local sources.lists for default mirrors ('python-apt' not installed?)",
                                                e,
                                                level=logging.WARN)

            url = mini_buildd.net.detect_apt_cacher_ng(url="http://{}:3142".format(socket.getfqdn()))
            if url:
                msglog.info("Local apt-cacher-ng detected: {}".format(url))
                for path in ["debian", "ubuntu", "debian-security", "debian-archive/debian", "debian-archive/debian-security", "debian-archive/debian-backports"]:
                    cls._mbd_get_or_create(msglog, "{}/{}/".format(url, path))

        @classmethod
        def mbd_meta_add_debian(cls, msglog):
            """Add internet Debian archive sources."""
            for url in ["http://ftp.debian.org/debian/",                 # Debian (release, updates, proposed-updates and backports)
                        "http://deb.debian.org/debian/",                 # alternate: CDN

                        "http://security.debian.org/debian-security/",   # Debian Security (release/updates)
                        "http://deb.debian.org/debian-security/",        # alternate: CDN

                        "http://archive.debian.org/debian/",             # Archived Debian Releases
                        "http://archive.debian.org/debian-security/",    # Archived Debian Security
                        "http://archive.debian.org/debian-backports/",   # Archived Debian Backports
                        ]:
                cls._mbd_get_or_create(msglog, url)
            msglog.info("Consider adding archives with your local or closest mirrors; check 'netselect-apt'.")

        @classmethod
        def mbd_meta_add_ubuntu(cls, msglog):
            """Add internet Ubuntu archive sources."""
            for url in ["http://archive.ubuntu.com/ubuntu/",              # Ubuntu releases
                        "http://security.ubuntu.com/ubuntu/",             # Ubuntu Security
                        "http://old-releases.ubuntu.com/ubuntu/",         # Older Ubuntu release
                        ]:
                cls._mbd_get_or_create(msglog, url)
            msglog.info("Consider replacing these archives with you closest mirror(s); check netselect-apt.")

    def __str__(self):
        return "{u} (ping {p} ms)".format(u=self.url, p=self.ping)

    # Note: pylint false-positive: https://github.com/PyCQA/pylint/issues/1553
    def clean(self, *args, **kwargs):  # pylint: disable=arguments-differ
        if self.url[-1] != "/" or self.url[-2] == "/":  # pylint: disable=unsubscriptable-object
            raise django.core.exceptions.ValidationError("The URL must have exactly one trailing slash (like 'http://example.org/path/').")
        super().clean(*args, **kwargs)

    def mbd_get_matching_release(self, request, source, gnupg):
        url = "{u}/dists/{d}/Release".format(u=self.url, d=source.codename)
        with tempfile.NamedTemporaryFile() as release_file:
            MsgLog(LOG, request).debug("Downloading '{u}' to '{t}'".format(u=url, t=release_file.name))
            try:
                release_file.write(mini_buildd.net.urlopen_ca_certificates(url).read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    MsgLog(LOG, request).debug("{a}: '404 Not Found' on '{u}'".format(a=self, u=url))
                    # Not for us
                    return None
                raise
            release_file.flush()
            release_file.seek(0)
            release = debian.deb822.Release(release_file)

            # Check release file fields
            if not source.mbd_is_matching_release(request, release):
                return None

            # Pre-Check 'Valid-Until'
            #
            # Some Release files contain an expire date via the
            # 'Valid-Until' tag. If such an expired archive is used,
            # builds will fail. Furthermore, it could be only the
            # selected archive not updating, while the source may be
            # perfectly fine from another archive.
            #
            # This pre-check avoids such a situation, or at least it
            # can be fixed by re-checking the source.
            try:
                valid_until = release["Valid-Until"]
                if dateutil.parser.parse(valid_until) < datetime.datetime.now(datetime.timezone.utc):
                    if source.mbd_get_extra_option("X-Check-Valid-Until", "yes").lower() in ("no", "false", "0"):
                        MsgLog(LOG, request).info("{u} expired, but source marked to ignore valid-until (Valid-Until='{v}').".format(u=url, v=valid_until))
                    else:
                        MsgLog(LOG, request).warning("{u} expired, maybe the archive has problems? (Valid-Until='{v}').".format(u=url, v=valid_until))
                        return None
            except KeyError:
                pass  # We can assume Release file has no "Valid-Until", and be quiet
            except BaseException as e:
                MsgLog(LOG, request).error("Ignoring error checking 'Valid-Until' on {u}: {e}".format(u=url, e=e))

            # Check signature
            with tempfile.NamedTemporaryFile() as signature:
                MsgLog(LOG, request).debug("Downloading '{u}.gpg' to '{t}'".format(u=url, t=signature.name))
                signature.write(mini_buildd.net.urlopen_ca_certificates(url + ".gpg").read())
                signature.flush()
                gnupg.verify(signature.name, release_file.name)

            # Ok, this is for this source
            return release

    def mbd_ping(self, request):
        """Ping and update the ping value."""
        try:
            t0 = django.utils.timezone.now()
            # Append dists to URL for ping check: Archive may be
            # just fine, but not allow to access to base URL
            # (like ourselves ;). Any archive _must_ have dists/ anyway.
            try:
                mini_buildd.net.urlopen_ca_certificates("{u}/dists/".format(u=self.url))
            except urllib.error.HTTPError as e:
                # Allow HTTP 4xx client errors through; these might be valid use cases like:
                # 404 Usage Information: apt-cacher-ng
                if not 400 <= e.code <= 499:
                    raise

            delta = django.utils.timezone.now() - t0
            self.ping = delta.total_seconds() * (10 ** 3)
            self.save()
            MsgLog(LOG, request).debug("{s}: Ping!".format(s=self))
        except Exception as e:
            self.ping = -1.0
            self.save()
            raise Exception("{s}: Does not ping: {e}".format(s=self, e=e))

    def mbd_get_reverse_dependencies(self):
        """Return all sources (and their deps) that use us."""
        result = [s for s in self.source_set.all()]
        for s in self.source_set.all():
            result += s.mbd_get_reverse_dependencies()
        return result


class Architecture(mini_buildd.models.base.Model):
    name = django.db.models.CharField(primary_key=True, max_length=50)

    def __str__(self):
        return self.name

    @classmethod
    def mbd_host_architecture(cls):
        return mini_buildd.call.Call(["dpkg", "--print-architecture"]).check().stdout.strip()

    @classmethod
    def mbd_supported_architectures(cls, arch=None):
        """Get all supported architectures (some archs also natively support other archs)."""
        arch = arch or cls.mbd_host_architecture()
        arch_map = {"amd64": ["i386"]}
        return [arch] + arch_map.get(arch, [])


class Component(mini_buildd.models.base.Model):
    name = django.db.models.CharField(primary_key=True, max_length=50)

    def __str__(self):
        return self.name


def component_key(component):
    """
    Get Debian components as string in a suitable order.

    I.e., 'main' should be first, the others in alphabetical order.

    Basically only needed for reprepro's (broken?) default
    component guessing, which uses the first given component in
    the configuration.
    """
    return "" if component.name == "main" else component.name  # Use empty string to force 'main' to order first.


class Source(mini_buildd.models.base.StatusModel):
    # Identity
    origin = django.db.models.CharField(max_length=60, default="Debian",
                                        help_text="The exact string of the 'Origin' field of the resp. Release file.")
    codename = django.db.models.CharField(max_length=60, default="sid",
                                          help_text="""\
<p>The <b>name of the directory below <code>dists/</code></b> in archives
this source refers to. This is also the 3rd part of an apt line.</p>

<p>With no extra options given, this source will be identified
comparing <code>Origin</code> and <code>Codename</code> with the values of the
<code>Release</code> file found.</p>

<p>Some sources need some more care via <em>Extra options</em> below:</p>

<b>Origin, Codename, Suite, Archive, Version, Label</b>:

<p>If needed, you may use these fields (same as in a Release file) to
further specify this source. These are also later used to pin the
source via apt.</p>

<p>For some sources, <code>Codename</code> (as we use it above) does not
match resp. value as given in the Release file. When <code>Codename</code>
is overridden in this manner, be sure to also add one further flag to
identify the source -- else apt pinning later would likely not be
unambiguous. Real world examples that need this extra handling are
<em>Debian Security</em>, and <em>Ubuntu Security and
Backports</em>:</p>

<pre>
Codename: bionic
Suite: bionic-backports
</pre>

<b>X-Check-Valid-Until</b>:

<p>Some sources have a <code>Valid-Until</code> field that is no longer
updated. If you really still want to use it anyway, use:</p>

<pre>
X-Check-Valid-Until: no
</pre>

<p>This will, 1st, ignore mini-buildd's own 'Valid-Until check' and
2nd, create apt lines for this source with the
<code>[check-valid-until=no]</code> option. I.e., at least from stretch
onwards, the check is disabled <em>per source</em>. For jessie or
worse (where this apt option does not work), a global workaround via
schroot is still in place.</p>

<b>X-Remove-From-Component</b>:

<p>Some (actually, we only know of <em>Debian Security</em>) sources
have weird <code>Components</code> that need to be fixed to work with
mini-buildd. For example, <em>Debian Security</em> needs:</p>

<pre>
Codename: stretch
Label: Debian-Security
X-Remove-From-Component: updates/
</pre>
""")

    # Apt Secure
    apt_keys = django.db.models.ManyToManyField(mini_buildd.models.gnupg.AptKey, blank=True,
                                                help_text="""\
Apt keys this source is signed with. Please add all keys the
resp. Release file is signed with (Run s.th. like
'gpg --verify Release.gpg Release'
manually run on a Debian system to be sure.
""")

    # Extra
    description = django.db.models.CharField(max_length=100, editable=False, blank=True, default="")
    codeversion = django.db.models.CharField(max_length=50, editable=False, blank=True, default="")
    codeversion_override = django.db.models.CharField(
        max_length=50, blank=True, default="",
        help_text="""
Save this as empty string to have the codeversion re-guessed on check, or
put your own override value here if the guessed string is broken. The
codeversion is only used for base sources.""")
    archives = django.db.models.ManyToManyField(Archive, blank=True)
    components = django.db.models.ManyToManyField(Component, blank=True)
    architectures = django.db.models.ManyToManyField(Architecture, blank=True)

    class Meta(mini_buildd.models.base.StatusModel.Meta):
        unique_together = ("origin", "codename")
        ordering = ["origin", "-codeversion", "codename"]

    class Admin(mini_buildd.models.base.StatusModel.Admin):
        list_display = mini_buildd.models.base.StatusModel.Admin.list_display + ["origin", "codeversion", "codename"]
        search_fields = ["origin", "codeversion", "codename"]
        ordering = ["origin", "-codeversion", "codename"]

        readonly_fields = ["codeversion", "archives", "components", "architectures", "description"]
        fieldsets = (
            ("Identity", {"fields": ("origin", "codename", "extra_options", "apt_keys")}),
            ("Extra", {"classes": ("collapse",), "fields": ("description", "codeversion", "codeversion_override", "archives", "components", "architectures")}),)
        filter_horizontal = ("apt_keys",)

        def get_readonly_fields(self, _request, obj=None):
            """Forbid to change identity on existing source (usually a bad idea; repos/chroots that refer to us may break)."""
            fields = copy.copy(self.readonly_fields)
            if obj:
                fields.append("origin")
                fields.append("codename")
            return fields

        @classmethod
        def _mbd_get_or_create(cls, msglog, origin, codename, keys, extra_options=""):
            try:
                obj, created = Source.mbd_get_or_create(msglog, origin=origin, codename=codename, extra_options=extra_options)
                if created:
                    for long_key_id in keys:
                        matching_keys = mini_buildd.models.gnupg.AptKey.mbd_filter_key(long_key_id)
                        if matching_keys:
                            apt_key = matching_keys[0]
                            msglog.debug("Already exists: {k}".format(k=apt_key))
                        else:
                            apt_key, _created = mini_buildd.models.gnupg.AptKey.mbd_get_or_create(msglog, key_id=long_key_id)
                        obj.apt_keys.add(apt_key)
                    obj.save()
            except BaseException as e:
                msglog.debug("Can't add {c} (most likely a non-default instance already exists): {e}".format(c=codename, e=e))

        @classmethod
        def mbd_meta_add_debian(cls, msglog):
            """
            Add well-known Debian sources.

            To display the key ids via apt-key in the format as used here::

              apt-key adv --list-public-keys --keyid-format=long

            """
            keys = {
                "archive_stretch": "E0B11894F66AEC98",           # Debian Archive Automatic Signing Key (9/stretch) <ftpmaster@debian.org>  (subkey 04EE7237B7D453EC)
                "release_stretch": "EF0F382A1A7B6500",           # Debian Stable Release Key (9/stretch) <debian-release@lists.debian.org>
                "archive_jessie": "7638D0442B90D010",            # Debian Archive Automatic Signing Key (8/jessie) <ftpmaster@debian.org>
                "release_jessie": "CBF8D6FD518E17E1",            # Jessie Stable Release Key <debian-release@lists.debian.org>
                "security_archive_jessie": "9D6D8F6BC857C906",   # Debian Security Archive Automatic Signing Key (8/jessie) <ftpmaster@debian.org>
                "security_archive_stretch": "EDA0D2388AE22BA9",  # Debian Security Archive Automatic Signing Key (9/stretch) <ftpmaster@debian.org>
                "archive_wheezy": "8B48AD6246925553",            # Debian Archive Automatic Signing Key (7.0/wheezy) <ftpmaster@debian.org>
                "release_wheezy": "6FB2A1C265FFB764",            # Wheezy Stable Release Key <debian-release@lists.debian.org>
            }

            cls._mbd_get_or_create(msglog, "Debian", "wheezy",
                                   [keys["archive_wheezy"], keys["release_wheezy"], keys["archive_jessie"]])
            cls._mbd_get_or_create(msglog, "Debian", "wheezy/updates",
                                   [keys["archive_wheezy"], keys["security_archive_jessie"]],
                                   extra_options="Codename: wheezy\nLabel: Debian-Security\nX-Remove-From-Component: updates/\nX-Check-Valid-Until: no")
            cls._mbd_get_or_create(msglog, "Debian Backports", "wheezy-backports",
                                   [keys["archive_wheezy"], keys["release_wheezy"], keys["archive_jessie"]])
            cls._mbd_get_or_create(msglog, "Debian Backports", "wheezy-backports-sloppy",
                                   [keys["archive_wheezy"], keys["archive_jessie"]])

            cls._mbd_get_or_create(msglog, "Debian", "jessie",
                                   [keys["archive_wheezy"], keys["release_jessie"], keys["archive_jessie"]])
            cls._mbd_get_or_create(msglog, "Debian", "jessie/updates",
                                   [keys["archive_wheezy"], keys["security_archive_jessie"]],
                                   extra_options="Codename: jessie\nLabel: Debian-Security\nX-Remove-From-Component: updates/")
            cls._mbd_get_or_create(msglog, "Debian Backports", "jessie-backports",
                                   [keys["archive_wheezy"], keys["archive_jessie"]],
                                   extra_options="X-Check-Valid-Until: no")
            cls._mbd_get_or_create(msglog, "Debian Backports", "jessie-backports-sloppy",
                                   [keys["archive_wheezy"], keys["archive_jessie"]],
                                   extra_options="X-Check-Valid-Until: no")

            cls._mbd_get_or_create(msglog, "Debian", "stretch",
                                   [keys["archive_wheezy"], keys["archive_jessie"], keys["release_jessie"], keys["release_stretch"]])
            cls._mbd_get_or_create(msglog, "Debian", "stretch/updates",
                                   [keys["archive_wheezy"], keys["security_archive_jessie"]],
                                   extra_options="Codename: stretch\nLabel: Debian-Security\nX-Remove-From-Component: updates/")
            cls._mbd_get_or_create(msglog, "Debian Backports", "stretch-backports",
                                   [keys["archive_wheezy"], keys["archive_jessie"]])

            cls._mbd_get_or_create(msglog, "Debian", "buster",
                                   [keys["archive_wheezy"], keys["archive_jessie"], keys["archive_stretch"]])
            cls._mbd_get_or_create(msglog, "Debian", "buster/updates",
                                   [keys["security_archive_jessie"], keys["security_archive_stretch"]],
                                   extra_options="Codename: buster\nLabel: Debian-Security\nX-Remove-From-Component: updates/")

            cls._mbd_get_or_create(msglog, "Debian", "sid",
                                   [keys["archive_wheezy"], keys["archive_jessie"], keys["archive_stretch"]])

        @classmethod
        def mbd_meta_add_ubuntu(cls, msglog):
            """Add well-known Ubuntu sources. Update hint: Keep latest two releases plus a couple of LTS releases."""
            keys = {
                "archive_current": "40976EAF437D05B5",    # Ubuntu Archive Automatic Signing Key <ftpmaster@ubuntu.com>
                "archive_2012": "3B4FE6ACC0B21F32",       # Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>
            }

            # trusty: 14.04 (LTS until 2019)
            cls._mbd_get_or_create(msglog, "Ubuntu", "trusty",
                                   [keys["archive_current"], keys["archive_2012"]])
            cls._mbd_get_or_create(msglog, "Ubuntu", "trusty-security",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: trusty\nSuite: trusty-security")
            cls._mbd_get_or_create(msglog, "Ubuntu", "trusty-backports",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: trusty\nSuite: trusty-backports")

            # xenial: 16.04 (LTS until 2021)
            cls._mbd_get_or_create(msglog, "Ubuntu", "xenial",
                                   [keys["archive_current"], keys["archive_2012"]])
            cls._mbd_get_or_create(msglog, "Ubuntu", "xenial-security",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: xenial\nSuite: xenial-security")
            cls._mbd_get_or_create(msglog, "Ubuntu", "xenial-backports",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: xenial\nSuite: xenial-backports")

            # bionic: 18.04 (LTS until 2023)
            cls._mbd_get_or_create(msglog, "Ubuntu", "bionic",
                                   [keys["archive_current"], keys["archive_2012"]])
            cls._mbd_get_or_create(msglog, "Ubuntu", "bionic-security",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: bionic\nSuite: bionic-security")
            cls._mbd_get_or_create(msglog, "Ubuntu", "bionic-backports",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: bionic\nSuite: bionic-backports")

            # cosmic: 18.10
            cls._mbd_get_or_create(msglog, "Ubuntu", "cosmic",
                                   [keys["archive_current"], keys["archive_2012"]])
            cls._mbd_get_or_create(msglog, "Ubuntu", "cosmic-security",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: cosmic\nSuite: cosmic-security")
            cls._mbd_get_or_create(msglog, "Ubuntu", "cosmic-backports",
                                   [keys["archive_current"], keys["archive_2012"]],
                                   "Codename: cosmic\nSuite: cosmic-backports")

        @classmethod
        def mbd_filter_active_base_sources(cls):
            """Filter active base sources; needed in chroot and distribution wizards."""
            return Source.objects.filter(status__gte=Source.STATUS_ACTIVE,
                                         origin__in=["Debian", "Ubuntu"],
                                         codename__regex=r"^[a-z]+$")

    def __str__(self):
        try:
            archive = self.mbd_get_archive().url
        except BaseException:
            archive = None
        return "{o} '{c}' from '{a}'".format(o=self.origin, c=self.codename, a=archive)

    def mbd_release_file_values(self):
        """Compute a dict of values a matching release file must have."""
        values = {k: v for k, v in self.mbd_get_extra_options().items() if not k.startswith("X-")}  # Keep "X-<header>" for special purposes. All other keys are like in a Release file.

        # Set Origin and Codename (may be overwritten) from fields
        values["Origin"] = self.origin
        if not values.get("Codename"):
            values["Codename"] = self.codename

        return values

    def mbd_is_matching_release(self, request, release):
        """Check that this release file matches us."""
        for key, value in list(self.mbd_release_file_values().items()):
            # Check identity: origin, codename
            MsgLog(LOG, request).debug("Checking '{k}: {v}'".format(k=key, v=value))
            if value != release[key]:
                MsgLog(LOG, request).debug("Release[{k}] field mismatch: '{rv}', expected '{v}'.".format(k=key, rv=release[key], v=value))
                return False
        return True

    def mbd_get_archive(self):
        """Get fastest archive."""
        oa_list = self.archives.all().filter(ping__gte=0.0).order_by("ping")
        if oa_list:
            return oa_list[0]
        raise Exception("{s}: No archive found. Please add appropriate archive and/or check network setup.".format(s=self))

    def mbd_get_apt_line_raw(self, components, prefix="deb "):
        return "{p}[{o}] {u} {d} {c}".format(
            p=prefix,
            o="check-valid-until={vu}".format(vu=self.mbd_get_extra_option("X-Check-Valid-Until", "yes")),
            u=self.mbd_get_archive().url,
            d=self.codename + ("" if components else "/"),
            c=" ".join(components))

    def mbd_get_apt_line(self, distribution, prefix="deb "):
        allowed_components = [c.name for c in distribution.components.all()]
        components = sorted([c for c in self.components.all() if c.name in allowed_components], key=component_key)
        return self.mbd_get_apt_line_raw([c.name for c in components], prefix=prefix)

    def mbd_get_apt_pin(self):
        """Apt 'pin line' (for use in a apt 'preference' file)."""
        # See man apt_preferences for the field/pin mapping
        supported_fields = {"Origin": "o", "Codename": "n", "Suite": "a", "Archive": "a", "Version": "v", "Label": "l"}
        pins = []
        for key, value in list(self.mbd_release_file_values().items()):
            k = supported_fields.get(key)
            if k:
                pins.append("{k}={v}".format(k=k, v=value))
        return "release " + ", ".join(pins)

    def mbd_prepare(self, request):
        if not self.apt_keys.all():
            raise Exception("{s}: Please add apt keys to this source.".format(s=self))
        if self.mbd_get_extra_option("Origin"):
            raise Exception("{s}: You may not override 'Origin', just use the origin field.".format(s=self))
        MsgLog(LOG, request).info("{s} with pin: {p}".format(s=self, p=self.mbd_get_apt_pin()))

    def mbd_sync(self, request):
        self._mbd_remove_and_prepare(request)

    def mbd_remove(self, _request):
        self.archives.set([])
        self.components.set([])
        self.architectures.set([])
        self.description = ""

    def mbd_check(self, request):
        """Rescan all archives, and check that there is at least one working."""
        msglog = MsgLog(LOG, request)

        self.archives.set([])
        with contextlib.closing(mini_buildd.gnupg.TmpGnuPG()) as gpg:
            for k in self.apt_keys.all():
                gpg.add_pub_key(k.key)

            for archive in Archive.objects.all():
                try:
                    # Check and update the ping value
                    archive.mbd_ping(request)

                    # Get release if this archive serves us, else exception
                    release = archive.mbd_get_matching_release(request, self, gpg)
                    if release:
                        # Implicitly save ping value for this archive
                        self.archives.add(archive)
                        self.description = release["Description"]

                        # Set codeversion
                        self.codeversion = ""
                        if self.codeversion_override:
                            self.codeversion = self.codeversion_override
                            msglog.info("{o}: Codeversion override active: {r}".format(o=self, r=self.codeversion_override))
                        else:
                            self.codeversion = mini_buildd.misc.guess_codeversion(release)
                            self.codeversion_override = self.codeversion
                            msglog.info("{o}: Codeversion guessed as: {r}".format(o=self, r=self.codeversion))

                        # Set architectures and components (may be auto-added)
                        if release.get("Architectures"):
                            for a in release["Architectures"].split(" "):
                                new_arch, _created = Architecture.mbd_get_or_create(msglog, name=a)
                                self.architectures.add(new_arch)
                        if release.get("Components"):
                            for c in release["Components"].split(" "):
                                new_component, _created = Component.mbd_get_or_create(msglog, name=c.replace(self.mbd_get_extra_option("X-Remove-From-Component", ""), ""))
                                self.components.add(new_component)
                        msglog.info("{o}: Added archive: {a}".format(o=self, a=archive))
                    else:
                        msglog.debug("{a}: Not hosting {s}".format(a=archive, s=self))
                except BaseException as e:
                    mini_buildd.setup.log_exception(msglog, "Error checking {a} for {s} (check Archive or Source)".format(a=archive, s=self), e)

        # Check that at least one archive can be found
        self.mbd_get_archive()

    def mbd_get_dependencies(self):
        return self.apt_keys.all()

    def mbd_get_reverse_dependencies(self):
        """Return all chroots and repositories that use us."""
        result = [c for c in self.chroot_set.all()]
        for d in self.distribution_set.all():
            result += d.mbd_get_reverse_dependencies()
        return result


class PrioritySource(mini_buildd.models.base.Model):
    source = django.db.models.ForeignKey(Source,
                                         on_delete=django.db.models.CASCADE)
    priority = django.db.models.IntegerField(default=1,
                                             help_text="A apt pin priority value (see 'man apt_preferences')."
                                             "Examples: 1=not automatic, 1001=downgrade'")

    class Meta(mini_buildd.models.base.Model.Meta):
        unique_together = ('source', 'priority')

    class Admin(mini_buildd.models.base.Model.Admin):
        exclude = ("extra_options",)

        @classmethod
        def mbd_meta_add_extras(cls, msglog):
            """Add all backports as prio=1 prio sources."""
            for source in Source.objects.exclude(codename__regex=r"^[a-z]+$"):
                PrioritySource.mbd_get_or_create(msglog, source=source, priority=1)

    def __str__(self):
        return "{i} with prio={p}".format(i=self.source, p=self.priority)

    def mbd_get_apt_preferences(self):
        return "Package: *\nPin: {pin}\nPin-Priority: {prio}\n".format(pin=self.source.mbd_get_apt_pin(), prio=self.priority)
