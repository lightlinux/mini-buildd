# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import datetime
import shutil
import errno
import subprocess
import threading
import Queue
import multiprocessing
import tempfile
import hashlib
import re
import urllib
import urllib2
import getpass
import pickle
import logging
import logging.handlers

LOG = logging.getLogger(__name__)


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
        self.__status__, self.__status_desc__, self.__stati__ = 0, "n/a", stati

    @property
    def status(self):
        return self.__stati__[self.__status__]

    @property
    def status_desc(self):
        return self.__status_desc__

    def set_status(self, status, desc="n/a"):
        """
        Set status with optional description.
        """
        self.__status__, self.__status_desc__ = status, desc

    def get_status(self):
        """
        Get raw (integer) status.
        """
        return self.__status__


class TmpDir(object):
    """
    Use with contextlib.closing() to guarantee tmpdir is purged afterwards.
    """
    def __init__(self):
        self._tmpdir = tempfile.mkdtemp()
        LOG.debug("TmpDir {t}".format(t=self._tmpdir))

    def close(self):
        LOG.debug("Purging tmpdir {t}".format(t=self._tmpdir))
        shutil.rmtree(self._tmpdir)

    @property
    def tmpdir(self):
        return self._tmpdir


class ConfFile(object):
    """ ConfFile generation helper.

    >>> ConfFile("/tmp/mini_buildd_test_conf_file", "my_option=7").add("my_2nd_option=42").save()
    """
    def __init__(self, file_path, snippet="", comment="#", encoding="UTF-8"):
        self._file_path = file_path
        self._encoding = encoding
        self._content = ""
        self.add("""\
{c} -*- coding: {e} -*-
{c} Generated by mini-buildd ({d}).
{c} Don't edit manually.""".format(c=comment, d=datetime.datetime.now(), e=encoding))
        self.add(snippet)

    def add(self, snippet):
        if isinstance(snippet, str):
            snippet = unicode(snippet, encoding=self._encoding)
            LOG.error("FIX CODE: Non-unicode string detected, converting assuming '{e}'.".format(e=self._encoding))
        self._content += "{s}\n".format(s=snippet)
        return self

    def save(self):
        open(self._file_path, "w").write(self._content.encode(self._encoding))


class BlockQueue(Queue.Queue):
    """
    Wrapper around Queue to get put() block until <= maxsize tasks are actually done.
    In Queue.Queue, task_done() is only used together with join().

    This way can use the Queue directly to limit the number of
    actually worked-on items for incoming and builds.
    """
    def __init__(self, maxsize):
        self._maxsize = maxsize
        self._active = Queue.Queue(maxsize=maxsize)
        Queue.Queue.__init__(self, maxsize=maxsize)

    def __unicode__(self):
        return "{l}: {n}/{m}".format(
            l=self.load,
            n=self._active.qsize(),
            m=self._maxsize)

    @property
    def load(self):
        return round(float(self._active.qsize()) / self._maxsize, 2)

    def put(self, item, **kwargs):
        self._active.put(item)
        Queue.Queue.put(self, item, **kwargs)

    def task_done(self):
        self._active.get()
        self._active.task_done()
        return Queue.Queue.task_done(self)


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


def nop(*_args, **_kwargs):
    pass


def timedelta_total_seconds(delta):
    """
    python 2.6 compat for timedelta.total_seconds() from python >= 2.7.
    """
    return float(delta.microseconds + (delta.seconds + delta.days * 24 * 3600) * (10 ** 6)) / (10 ** 6)


class Distribution(object):
    """
    A mini-buildd distribution string.

    >>> d = Distribution("squeeze-test-stable")
    >>> d.codename, d.repository, d.suite
    (u'squeeze', u'test', u'stable')
    >>> d.get()
    u'squeeze-test-stable'
    >>> d = Distribution("squeeze-test-stable-rollback5", allow_rollback=True)
    >>> d.is_rollback
    True
    >>> d.codename, d.repository, d.suite, d.rollback
    (u'squeeze', u'test', u'stable', u'rollback5')
    >>> d.get()
    u'squeeze-test-stable-rollback5'
    >>> d.rollback_no
    5
    """
    def __init__(self, dist, allow_rollback=False):
        self._dsplit = dist.split("-")
        self._max_parts = 4 if allow_rollback else 3

        def some_empty():
            for d in self._dsplit:
                if not d:
                    return True
            return False

        if (len(self._dsplit) < 3 or len(self._dsplit) > self._max_parts) or some_empty():
            raise Exception("Malformed distribution '{d}': Must be 'CODENAME-ID-SUITE{r}'".format(d=dist, r="[-rollbackN]" if allow_rollback else ""))

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
        return self._dsplit[3]

    @property
    def rollback_no(self):
        " Rollback (int) number: 'rollback0' -> 0 "
        return int(re.sub(r"\D", "", self.rollback))


