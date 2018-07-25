# -*- coding: utf-8 -*-

import abc
import logging
import os
import email
import mimetypes

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class HttpD(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def _add_route(self, route, directory, with_index=False, match="", with_doc_missing_error=False):
        "Serve static files from a directory."
        pass

    def __init__(self, supported_types):
        self._doc_missing_html_template = """\
<html><body>
<h1>{status} (<tt>mini-buildd-doc</tt> not installed?)</h1>
Maybe package <b><tt>mini-buildd-doc</tt></b> needs to be installed to make the manual available.
</body></html>
"""
        self._debug = "http" in mini_buildd.setup.DEBUG
        self._foreground = mini_buildd.setup.FOREGROUND
        self._access_log_file = mini_buildd.setup.ACCESS_LOG_FILE
        self._char_encoding = mini_buildd.setup.CHAR_ENCODING
        self._mime_text_plain = "text/plain; charset={charset}".format(charset=self._char_encoding)
        self._mime_types = {"log": self._mime_text_plain,
                            "buildlog": self._mime_text_plain,
                            "changes": self._mime_text_plain,
                            "dsc": self._mime_text_plain}
        self._endpoints = mini_buildd.setup.HTTPD_ENDPOINTS
        for ep in self._endpoints:
            if ep.type not in supported_types:
                raise Exception("HTTPd backend does not support network endpoint type: {}".format(self._endpoints[0].type))

    def _add_routes(self):
        self._add_route("static", "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))                      # WebApp static directory
        self._add_route("doc", mini_buildd.setup.MANUAL_DIR, with_doc_missing_error=True)                                    # HTML manual
        self._add_route("repositories", mini_buildd.setup.REPOSITORIES_DIR, with_index=True, match=r"^/.+/(pool|dists)/.*")  # Repositories
        self._add_route("log", mini_buildd.setup.LOG_DIR, with_index=True, match=r"^/.+/.*")                                 # Logs

    @abc.abstractmethod
    def run(self):
        "Run the HTTP server. Must be implemented by backend."
        pass


# Helpers
def html_index(directory, path_info, backend_info):
    "Generate a directory index as html (fallback for backends that do not support indexes)."

    table_row_tpl = """\
<tr>
 <td style="text-align: left;"><a href="{name}" title="{name}"><kbd>{name}</kbd></a></td>
 <td style="text-align: left; padding: 0px 15px 0px 15px"><kbd><em>{mod}</em></kbd></td>
 <td style="text-align: right;"><kbd>{size}</kbd></td>
</tr>"""

    def table_rows(directory):
        "Return an array of strings formatted as html table rows for all directory entries."
        result = []

        def add(path, entry, as_dir):
            entry_path = os.path.join(path, entry)
            result.append(table_row_tpl.format(name=entry + ("/" if as_dir else ""),
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
  <title>Index of {path_info}</title>
 </head>
 <body>
  <h1>Index of {path_info}</h1>
   <table>
    <tr>
    <th style="text-align: left;">Name</th>
    <th style="text-align: left; padding: 0px 15px 0px 15px">Last modified</th>
    <th style="text-align: right;">Size</th>
    </tr>
    {table_separator}
    {table_parent}
    {table_rows}
    {table_separator}
   </table>
  <address>mini-buildd {mbd_version} ({backend_info})</address>
 </body>
</html>
""".format(path_info=path_info,
           table_separator="<tr><th colspan=\"3\"><hr /></th></tr>",
           table_parent=table_row_tpl.format(name="../", mod="&nbsp;", size="PARENT"),
           table_rows="\n".join(table_rows(directory.rstrip(r"\/"))),
           mbd_version=mini_buildd.__version__,
           backend_info=backend_info), encoding=mini_buildd.setup.CHAR_ENCODING)


class WSGIWithRoutes(object):
    """
    Simple WSGI helper app that also will also handle very basic static delivery.
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
