# -*- coding: utf-8 -*-

import logging
import mimetypes
import os

import wsgiref.validate
import wsgiref.simple_server
import wsgiref.util

import mini_buildd.misc
import mini_buildd.httpd

LOG = logging.getLogger(__name__)


class StaticAndWSGI(object):
    """
    Not for production: Very simple WSGI app, adding static routing and delivery via wsgi.
    """
    def __init__(self, wsgi):
        self._wsgi = wsgi
        self._routes = {}

    def add_route(self, route, directory):
        self._routes[route] = directory

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "/")
        for route, directory in self._routes.items():
            if path_info.startswith("/{}".format(route)):
                translated_path = os.path.join(directory, path_info[1 + len(route):].strip("/"))
                LOG.info("Static deliver translation: {} -> {}".format(path_info, translated_path))

                if os.path.isdir(translated_path):
                    start_response("200 OK", [('Content-type', 'text/html; charset=utf-8')])
                    return [mini_buildd.httpd.html_index(translated_path, path_info, "wsgiref")]
                if os.path.isfile(translated_path):
                    with open(translated_path, "rb") as f:
                        start_response("200 OK", [('Content-type', '{}'.format(mimetypes.guess_type(translated_path)[0] or "application/octet-stream"))])
                        return [f.read()]

        return self._wsgi.__call__(environ, start_response)


class HttpD(mini_buildd.httpd.HttpD):
    def __init__(self, wsgi_app):
        super().__init__(["tcp6", "tcp"])

        self.app = StaticAndWSGI(wsgi_app)
        self.server = wsgiref.simple_server.make_server("", int(self._endpoints[0].option("port")), self.app)

        self._add_routes()

    def _add_route(self, route, directory, with_index=False, match="", with_doc_missing_error=False):  # pylint: disable=unused-argument
        self.app.add_route(route, directory)

    def run(self):
        self.server.serve_forever()