def subst_placeholders(template, placeholders):
    """Substitue placeholders in string from a dict.

    >>> subst_placeholders("Repoversionstring: %IDENTITY%%CODEVERSION%", { "IDENTITY": "test", "CODEVERSION": "60" })
    u'Repoversionstring: test60'
    """
    for key, value in placeholders.items():
        template = template.replace("%{p}%".format(p=key), value)
    return template


def fromdos(string):
    return string.replace('\r\n', '\n').replace('\r', '')


def run_as_thread(thread_func=None, daemon=False, **kwargs):
    def run(**kwargs):
        tid = thread_func.__module__ + "." + thread_func.__name__
        try:
            LOG.info("{i}: Starting...".format(i=tid))
            thread_func(**kwargs)
            LOG.info("{i}: Finished.".format(i=tid))
        except Exception as e:
            LOG.exception("{i}: Exception: {e}".format(i=tid, e=e))
        except:
            LOG.exception("{i}: Non-standard exception".format(i=tid))

    thread = threading.Thread(target=run, kwargs=kwargs)
    thread.setDaemon(daemon)
    thread.start()
    return thread


def hash_of_file(file_name, hash_type="md5"):
    """
    Helper to get any hash from file contents.
    """
    md5 = hashlib.new(hash_type)
    with open(file_name) as f:
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


def taint_env(taint):
    env = os.environ.copy()
    for name in taint:
        env[name] = taint[name]
    return env


def get_cpus():
    try:
        return multiprocessing.cpu_count()
    except:
        return 1


def mkdirs(path):
    try:
        os.makedirs(path)
        LOG.info("Directory created: {d}".format(d=path))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
        else:
            LOG.debug("Directory already exists, ignoring; {d}".format(d=path))


def sose_call(args):
    result = tempfile.TemporaryFile()
    subprocess.check_call(args,
                          stdout=result,
                          stderr=subprocess.STDOUT)
    result.seek(0)
    return result.read().decode("UTF-8")


def call(args, run_as_root=False, value_on_error=None, log_output=True, error_log_on_fail=True, **kwargs):
    """Wrapper around subprocess.call().

    >>> call(["echo", "-n", "hallo"])
    u'hallo'
    >>> call(["id", "-syntax-error"], value_on_error="Kapott")
    u'Kapott'
    """

    if run_as_root:
        args = ["sudo"] + args

    stdout = tempfile.TemporaryFile()
    stderr = tempfile.TemporaryFile()

    LOG.info("Calling: {a}".format(a=args))
    try:
        olog = LOG.debug
        try:
            subprocess.check_call(args, stdout=stdout, stderr=stderr, **kwargs)
        except:
            if error_log_on_fail:
                olog = LOG.error
            raise
        finally:
            try:
                if log_output:
                    stdout.seek(0)
                    for line in stdout:
                        olog("Call stdout: {l}".format(l=line.decode("UTF-8").rstrip('\n')))
                    stderr.seek(0)
                    for line in stderr:
                        olog("Call stderr: {l}".format(l=line.decode("UTF-8").rstrip('\n')))
            except Exception as e:
                LOG.error("Output logging failed (char enc?): {e}".format(e=e))
    except:
        if error_log_on_fail:
            LOG.error("Call failed: {a}".format(a=args))
        if value_on_error is not None:
            return value_on_error
        else:
            raise
    LOG.info("Call successful: {a}".format(a=args))
    stdout.seek(0)
    return stdout.read().decode("UTF-8")


def call_sequence(calls, run_as_root=False, value_on_error=None, log_output=True, rollback_only=False, **kwargs):
    """Run sequences of calls with rolbback support.

    >>> call_sequence([(["echo", "-n", "cmd0"], ["echo", "-n", "rollback cmd0"])])
    >>> call_sequence([(["echo", "cmd0"], ["echo", "rollback cmd0"])], rollback_only=True)
    """

    def rollback(pos):
        for i in range(pos, -1, -1):
            if calls[i][1]:
                call(calls[i][1], run_as_root=run_as_root, value_on_error="", log_output=log_output, **kwargs)
            else:
                LOG.debug("Skipping empty rollback call sequent {i}".format(i=i))

    if rollback_only:
        rollback(len(calls) - 1)
    else:
        i = 0
        try:
            for l in calls:
                if l[0]:
                    call(l[0], run_as_root=run_as_root, value_on_error=value_on_error, log_output=log_output, **kwargs)
                else:
                    LOG.debug("Skipping empty call sequent {i}".format(i=i))
                i += 1
        except:
            LOG.error("Sequence failed at: {i} (rolling back)".format(i=i))
            rollback(i)
            raise


