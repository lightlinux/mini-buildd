import abc
import logging

import mini_buildd.misc
import mini_buildd.config

LOG = logging.getLogger(__name__)


class HttpD(metaclass=abc.ABCMeta):
    DOC_MISSING_HTML = """\
<html><body>
<h1>Online Manual Not Available (<tt>mini-buildd-doc</tt> not installed?)</h1>
Package <b><tt>mini-buildd-doc</tt></b> is not installed on this site (might be intentional to save space).
<p><a href="/">[Back]</a></p>
</body></html>
"""

    @abc.abstractmethod
    def _add_route(self, route, directory, with_index=False, uri_regex=r".*", with_doc_missing_error=False):
        """Serve static files from a directory."""

    def __init__(self):
        self._debug = "http" in mini_buildd.config.DEBUG
        self._foreground = mini_buildd.config.FOREGROUND

        self._char_encoding = mini_buildd.config.CHAR_ENCODING
        self._mime_text_plain = "text/plain; charset={charset}".format(charset=self._char_encoding)
        self._mime_types = {"log": self._mime_text_plain,
                            "buildlog": self._mime_text_plain,
                            "changes": self._mime_text_plain,
                            "dsc": self._mime_text_plain}
        self._endpoints = mini_buildd.config.HTTPD_ENDPOINTS

    def _add_routes(self):
        self._add_route("static", "{p}/mini_buildd/static".format(p=mini_buildd.config.PY_PACKAGE_PATH))                                       # WebApp static directory
        self._add_route("doc", mini_buildd.config.MANUAL_DIR, with_doc_missing_error=True)                                                     # HTML manual
        self._add_route("repositories", mini_buildd.config.REPOSITORIES_DIR, with_index=True, uri_regex=r"^/repositories/.+/(pool|dists)/.*")  # Repositories
        self._add_route("log", mini_buildd.config.LOG_DIR, with_index=True, uri_regex=r"^/log/.+/.*")                                          # Logs

    @abc.abstractmethod
    def run(self):
        """Run the HTTP server. Must be implemented by backend."""
