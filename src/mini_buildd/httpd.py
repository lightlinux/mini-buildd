# -*- coding: utf-8 -*-

import os
import re
import stat
import email.utils
import logging

import cherrypy
import cherrypy.lib.cptools
import cherrypy.lib.httputil
import cherrypy.lib.static

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class StaticWithIndex(cherrypy._cptools.HandlerTool):  # pylint: disable=protected-access
    _TABLE_HEADER = """\
<tr>
 <th style="text-align: left;">Name</th>
 <th style="text-align: left; padding: 0px 15px 0px 15px">Last modified</th>
 <th style="text-align: right;">Size</th>
</tr>"""

    _TABLE_SEPARATOR = """\
<tr><th colspan="3"><hr /></th></tr>"""

    _TABLE_ROW = """\
<tr>
 <td style="text-align: left;"><a href="{name}" title="{name}"><kbd>{name}</kbd></a></td>
 <td style="text-align: left; padding: 0px 15px 0px 15px"><kbd><em>{mod}</em></kbd></td>
 <td style="text-align: right;"><kbd>{size}</kbd></td>
</tr>"""

    @classmethod
    def _mbd_html_index(cls, directory):
        "Generate a directory index as html."

        def table_rows(directory):
            "Return an array of strings formatted as html table rows for all directory entries."
            result = []

            def add(path, entry, as_dir):
                entry_path = os.path.join(path, entry)
                result.append(cls._TABLE_ROW.format(name=entry + ("/" if as_dir else ""),
                                                    mod=email.utils.formatdate(os.path.getmtime(entry_path)),
                                                    size="DIR" if as_dir else os.path.getsize(entry_path)))

            # Only walk one step
            path, dirs, files = next(os.walk(directory))
            # Dirs first, and sort entries by name
            for entry in sorted(dirs):
                add(path, entry, True)

            for entry in sorted(files):
                add(path, entry, False)

            return result

        return bytes("""\
<!DOCTYPE html>

<html>
 <head>
  <title>Index of {path}</title>
 </head>
 <body>
  <h1>Index of {path}</h1>
  <table>
   {table_header}
   {table_separator}
   {table_parent}
   {table_rows}
   {table_separator}
  </table>
 <address>mini-buildd {mbd_version} (CherryPy {cp_version})</address>
 </body>
</html>
""".format(path=cherrypy.request.path_info,
           table_header=cls._TABLE_HEADER,
           table_separator=cls._TABLE_SEPARATOR,
           table_parent=cls._TABLE_ROW.format(name="../", mod="&nbsp;", size="PARENT"),
           table_rows="\n".join(table_rows(directory.rstrip(r"\/"))),
           mbd_version=mini_buildd.__version__,
           cp_version=cherrypy.__version__), encoding=mini_buildd.setup.CHAR_ENCODING)

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
            cherrypy.response.body = cls._mbd_html_index(path)
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


def run(bind, wsgi_app):
    """
    Run the CherryPy WSGI Web Server.

    :param bind: the bind address to use.
    :type bind: string
    :param wsgi_app: the web application to process.
    :type wsgi_app: WSGI-application

    """

    def _error_manual_missing(status, message, traceback, version):  # Exact arg names needed (cherrypy calls back with named arguments)  # pylint: disable=unused-argument
        return "<html><body><h1>Online Manual Not Available ({})</h1>Package <b><tt>mini-buildd-doc</tt></b> needs to be installed to make the manual available.</body></html>".format(status)

    def add_static_handler(path, root, with_index=False, match="", config=None):
        "Shortcut to add a static handler."
        mime_text_plain = "text/plain; charset={charset}".format(charset=mini_buildd.setup.CHAR_ENCODING)

        ht = StaticWithIndex() if with_index else cherrypy.tools.staticdir

        cherrypy.tree.mount(
            ht.handler("/",
                       "",
                       root=root,
                       match=match,
                       content_types={"log": mime_text_plain,
                                      "buildlog": mime_text_plain,
                                      "changes": mime_text_plain,
                                      "dsc": mime_text_plain}),
            path,
            config={"/": config if config else {}})

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

    # Serve mini_buildd webapp's static directory
    add_static_handler(mini_buildd.setup.STATIC_URL,
                       "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

    # Serve django admin webapp's static directory.
    # Note: 'STATIC_URL' has trailing "/"; cherrypy does not like double slashes inside path like add_static_handler("/my//path").
    add_static_handler("{p}admin/".format(p=mini_buildd.setup.STATIC_URL),
                       "{p}/django/contrib/admin/static/admin".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

    # Serve mini-buildd's HTML manual
    add_static_handler("/doc/",
                       mini_buildd.setup.MANUAL_DIR,
                       config={"error_page.default": _error_manual_missing})

    # Serve repositories with index support
    add_static_handler("/repositories/",
                       mini_buildd.setup.REPOSITORIES_DIR,
                       with_index=True,
                       match=r"^/.+/(pool|dists)/.*")

    # Serve logs with index support
    add_static_handler("/log/",
                       mini_buildd.setup.LOG_DIR,
                       with_index=True,
                       match=r"^/.+/.*")

    # Register wsgi app (django)
    cherrypy.tree.graft(wsgi_app)

    # Finally, start server
    cherrypy.engine.start()
    cherrypy.engine.block()
