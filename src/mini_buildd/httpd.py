# -*- coding: utf-8 -*-

import abc
import logging

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class HttpD(metaclass=abc.ABCMeta):
    DOC_MISSING_HTML_TEMPLATE = """\
<html><body>
<h1>{status} (<tt>mini-buildd-doc</tt> not installed?)</h1>
Maybe package <b><tt>mini-buildd-doc</tt></b> needs to be installed to make the manual available.
</body></html>
"""

    @abc.abstractmethod
    def add_static(self, route, directory, with_index=False, match="", with_doc_missing_error=False):
        "Serve static files from a directory."
        pass

    @abc.abstractmethod
    def __init__(self, bind, wsgi_app):
        """
        Setup HTTP server.

        :param bind: the bind address to use.
        :type bind: string
        :param wsgi_app: the web application to process.
        :type wsgi_app: WSGI-application
        """
        pass

    def __init__(self):
        self.add_static("static", "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))                      # WebApp static directory
        self.add_static("doc", mini_buildd.setup.MANUAL_DIR, with_doc_missing_error=True)                                    # HTML manual
        self.add_static("repositories", mini_buildd.setup.REPOSITORIES_DIR, with_index=True, match=r"^/.+/(pool|dists)/.*")  # Repositories
        self.add_static("log", mini_buildd.setup.LOG_DIR, with_index=True, match=r"^/.+/.*")                                 # Logs

    @abc.abstractmethod
    def run(self):
        "Run the HTTP server. Must be implemented by backend."
        pass
