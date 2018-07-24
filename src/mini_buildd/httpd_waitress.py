# -*- coding: utf-8 -*-

import waitress

import mini_buildd.httpd


class HttpD(mini_buildd.httpd.HttpD):
    def _add_route(self, route, directory, with_index=False, match="", with_doc_missing_error=False):  # pylint: disable=unused-argument
        self.app.add_route(route, directory)

    def __init__(self, wsgi_app):
        super().__init__(["tcp6", "tcp"])
        self.app = mini_buildd.httpd.WSGIWithRoutes(wsgi_app)
        self._add_routes()

    def run(self):
        # Note: Seems we can't use an explicit IPv6 type for the 'host=' arg. However, not specifying means binding on both.
        waitress.serve(self.app, port=int(self._endpoints[0].option("port")))
