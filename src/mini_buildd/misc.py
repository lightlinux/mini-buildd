# -*- coding: utf-8 -*-

import os
import copy
import datetime
import shutil
import glob
import errno
import threading
import socket
import queue
import multiprocessing
import tempfile
import hashlib
import base64
import re
import urllib.request
import urllib.parse
import urllib.error
import getpass
import logging
import logging.handlers

import debian.debian_support

import mini_buildd.setup  # pylint: disable=wrong-import-position

LOG = logging.getLogger(__name__)


def open_utf8(path, mode="r", **kwargs):
    return open(path, mode, encoding=mini_buildd.setup.CHAR_ENCODING, **kwargs)


def check_multiprocessing():
    "Multiprocessing needs shared memory. This may be use to check for misconfigured shm early for better error handling."
    try:
        multiprocessing.Lock()
    except Exception as e:
        raise Exception("multiprocessing not functional (shm misconfigured?): {e}".format(e=e))


class API(object):
    """
    Helper class to implement an API check.

    Inheriting classes must define an __API__ class attribute
    that should be increased on incompatible changes, and may
    then check via api_check() method.
    """
    __API__ = -1000

    def __init__(self):
        self.__api__ = self.__API__

    def api_check(self):
        return self.__api__ == self.__API__


class Status(object):
    """
    Helper class to implement an internal status.

    Inheriting classes must give a stati dict to init.
    """
    def __init__(self, stati):
        self.__status__, self.__status_desc__, self.__stati__ = 0, "", stati

    @property
    def status(self):
        return self.__stati__[self.__status__]

    @property
    def status_desc(self):
        return self.__status_desc__

    def set_status(self, status, desc=""):
        """
        Set status with optional description.
        """
        self.__status__, self.__status_desc__ = status, desc

    def get_status(self):
        """
        Get raw (integer) status.
        """
        return self.__status__


def _skip_if_in_debug(key, func, *args, **kwargs):
    if key in mini_buildd.setup.DEBUG:
        LOG.warning("DEBUG MODE('{k}'): Skipping: {f} {args} {kwargs}".format(k=key, f=func, args=args, kwargs=kwargs))
    else:
        return func(*args, **kwargs)


def skip_if_keep_in_debug(func, *args, **kwargs):
    return _skip_if_in_debug("keep", func, *args, **kwargs)


class TmpDir(object):
    """
    Use with contextlib.closing() to guarantee tmpdir is purged afterwards.
    """
    def __init__(self, tmpdir=None):
        self._tmpdir = tmpdir if tmpdir else tempfile.mkdtemp(dir=mini_buildd.setup.TMP_DIR)
        LOG.debug("TmpDir {t}".format(t=self._tmpdir))

    def close(self):
        LOG.debug("Purging tmpdir: {t}".format(t=self._tmpdir))
        skip_if_keep_in_debug(shutil.rmtree, self._tmpdir, ignore_errors=True)

    @property
    def tmpdir(self):
        return self._tmpdir

    @classmethod
    def file_dir(cls, file_name):
        # nf "/var/lib/mini-buildd/tmp/t123/xyz.file
        # nd "/var/lib/mini-buildd/tmp/t123"
        # nt "/var/lib/mini-buildd/tmp"
        nf = os.path.normpath(file_name)
        nd = os.path.dirname(nf)
        nt = os.path.normpath(mini_buildd.setup.TMP_DIR)
        if nf.startswith(nt) and nd != nt:
            return nd


class ConfFile(object):
    """ ConfFile generation helper.

    >>> ConfFile("/tmp/mini_buildd_test_conf_file", "my_option=7").add("my_2nd_option=42").save()
    """
    def __init__(self, file_path, snippet="", comment="#"):
        self._file_path = file_path
        self._content = ""
        self.add("""\
{c} -*- coding: {e} -*-
{c} Generated by mini-buildd ({d}).
{c} Don't edit manually.""".format(c=comment, d=datetime.datetime.now(), e=mini_buildd.setup.CHAR_ENCODING))
        self.add(snippet)

    def add(self, snippet):
        self._content += "{s}\n".format(s=snippet)
        return self

    def save(self):
        open_utf8(self._file_path, "w").write(self._content)


