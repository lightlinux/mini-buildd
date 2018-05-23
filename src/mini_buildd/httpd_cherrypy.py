# -*- coding: utf-8 -*-

import os
import re
import stat
import logging

import cherrypy
import cherrypy.lib.cptools
import cherrypy.lib.httputil
import cherrypy.lib.static

import mini_buildd.misc
import mini_buildd.setup
import mini_buildd.httpd

LOG = logging.getLogger(__name__)


class Backend(mini_buildd.httpd.HttpD):
    class StaticWithIndex(cherrypy._cptools.HandlerTool):  # pylint: disable=protected-access
        @classmethod
        def _mbd_serve_index(cls, _section, directory, root="", match="", **_kwargs):
            if match and not re.search(match, cherrypy.request.path_info):
                raise cherrypy.HTTPError(403, "Requested path does not match allowed regex.")

            # Compute absolute directory path to serve
            path = os.path.realpath(os.path.join(root, directory, cherrypy.request.path_info.lstrip("/")))

            if not path.startswith(os.path.normpath(root)):
                raise cherrypy.HTTPError(403, "Requested path outside root directory.")

            # Check that the path actually exists
            try:
                path_stat = os.stat(path)
            except OSError:
                # This will trigger a 404
                return False

            if stat.S_ISDIR(path_stat.st_mode):
                cherrypy.request.is_index = True

                # Set last modified, and call validate_since
                # This may return a "304 Not Modified" in case the client did a condition GET
                cherrypy.response.headers["Last-Modified"] = cherrypy.lib.httputil.HTTPDate(path_stat.st_mtime)
                cherrypy.lib.cptools.validate_since()

                # Check for trailing "/" (without, browser will use wrong URLs for entries)
                # This may return a "301 Moved Permanently" with Location dir + "/"
                cherrypy.lib.cptools.trailing_slash()

                # Produce and deliver a new index
                cherrypy.response.body = mini_buildd.httpd.HtmlIndex.html_index(path, cherrypy.request.path_info, "CherryPy {cp_version}".format(cp_version=cherrypy.__version__))
                cherrypy.response.headers["Content-Type"] = "text/html"
                return True

            return False

        @classmethod
        def _mbd_serve(cls, section, directory, **kwargs):
            "Try cherrypy static serve, fallback to . Try built-in static dir first"
            if cherrypy.lib.static.staticdir(section, directory, **kwargs):
                return True
            return cls._mbd_serve_index(section, directory, **kwargs)

        def __init__(self):
            super().__init__(self._mbd_serve)

    @classmethod
    def _error_doc_missing(cls, status, message, traceback, version):  # Exact arg names needed (cherrypy calls back with named arguments)  # pylint: disable=unused-argument
        return cls.DOC_MISSING_HTML_TEMPLATE.format(status=status)

    def add_static(self, route, directory, with_index=False, match="", with_doc_missing_error=False):
        "Shortcut to add a static handler."
        mime_text_plain = "text/plain; charset={charset}".format(charset=mini_buildd.setup.CHAR_ENCODING)

        ht = self.StaticWithIndex() if with_index else cherrypy.tools.staticdir

        cherrypy.tree.mount(
            ht.handler("/",
                       "",
                       root=directory,
                       match=match,
                       content_types={"log": mime_text_plain,
                                      "buildlog": mime_text_plain,
                                      "changes": mime_text_plain,
                                      "dsc": mime_text_plain}),
            "/{}/".format(route),  # cherrpy needs '/xyz/' notation!
            config={"/": {"error_page.default": self._error_doc_missing} if with_doc_missing_error else {}})

    def __init__(self, bind, wsgi_app):
        """
        Construct the CherryPy WSGI Web Server.

        :param bind: the bind address to use.
        :type bind: string
        :param wsgi_app: the web application to process.
        :type wsgi_app: WSGI-application

        """

        debug = "http" in mini_buildd.setup.DEBUG
        cherrypy.config.update({"server.socket_host": str(mini_buildd.misc.HoPo(bind).host),
                                "server.socket_port": mini_buildd.misc.HoPo(bind).port,
                                "engine.autoreload.on": False,
                                "checker.on": debug,
                                "tools.log_headers.on": debug,
                                "request.show_tracebacks": debug,
                                "request.show_mismatched_params": debug,
                                "log.error_file": None,
                                "log.access_file": None,
                                "log.screen": debug and mini_buildd.setup.FOREGROUND})

        # Redirect cherrypy's error log to mini-buildd's logging
        cherrypy.engine.subscribe("log", lambda msg, level: LOG.log(level, "CherryPy: {m}".format(m=msg)))

        # Set up a rotating file handler for cherrypy's access log
        handler = logging.handlers.RotatingFileHandler(
            mini_buildd.setup.ACCESS_LOG_FILE,
            maxBytes=5000000,
            backupCount=9,
            encoding="UTF-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(cherrypy._cplogging.logfmt)  # pylint: disable=protected-access
        cherrypy.log.access_log.addHandler(handler)

        # Register wsgi app (django)
        cherrypy.tree.graft(wsgi_app)

        super().__init__()

    def run(self):
        """
        Run the CherryPy WSGI Web Server.
        """
        cherrypy.engine.start()
        cherrypy.engine.block()
