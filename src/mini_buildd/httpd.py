# -*- coding: utf-8 -*-

import logging

import mini_buildd.misc
import mini_buildd.setup
import mini_buildd.httpd_cherrypy

LOG = logging.getLogger(__name__)


class HttpD(object):
    def __init__(self, backend, bind, wsgi_app):
        self._backend = backend
        self._backend.setup(bind, wsgi_app)

        # Serve mini_buildd webapp's static directory
        self._backend.add_static_handler(mini_buildd.setup.STATIC_URL,
                                         "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

        # Serve django admin webapp's static directory.
        # Note: 'STATIC_URL' has trailing "/"; cherrypy does not like double slashes inside path like add_static_handler("/my//path").
        self._backend.add_static_handler("{p}admin/".format(p=mini_buildd.setup.STATIC_URL),
                                         "{p}/django/contrib/admin/static/admin".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

        # Serve mini-buildd's HTML manual
        self._backend.add_static_handler("/doc/",
                                         mini_buildd.setup.MANUAL_DIR,
                                         with_manual_missing_error=True)

        # Serve repositories with index support
        self._backend.add_static_handler("/repositories/",
                                         mini_buildd.setup.REPOSITORIES_DIR,
                                         with_index=True,
                                         match=r"^/.+/(pool|dists)/.*")

        # Serve logs with index support
        self._backend.add_static_handler("/log/",
                                         mini_buildd.setup.LOG_DIR,
                                         with_index=True,
                                         match=r"^/.+/.*")

    def run(self):
        self._backend.run()


def run(bind, wsgi_app):
    """
    Run the Web Server.

    :param bind: the bind address to use.
    :type bind: string
    :param wsgi_app: the web application to process.
    :type wsgi_app: WSGI-application

    """
    HttpD(mini_buildd.httpd_cherrypy, bind, wsgi_app).run()
