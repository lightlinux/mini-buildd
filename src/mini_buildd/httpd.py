# -*- coding: utf-8 -*-

import abc
import logging

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class HttpD(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def add_static_handler(self, path, root, with_index=False, match="", with_manual_missing_error=False):
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
        # Serve mini_buildd webapp's static directory
        self.add_static_handler(mini_buildd.setup.STATIC_URL,
                                "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

        # Serve django admin webapp's static directory.
        # Note: 'STATIC_URL' has trailing "/"; cherrypy does not like double slashes inside path like add_static_handler("/my//path").
        self.add_static_handler("{p}admin/".format(p=mini_buildd.setup.STATIC_URL),
                                "{p}/django/contrib/admin/static/admin".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

        # Serve mini-buildd's HTML manual
        self.add_static_handler("/doc/",
                                mini_buildd.setup.MANUAL_DIR,
                                with_manual_missing_error=True)

        # Serve repositories with index support
        self.add_static_handler("/repositories/",
                                mini_buildd.setup.REPOSITORIES_DIR,
                                with_index=True,
                                match=r"^/.+/(pool|dists)/.*")

        # Serve logs with index support
        self.add_static_handler("/log/",
                                mini_buildd.setup.LOG_DIR,
                                with_index=True,
                                match=r"^/.+/.*")

    @abc.abstractmethod
    def run(self):
        "Run the HTTP server. Must be implemented by backend."
        pass


def run(bind, wsgi_app):
    """
    Run the Web Server.

    :param bind: the bind address to use.
    :type bind: string
    :param wsgi_app: the web application to process.
    :type wsgi_app: WSGI-application

    """
    from mini_buildd.httpd_cherrypy import Backend
    Backend(bind, wsgi_app).run()
