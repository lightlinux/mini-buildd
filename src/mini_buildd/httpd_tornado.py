# -*- coding: utf-8 -*-

import logging
import asyncio

import tornado
import tornado.wsgi
import tornado.web
import tornado.httpserver
import tornado.platform.asyncio

import mini_buildd.misc
import mini_buildd.httpd

LOG = logging.getLogger(__name__)


class HttpD(mini_buildd.httpd.HttpD):
    def _add_route(self, route, directory, with_index=False, match=".*", with_doc_missing_error=False):  # pylint: disable=unused-argument
        # NOT IMPL: with_index, match, with_doc_missing_error
        self.tornado_app.add_handlers(
            r".*",  # match any host
            [
                (
                    r"/{route}/({match})".format(route=route, match=".*"),
                    tornado.web.StaticFileHandler,
                    dict(path=directory)
                ),
            ])

    def __init__(self, wsgi_app):
        super().__init__(["ssl", "tcp6", "tcp"])

        self.wsgi_container = tornado.wsgi.WSGIContainer(wsgi_app)
        self.tornado_app = tornado.web.Application()

        # Note: This backend only supports one endpoint.
        if self._endpoints[0].type in ["ssl"]:
            self.server = tornado.httpserver.HTTPServer(self.tornado_app, ssl_options={"certfile": self._endpoints[0].option("certKey"),
                                                                                       "keyfile": self._endpoints[0].option("privateKey")})
        elif self._endpoints[0].type in ["tcp6", "tcp"]:
            self.server = tornado.httpserver.HTTPServer(self.tornado_app)
        # Generic
        self._add_routes()
        self.tornado_app.add_handlers(
            r".*",  # match any host
            [
                ('.*', tornado.web.FallbackHandler, dict(fallback=self.wsgi_container)),
            ])

    def run(self):
        asyncio.set_event_loop(asyncio.new_event_loop())  # See: https://github.com/tornadoweb/tornado/issues/2183
        self.server.listen(int(self._endpoints[0].option("port")))
        tornado.ioloop.IOLoop.instance().start()
