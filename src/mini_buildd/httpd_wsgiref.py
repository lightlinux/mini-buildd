# -*- coding: utf-8 -*-

import wsgiref.simple_server

import mini_buildd.httpd


class HttpD(mini_buildd.httpd.HttpD):
    def __init__(self, wsgi_app):
        super().__init__(["tcp6", "tcp"])

        self.app = mini_buildd.httpd.WSGIWithRoutes(wsgi_app)
        self.server = wsgiref.simple_server.make_server("", int(self._endpoints[0].option("port")), self.app)

        self._add_routes()

    def _add_route(self, route, directory, with_index=False, match="", with_doc_missing_error=False):  # pylint: disable=unused-argument
        self.app.add_route(route, directory)

    def run(self):
        self.server.serve_forever()
