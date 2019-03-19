# -*- coding: utf-8 -*-

import abc
import logging

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class HttpD(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def _add_route(self, route, directory, with_index=False, uri_regex=r".*", with_doc_missing_error=False):
        "Serve static files from a directory."

    def __init__(self, supported_types):
        self._doc_missing_html_template = """\
<html><body>
<h1>{status} (<tt>mini-buildd-doc</tt> not installed?)</h1>
Maybe package <b><tt>mini-buildd-doc</tt></b> needs to be installed to make the manual available.
</body></html>
"""
        self._debug = "http" in mini_buildd.setup.DEBUG
        self._foreground = mini_buildd.setup.FOREGROUND
        self._access_log_file = mini_buildd.setup.ACCESS_LOG_FILE
        self._char_encoding = mini_buildd.setup.CHAR_ENCODING
        self._mime_text_plain = "text/plain; charset={charset}".format(charset=self._char_encoding)
        self._mime_types = {"log": self._mime_text_plain,
                            "buildlog": self._mime_text_plain,
                            "changes": self._mime_text_plain,
                            "dsc": self._mime_text_plain}
        self._endpoints = mini_buildd.setup.HTTPD_ENDPOINTS
        for ep in self._endpoints:
            if ep.type not in supported_types:
                raise Exception("HTTPd backend does not support network endpoint type: {}".format(ep.type))

    def _add_routes(self):
        self._add_route("static", "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))                                       # WebApp static directory
        self._add_route("doc", mini_buildd.setup.MANUAL_DIR, with_doc_missing_error=True)                                                     # HTML manual
        self._add_route("repositories", mini_buildd.setup.REPOSITORIES_DIR, with_index=True, uri_regex=r"^/repositories/.+/(pool|dists)/.*")  # Repositories
        self._add_route("log", mini_buildd.setup.LOG_DIR, with_index=True, uri_regex=r"^/log/.+/.*")                                          # Logs

    @abc.abstractmethod
    def run(self):
        "Run the HTTP server. Must be implemented by backend."
