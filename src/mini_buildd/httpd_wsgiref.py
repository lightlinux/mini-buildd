# -*- coding: utf-8 -*-

import logging
import mimetypes
import wsgiref.validate
import wsgiref.simple_server
import wsgiref.util
import os
import email

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


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


class StaticAndWSGI(object):
    """
    Not for production: Very simple WSGI app, adding static routing and delivery via wsgi.
    """
    def __init__(self, wsgi):
        self._wsgi = wsgi
        self._static = {}

    def add_static(self, route, directory):
        self._static[route] = directory

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "/")
        for route, directory in self._static.items():
            if path_info.startswith("/{}".format(route)):
                translated_path = os.path.join(directory, path_info[1 + len(route):].strip("/"))
                LOG.info("Static deliver translation: {} -> {}".format(path_info, translated_path))

                if os.path.isdir(translated_path):
                    start_response("200 OK", [('Content-type', 'text/html; charset=utf-8')])
                    return [html_index(translated_path, path_info, "wsgiref")]
                if os.path.isfile(translated_path):
                    with open(translated_path, "rb") as f:
                        start_response("200 OK", [('Content-type', '{}'.format(mimetypes.guess_type(translated_path)[0] or "application/octet-stream"))])
                        return [f.read()]

        return self._wsgi.__call__(environ, start_response)


class Backend(mini_buildd.httpd.HttpD):
    def __init__(self, bind, wsgi_app):
        self.app = StaticAndWSGI(wsgi_app)
        self.server = wsgiref.simple_server.make_server('', mini_buildd.misc.HoPo(bind).port, self.app)
        super().__init__()

    def add_static(self, route, directory, with_index=False, match="", with_doc_missing_error=False):  # pylint: disable=unused-argument
        self.app.add_static(route, directory)

    def run(self):
        self.server.serve_forever()
