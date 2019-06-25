import copy
import enum
import ipaddress
import socket
import re
import urllib.request
import urllib.parse
import urllib.error
import ssl
import logging
import logging.handlers

import twisted.internet.endpoints

import mini_buildd.setup

LOG = logging.getLogger(__name__)


def parse_hopo(hopo):
    triple = hopo.rpartition(":")
    return triple[0], int(triple[2])


class Protocol(enum.Enum):
    HTTP = enum.auto()
    FTP = enum.auto()


def escape(string):
    return string.replace(":", r"\:")


def unescape(string):
    return string.replace(r"\:", ":")


class Endpoint():
    r"""Network server endpoint description parser (twisted-like).

    Syntax and semantic of the description string should be like in
    the twisted framework, see

    https://twistedmatrix.com/documents/current/core/howto/endpoints.html#servers

    Generic form: ':'-separated list of parameters:
     <param>[:<param>]...
    1st parameter is always the type, determining the syntax and semantics of the following parameters:
     <type>[:<param>]...
    A parameter may also be in key=value style, which we then call an option (can be accessed by its name):
     <type>[:<option>=<value>]...

    >>> Endpoint.hopo2desc("0.0.0.0:8066")
    'tcp:interface=0.0.0.0:port=8066'
    >>> print(Endpoint.hopo2desc(":::8066"))
    tcp6:interface=\:\::port=8066

    >>> Endpoint("tcp:host=example.com:port=1234", Protocol.HTTP).url()  # HTTP client
    'http://example.com:1234/'

    >>> Endpoint("tls:host=example.com:port=1234", Protocol.HTTP).url()  # HTTPS client
    'https://example.com:1234/'

    >>> Endpoint("tcp6:port=1234", Protocol.HTTP).url(host="example.com")  # HTTP server
    'http://example.com:1234/'

    >>> Endpoint("ssl:port=1234", Protocol.HTTP).url(host="example.com")  # HTTPS server
    'https://example.com:1234/'

    """
    _PROTOCOL2URL_SCHEME = {Protocol.HTTP: "http", Protocol.FTP: "ftp"}
    _SUPPORTED_TWISTED_TYPES = ["ssl", "tls", "tcp6", "tcp", "unix"]

    def __init__(self, desc, protocol):
        LOG.debug("Initializing endpoint: {} ({})".format(desc, protocol))
        self.desc = desc
        self.protocol = protocol

        self._params = [unescape(p) for p in re.split(r"(?<!\\):", desc)]
        if self.type not in self._SUPPORTED_TWISTED_TYPES:
            raise Exception("Unsupported endpoint type: {} (twisted types supported: {})".format(self.type, ",".join(self._SUPPORTED_TWISTED_TYPES)))

        self.url_scheme = self._PROTOCOL2URL_SCHEME[protocol]
        if self.type in ["ssl", "tls"]:
            self.url_scheme += "s"

        self._options = {}
        for p in self._params:
            key = p.partition("=")
            if key[1]:
                self._options[key[0]] = key[2]

    def __repr__(self):
        return "{scheme} on {desc}".format(scheme=self.url_scheme, desc=self.desc)

    def param(self, index):
        return self._params[index]

    @property
    def type(self):
        return self.param(0)

    def option(self, key, default=None):
        return self._options.get(key, default)

    def hopo(self, host=None):
        return "{host}:{port}".format(host=host if host else self.option("host") if self.option("host") else socket.getfqdn(), port=self.option("port"))

    def url(self, host=None):
        return "{scheme}://{hopo}/".format(scheme=self.url_scheme, hopo=self.hopo(host=host))

    # Compat...
    @classmethod
    def hopo2desc(cls, hopo, server=True):
        """Needed for HoPo compat."""
        host, port = parse_hopo(hopo)
        if server:  # pylint: disable=no-else-return
            typ = "tcp" if isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address) else "tcp6"
            return "{typ}:interface={host}:port={port}".format(typ=typ, port=port, host=escape(host))
        else:
            return "tcp:host={host}:port={port}".format(host=escape(host), port=port)

    @property
    def port(self):
        return self.option("port", "")


class ServerEndpoint(Endpoint):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        twisted.internet.endpoints.serverFromString(None, self.desc)  # Syntax check only for now

    def __repr__(self):
        return "Net server: {}".format(super().__repr__())

    def get_certificate(self):
        cert_key_file = self.option("certKey")
        return open(cert_key_file).read() if cert_key_file else ""


class ClientEndpoint(Endpoint):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        twisted.internet.endpoints.clientFromString(None, self.desc)  # Syntax check only for now

    def __repr__(self):
        return "Net client: {}".format(super().__repr__())

    def get_certificate(self):
        return ssl.get_server_certificate((self.option("host"), self.option("port")))


class UserURL():
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
            raise Exception("UserURL: We don't allow to give password in URL")
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
            full[1] = "{user}@{url}".format(user=self._username, url=self._plain[1])
            return urllib.parse.urlunparse(full)
        return self.plain


def urlopen_ca_certificates(url, **kwargs):
    """
    urlopen() with system's default ssl context.

    .. todo: Is this obsolete? Seems py3 urlopen() works w/o giving any context, but not sure if it uses the default context, or rather no auth.
    """
    context = ssl.create_default_context()
    return urllib.request.urlopen(url, context=context, **kwargs)


def detect_apt_cacher_ng(url="http://localhost:3142"):
    """
    Little heuristic helper for the "local archives" wizard.
    """
    try:
        urlopen_ca_certificates(url)
    except urllib.error.HTTPError as e:
        if e.code == 406 and re.findall(r"apt.cacher.ng", e.file.read().decode("UTF-8"), re.IGNORECASE):
            return url
    except Exception:  # pylint: disable=broad-except
        pass
    return None


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
        opener = urllib.request.build_opener(cookie_handler)

        # Retrieve login page
        opener.open(login_url)
        opener.addheaders = [("Referer", plain_url)]

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

        # We need to use this very opener object to stay logged in (for some reason, standard urlopen() just worked fine for http).
        return opener
    except Exception as e:
        raise Exception("Login failed: {u}@{h}: {e}".format(u=user, h=host, e=e))