class CredsCache(object):
    def __init__(self, cache_file):
        self._file = cache_file
        self._creds = {}
        try:
            self._creds = pickle.load(open(self._file))
            LOG.debug("Creds cache pickled from '{c}'. {l} entries.".format(c=cache_file, l=len(self._creds)))
        except Exception as e:
            LOG.debug("Can't read credentials cache {c}: {e}".format(c=cache_file, e=e))
        self._changed = []

    def save(self):
        if self._changed:
            LOG.info("Got new credentials for: {c}".format(c=",".join(self._changed)))
            answer = raw_input("Cache (unencrypted) in '{f}' (y/n)? ".format(f=self._file))
            if answer.upper() == "Y":
                pickle.dump(self._creds,
                            os.fdopen(os.open(self._file, os.O_CREAT | os.O_WRONLY, 0600), "w"))
        else:
            LOG.debug("No changes in '{c}'".format(c=self._file))

    def clear(self):
        self._creds = {}
        if os.path.exists(self._file):
            os.remove(self._file)
            LOG.info("Credentials cache removed: {c}".format(c=self._file))
        else:
            LOG.info("No credentials cache file: {c}".format(c=self._file))

    def get(self, url):
        try:
            username, password = self._creds[url]
            LOG.debug("Using creds from cache '{f}': {url}, user {user}".format(f=self._file, url=url, user=username))
        except Exception as e:
            LOG.debug("Not in cache {u}: {e}".format(u=url, e=e))
            username = raw_input("Username: ")
            password = getpass.getpass("Password: ")
            self._changed.append(url)
            self._creds[url] = username, password

        return username, password


def web_login(url, credentials, login_loc="/admin/", next_loc="/mini_buildd/"):
    username = None
    try:
        login_url = url + login_loc
        next_url = url + next_loc

        username, password = credentials.get(url)

        # Create cookie-enabled opener
        cookie_handler = urllib2.HTTPCookieProcessor()
        opener = urllib2.build_opener(urllib2.HTTPHandler(debuglevel=0), cookie_handler)

        # Retrieve login page
        opener.open(login_url)

        # Find "csrftoken" in cookiejar
        csrf_cookies = [c for c in cookie_handler.cookiejar if c.name == "csrftoken"]
        if len(csrf_cookies) != 1:
            raise Exception("{n} csrftoken cookies found in login pages (need exactly 1).")
        LOG.debug("csrftoken={c}".format(c=csrf_cookies[0].value))

        # Login via POST request
        response = opener.open(
            login_url,
            urllib.urlencode({"username": username,
                              "password": password,
                              "csrfmiddlewaretoken": csrf_cookies[0].value,
                              "this_is_the_login_form": "1",
                              "next": next_loc,
                              }))

        # If successfull, next url of the response must match
        if response.geturl() != next_url:
            raise Exception("Wrong creds: Please check username and password")

        # Logged in: Install opener, save credentials
        LOG.info("User '{u}' logged in to '{url}'".format(u=username, url=url))
        urllib2.install_opener(opener)
        credentials.save()
    except Exception as e:
        raise Exception("Login as '{u}' failed: {e}".format(u=username, e=e))


SBUILD_KEYS_WORKAROUND_LOCK = threading.Lock()


def sbuild_keys_workaround():
    "Create sbuild's internal key if needed (sbuild needs this one-time call, but does not handle it itself)."
    with SBUILD_KEYS_WORKAROUND_LOCK:
        if os.path.exists("/var/lib/sbuild/apt-keys/sbuild-key.pub"):
            LOG.debug("/var/lib/sbuild/apt-keys/sbuild-key.pub: Already exists, skipping")
        else:
            t = tempfile.mkdtemp()
            LOG.warn("One-time generation of sbuild keys (may take some time)...")
            call(["sbuild-update", "--keygen"], env=taint_env({"HOME": t}))
            shutil.rmtree(t)
            LOG.info("One-time generation of sbuild keys done")


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


if __name__ == "__main__":
    setup_console_logging()

    import doctest
    doctest.testmod()
