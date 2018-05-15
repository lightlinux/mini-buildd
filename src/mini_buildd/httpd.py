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