class BlockQueue(queue.Queue):
    """
    Wrapper around Queue to get put() block until <= maxsize tasks are actually done.
    In Queue.Queue, task_done() is only used together with join().

    This way can use the Queue directly to limit the number of
    actually worked-on items for incoming and builds.
    """
    def __init__(self, maxsize):
        self._maxsize = maxsize
        self._pending = 0
        self._active = queue.Queue(maxsize=maxsize)
        queue.Queue.__init__(self, maxsize=maxsize)

    def __str__(self):
        return "{l}: {n}/{m} ({p} pending)".format(
            l=self.load,
            n=self._active.qsize(),
            m=self._maxsize,
            p=self._pending)

    @property
    def load(self):
        return round(float(self._active.qsize() + self._pending) / self._maxsize, 2)

    def put(self, item, **kwargs):
        self._pending += 1
        self._active.put(item)
        queue.Queue.put(self, item, **kwargs)
        self._pending -= 1

    def task_done(self):
        self._active.get()
        self._active.task_done()
        return queue.Queue.task_done(self)


class HoPo(object):
    """ Convenience class to parse bind string "hostname:port" """
    def __init__(self, bind):
        try:
            self.string = bind
            triple = bind.rpartition(":")
            self.tuple = (triple[0], int(triple[2]))
            self.host = self.tuple[0]
            self.port = self.tuple[1]
        except:
            raise Exception("Invalid bind argument (HOST:PORT): '{b}'".format(b=bind))

    def test_bind(self):
        "Check that we can bind to the first found addrinfo (not already bound, permissions)."
        ai = socket.getaddrinfo(self.host, self.port)[0]
        s = socket.socket(family=ai[0])
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(self.tuple)
        s.close()


def nop(*_args, **_kwargs):
    pass


