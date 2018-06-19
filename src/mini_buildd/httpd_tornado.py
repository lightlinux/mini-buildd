# -*- coding: utf-8 -*-

import logging
import asyncio

import tornado
import tornado.wsgi
import tornado.web
import tornado.httpserver
import tornado.platform.asyncio

import mini_buildd.misc
import mini_buildd.setup
import mini_buildd.httpd

LOG = logging.getLogger(__name__)


class HttpD(mini_buildd.httpd.HttpD):
    def add_static(self, route, directory, with_index=False, match=".*", with_doc_missing_error=False):  # pylint: disable=unused-argument
        # NOT IMPL: with_index, match, with_doc_missing_error
        self.tornado_app.add_handlers(
            r".*",  # match any host
            [
                (
                    r"/{route}/({match})".format(route=route, match=match),
                    tornado.web.StaticFileHandler,
                    dict(path=directory)
                ),
            ])

    def __init__(self, bind, wsgi_app):
        self.wsgi_container = tornado.wsgi.WSGIContainer(wsgi_app)
        self.tornado_app = tornado.web.Application()
        self.server = tornado.httpserver.HTTPServer(self.tornado_app)

        # Generic
        super().__init__()
        self.tornado_app.add_handlers(
            r".*",  # match any host
            [
                ('.*', tornado.web.FallbackHandler, dict(fallback=self.wsgi_container)),
            ])

    def run(self):
        asyncio.set_event_loop(asyncio.new_event_loop())  # See: https://github.com/tornadoweb/tornado/issues/2183
        self.server.listen(8066)
        tornado.ioloop.IOLoop.instance().start()