def dont_care_run(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except:
        pass


def timedelta_total_seconds(delta):
    """
    python 2.6 compat for timedelta.total_seconds() from python >= 2.7.
    """
    return float(delta.microseconds + (delta.seconds + delta.days * 24 * 3600) * (10 ** 6)) / (10 ** 6)


def codename_has_lintian_suppress(codename):
    "Test if the distribution (identified by the codename) has a recent lintian with the '--suppress-tags' option."
    return codename not in ["buzz", "rex", "bo", "hamm", "slink", "potato", "woody", "sarge", "etch", "lenny"]


class Distribution(object):
    """
    A mini-buildd distribution string.

    Normal distribution:

    >>> d = Distribution("squeeze-test-stable")
    >>> d.codename, d.repository, d.suite
    ('squeeze', 'test', 'stable')
    >>> d.get()
    'squeeze-test-stable'

    Rollback distribution:

    >>> d = Distribution("squeeze-test-stable-rollback5")
    >>> d.is_rollback
    True
    >>> d.codename, d.repository, d.suite, d.rollback
    ('squeeze', 'test', 'stable', 'rollback5')
    >>> d.get()
    'squeeze-test-stable-rollback5'
    >>> d.rollback_no
    5

    Malformed distributions:

    >>> Distribution("-squeeze-stable")
    Traceback (most recent call last):
    ...
    Exception: Malformed distribution '-squeeze-stable': Must be '<codename>-<repoid>-<suite>[-rollback<n>]'

    >>> Distribution("squeeze--stable")
    Traceback (most recent call last):
    ...
    Exception: Malformed distribution 'squeeze--stable': Must be '<codename>-<repoid>-<suite>[-rollback<n>]'

    >>> Distribution("squeeze-test-stable-")
    Traceback (most recent call last):
    ...
    Exception: Malformed distribution 'squeeze-test-stable-': Must be '<codename>-<repoid>-<suite>[-rollback<n>]'

    >>> Distribution("squeeze-test-stable-rollback")
    Traceback (most recent call last):
    ...
    Exception: Malformed distribution 'squeeze-test-stable-rollback': Must be '<codename>-<repoid>-<suite>[-rollback<n>]'

    >>> Distribution("squeeze-test-stable-rolback0")
    Traceback (most recent call last):
    ...
    Exception: Malformed distribution 'squeeze-test-stable-rolback0': Must be '<codename>-<repoid>-<suite>[-rollback<n>]'
    """

    _REGEX = re.compile(r"^\w+-\w+-\w+?(-rollback\d+)?$")

    def __init__(self, dist):
        LOG.debug("Parsing internal distribution: {d}...".format(d=dist))

        if not self._REGEX.match(dist):
            raise Exception("Malformed distribution '{d}': Must be '<codename>-<repoid>-<suite>[-rollback<n>]'".format(d=dist))

        self.dist = dist
        self._dsplit = self.dist.split("-")

    def get(self, rollback=True):
        if rollback:
            return "-".join(self._dsplit)
        else:
            return "-".join(self._dsplit[:3])

    @property
    def codename(self):
        return self._dsplit[0]

    @property
    def repository(self):
        return self._dsplit[1]

    @property
    def suite(self):
        return self._dsplit[2]

    @property
    def is_rollback(self):
        return len(self._dsplit) == 4

    @property
    def rollback(self):
        if self.is_rollback:
            return self._dsplit[3]

    @property
    def rollback_no(self):
        " Rollback (int) number: 'rollback0' -> 0 "
        if self.is_rollback:
            return int(re.sub(r"\D", "", self.rollback))

    def has_lintian_suppress(self):
        return codename_has_lintian_suppress(self.codename)


def strip_epoch(version):
    "Strip the epoch from a version string."
    return version.rpartition(":")[2]


def guess_codeversion(release):
    """
    Guess the 'codeversion' aka the first two digits of a Debian
    release version; for releases without version, this falls
    back to the uppercase codename.

    In Debian,
      - point release <= sarge had the 'M.PrN' syntax (with 3.1 being a major release).
      - point release in squeeze used 'M.0.N' syntax.
      - point releases for >= wheezy have the 'M.N' syntax (with 7.1 being a point release).
      - testing and unstable do not gave a version in Release and fall back to uppercase codename

    Ubuntu just uses YY.MM which we can use as-is.

    >>> guess_codeversion({"Origin": "Debian", "Version": "3.1r8", "Codename": "sarge"})
    '31'
    >>> guess_codeversion({"Origin": "Debian", "Version": "4.0r9", "Codename": "etch"})
    '40'
    >>> guess_codeversion({"Origin": "Debian", "Version": "6.0.6", "Codename": "squeeze"})
    '60'
    >>> guess_codeversion({"Origin": "Debian", "Version": "7.0", "Codename": "wheezy"})
    '7'
    >>> guess_codeversion({"Origin": "Debian", "Version": "7.1", "Codename": "wheezy"})
    '7'
    >>> guess_codeversion({"Origin": "Debian", "Codename": "jessie"})
    '~JESSIE'
    >>> guess_codeversion({"Origin": "Debian", "Codename": "sid"})
    '~SID'
    >>> guess_codeversion({"Origin": "Ubuntu", "Version": "12.10", "Codename": "quantal"})
    '1210'
    """
    try:
        ver_split = release["Version"].split(".")
        number0 = ver_split[0]
        number1 = ver_split[1].partition("r")[0]  # Some older Debian versions had an "r" in the number
        if release.get("Origin", None) == "Debian" and int(number0) >= 7:
            return number0  # Debian >= wheezy: '~testN': One number tells the codeversion
        else:
            return number0 + number1  # Debian < wheezy, Ubuntu, maybe others: 1st plus 2nd number is needed
    except:
        return "~" + release["Codename"].upper()


def guess_default_dirchroot_backend(overlay, aufs):
    try:
        release = os.uname()[2]
        # linux 3.18-1~exp1 in Debian removed aufs in favor of overlay
        if debian.debian_support.Version(release) < debian.debian_support.Version("3.18"):
            return aufs
    except:
        pass

    return overlay


def chroot_libdir_path(codename, architecture):
    return os.path.join(mini_buildd.setup.CHROOTS_LIBDIR, codename, architecture)


def pkg_fmt(status, distribution, package, version, extra=None, message=None):
    "Generate a package status line."
    fmt = "{status} ({distribution}): {package} {version}".format(status=status,
                                                                  distribution=distribution,
                                                                  package=package,
                                                                  version=version)
    if extra:
        fmt += " [{extra}]".format(extra=extra)
    if message:
        fmt += ": {message}".format(message=message)
    return fmt


class PkgLog(object):
    @classmethod
    def get_path(cls, repository, installed, package, version=None, architecture=None, relative=False):
        return os.path.join("" if relative else mini_buildd.setup.LOG_DIR,
                            repository,
                            "" if installed else "_failed",
                            package,
                            version if version else "",
                            architecture if architecture else "")

    @classmethod
    def make_relative(cls, path):
        return path.replace(mini_buildd.setup.LOG_DIR, "")

    def __init__(self, repository, installed, package, version):
        self.path = self.get_path(repository, installed, package, version)

        # Find build logs: "LOG_DIR/REPO/[_failed/]PACKAGE/VERSION/ARCH/PACKAGE_VERSION_ARCH.buildlog"
        self.buildlogs = {}
        for buildlog in glob.glob("{p}/*/*.buildlog".format(p=self.path)):
            arch = os.path.basename(os.path.dirname(buildlog))
            self.buildlogs[arch] = buildlog

        # Find changes: "LOG_DIR/REPO/[_failed/]PACKAGE/VERSION/ARCH/PACKAGE_VERSION_ARCH.changes"
        self.changes = None
        for c in glob.glob("{p}/*/*.changes".format(p=self.path)):
            if not ("mini-buildd-buildrequest" in c or "mini-buildd-buildresult" in c):
                self.changes = c
                break


def subst_placeholders(template, placeholders):
    """Substitue placeholders in string from a dict.

    >>> subst_placeholders("Repoversionstring: %IDENTITY%%CODEVERSION%", { "IDENTITY": "test", "CODEVERSION": "60" })
    'Repoversionstring: test60'
    """
    for key, value in list(placeholders.items()):
        template = template.replace("%{p}%".format(p=key), value)
    return template


def fromdos(string):
    return string.replace('\r\n', '\n').replace('\r', '')


def run_as_thread(thread_func, name, daemon=False, **kwargs):
    def run(**kwargs):
        qual_thread_func = thread_func.__module__ + "." + thread_func.__name__
        try:
            LOG.debug("Thread function started: {f}.".format(f=qual_thread_func))
            thread_func(**kwargs)
            LOG.debug("Thread function finished: {f}.".format(f=qual_thread_func))
        except Exception as e:
            mini_buildd.setup.log_exception(LOG, "Error in thread function: {f}".format(f=qual_thread_func), e)
        except:
            LOG.exception("Non-standard exception in thread function: {f}".format(f=qual_thread_func))

    thread = threading.Thread(target=run, name=name, kwargs=kwargs)
    thread.setDaemon(daemon)
    thread.start()
    return thread


def hash_of_file(file_name, hash_type="md5"):
    """
    Helper to get any hash from file contents.
    """
    md5 = hashlib.new(hash_type)
    with open(file_name, "rb") as f:
        while True:
            data = f.read(128)
            if not data:
                break
            md5.update(data)
    return md5.hexdigest()


def md5_of_file(file_name):
    return hash_of_file(file_name, hash_type="md5")


def sha1_of_file(file_name):
    return hash_of_file(file_name, hash_type="sha1")


def u2b64(unicode_string):
    """
    Convert unicode string to base46.

    >>> b64 = u2b64("Ünicode strüng")
    >>> b64.__class__.__name__
    'bytes'
    >>> b64
    b'w5xuaWNvZGUgc3Ryw7xuZw=='
    """
    return base64.b64encode(unicode_string.encode(mini_buildd.setup.CHAR_ENCODING))


def b642u(base64_bytestream):
    """
    Convert base64 string (in :py:data:`mini_buildd.setup.CHAR_ENCODING`) to unicode.

    >>> u = b642u('w5xuaWNvZGUgc3Ryw7xuZw==')
    >>> u.__class__.__name__
    'str'
    >>> print(u)
    Ünicode strüng
    """
    return str(base64.b64decode(base64_bytestream), encoding=mini_buildd.setup.CHAR_ENCODING)


def get_cpus():
    try:
        return multiprocessing.cpu_count()
    except:
        return 1


def list_get(list_, index, default=None):
    try:
        return list_[index]
    except IndexError:
        return default


def mkdirs(path):
    """
    Make path and any parents needed. Succeed even if it does already exist in the first place.

    .. note:: Needed for python 2.x only. For 3.x, just use 'exist_ok' parameter for os.makedirs().
    """
    try:
        os.makedirs(path)
        LOG.info("Directory created: {d}".format(d=path))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
        else:
            LOG.debug("Directory already exists, ignoring; {d}".format(d=path))


def rmdirs(path):
    """
    Remove path recursively. Succeed even if it does not exist in the first place.
    """
    if os.path.exists(path):
        shutil.rmtree(path)
        LOG.info("Directory removed recursively: {p}".format(p=path))


class UserURL(object):
    """
    URL with a username attached.

    >>> U = UserURL("http://admin@localhost:8066")
    >>> (U.username, U.plain, U.full)
    ('admin', 'http://localhost:8066', 'http://admin@localhost:8066')

    >>> U = UserURL("http://example.org:8066", "admin")
    >>> (U.username, U.plain, U.full)
    ('admin', 'http://example.org:8066', 'http://admin@example.org:8066')

    >>> UserURL("http://localhost:8066")
    Traceback (most recent call last):
      ...
    Exception: UserURL: No username given

    >>> UserURL("http://admin@localhost:8066", "root")
    Traceback (most recent call last):
      ...
    Exception: UserURL: Username given in twice, in URL and parameter
    """
    def __init__(self, url, username=None):
        parsed = urllib.parse.urlparse(url)
        if parsed.password:
            raise Exception("UserURL: We don't allow to give pasword in URL")
        if parsed.username and username:
            raise Exception("UserURL: Username given in twice, in URL and parameter")
        if not parsed.username and not username:
            raise Exception("UserURL: No username given")
        if username:
            self._username = username
        else:
            self._username = parsed.username
        self._plain = list(parsed)
        self._plain[1] = parsed[1].rpartition("@")[2]

    def __str__(self):
        return self.full

    @property
    def username(self):
        return self._username

    @property
    def plain(self):
        "URL string without username."
        return urllib.parse.urlunparse(self._plain)

    @property
    def full(self):
        "URL string with username."
        if self._username:
            full = copy.copy(self._plain)
            full[1] = "{u}@{l}".format(u=self._username, l=self._plain[1])
            return urllib.parse.urlunparse(full)
        else:
            return self.plain


def qualname(obj):
    return "{m}.{c}".format(m=obj.__module__, c=obj.__class__.__name__)


class Keyring(object):
    _SAVE_POLICY_KEY = "save_policy"

    @classmethod
    def _get_python_keyring(cls):
        """
        Import wrapper for keyring module.

        Only by importing the ``keyring`` module, the module peruses the default backend
        and tries to initialize it. This might fail, depending from what environment
        ``mini_buildd.misc`` was imported. Doing the import here puts that code to
        the time when the keyring is actually used.
        """
        # Workaround: Avoid warning 'No handlers could be found for logger "keyring"'
        keyring_log = logging.getLogger("keyring")
        keyring_log.addHandler(logging.NullHandler())

        import keyring  # pylint: disable=wrong-import-position
        try:
            from keyring.util.platform_ import data_root as keyring_data_root  # pylint: disable=wrong-import-position,wrong-import-order,import-error,no-name-in-module
        except ImportError:
            from keyring.util.platform import data_root as keyring_data_root  # pylint: disable=wrong-import-position,wrong-import-order,import-error,no-name-in-module

        LOG.info("Viable keyring backends: {b}".format(b=" ".join([qualname(o) for o in keyring.backend.get_all_keyring()])))
        LOG.info("Hint: You may set up '{r}/keyringrc.cfg' to force a backend.".format(r=keyring_data_root()))
        LOG.info("Hint: See 'keyringrc.cfg' in the package's docs 'examples' directory for a sample file.")

        return keyring.get_keyring()

    def __init__(self, service):
        self._service = service
        self._keyring = self._get_python_keyring()
        self._save_policy = self._keyring.get_password(service, self._SAVE_POLICY_KEY)

    def __str__(self):
        return "Saving '{s}' passwords to '{k}' with policy '{p}'".format(
            s=self._service,
            k=qualname(self._keyring),
            p={"A": "Always", "V": "Never"}.get(self._save_policy, "Ask"))

    def reset_save_policy(self):
        LOG.warning("Resetting save policy in '{k}' back to 'Ask'.".format(k=qualname(self._keyring)))
        if self._save_policy:
            self._keyring.delete_password(self._service, self._SAVE_POLICY_KEY)
            self._save_policy = None

    def set(self, key, password):
        if self._save_policy:
            answer = self._save_policy
        else:
            while True:
                answer = input("""
{c}:

Save password for '{k}': (Y)es, (N)o, (A)lways, Ne(v)er? """.format(c=self, k=key)).upper()[:1]
                if answer in ["Y", "N", "A", "V"]:
                    break

        if answer in ["A", "V"]:
            self._keyring.set_password(self._service, self._SAVE_POLICY_KEY, answer)
            LOG.info("Password saved to '{k}'".format(k=qualname(self._keyring)))

        if answer in ["Y", "A"]:
            self._keyring.set_password(self._service, key, password)

    def get(self, host, user=""):
        if not user:
            user = input("[{h}] Username: ".format(h=host))
        key = "{u}@{h}".format(u=user, h=host)

        password = self._keyring.get_password(self._service, key)
        if password:
            LOG.info("Password retrieved from '{k}'".format(k=qualname(self._keyring)))
            new = False
        else:
            password = getpass.getpass("[{k}] Password: ".format(k=key))
            new = True

        return key, user, password, new


def urlopen_ca_certificates(url):
    """
    Wrapper for urlib2.urlopen, optionally using certificates from ca-certificates package, when installed.
    (See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=832350).
    """
    cafile = "/etc/ssl/certs/ca-certificates.crt"
    return urllib.request.urlopen(url, cafile=cafile) if os.path.exists(cafile) else urllib.request.urlopen(url)


def canonize_url(url):
    "Poor man's URL canonizer: Always include the port (currently only works for 'http' and 'https' default ports)."
    default_scheme2port = {"http": ":80", "https": ":443"}

    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc
    if parsed.port is None:
        netloc = parsed.hostname + default_scheme2port.get(parsed.scheme, "")
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


def web_login(host, user, credentials,
              proto="http",
              login_loc="/accounts/login/",
              next_loc="/mini_buildd/"):
    plain_url = "{p}://{h}".format(p=proto, h=host)
    try:
        key, user, password, new = credentials.get(host, user)
        login_url = plain_url + login_loc
        next_url = plain_url + next_loc

        # Create cookie-enabled opener
        cookie_handler = urllib.request.HTTPCookieProcessor()
        opener = urllib.request.build_opener(urllib.request.HTTPHandler(debuglevel=0), cookie_handler)

        # Retrieve login page
        opener.open(login_url)

        # Find "csrftoken" in cookiejar
        csrf_cookies = [c for c in cookie_handler.cookiejar if c.name == "csrftoken"]
        if len(csrf_cookies) != 1:
            raise Exception("{n} csrftoken cookies found in login pages (need exactly 1).".format(n=len(csrf_cookies)))
        LOG.debug("csrftoken={c}".format(c=csrf_cookies[0].value))

        # Login via POST request
        response = opener.open(
            login_url,
            bytes(urllib.parse.urlencode({"username": user,
                                          "password": password,
                                          "csrfmiddlewaretoken": csrf_cookies[0].value,
                                          "this_is_the_login_form": "1",
                                          "next": next_loc}),
                  encoding=mini_buildd.setup.CHAR_ENCODING))

        # If successful, next url of the response must match
        if canonize_url(response.geturl()) != canonize_url(next_url):
            raise Exception("Wrong credentials: Please try again")

        # Logged in: Install opener, save credentials
        LOG.info("User logged in: {key}".format(key=key))
        urllib.request.install_opener(opener)
        if new:
            credentials.set(key, password)
    except Exception as e:
        raise Exception("Login failed: {u}@{h}: {e}".format(u=user, h=host, e=e))


def clone_log(dst, src="mini_buildd"):
    "Setup logger named 'dst' with the same handlers and loglevel as the logger named 'src'."
    src_log = logging.getLogger(src)
    dst_log = logging.getLogger(dst)
    dst_log.handlers = []
    for h in src_log.handlers:
        dst_log.addHandler(h)
    dst_log.setLevel(src_log.getEffectiveLevel())


def setup_console_logging(level=logging.DEBUG):
    logging.addLevelName(logging.DEBUG, "D")
    logging.addLevelName(logging.INFO, "I")
    logging.addLevelName(logging.WARNING, "W")
    logging.addLevelName(logging.ERROR, "E")
    logging.addLevelName(logging.CRITICAL, "C")

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    for ln in ["__main__", "mini_buildd"]:
        l = logging.getLogger(ln)
        l.addHandler(ch)
        l.setLevel(level)
