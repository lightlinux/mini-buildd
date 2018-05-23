# -*- coding: utf-8 -*-

import abc
import os
import email
import logging

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class HtmlIndex(object):
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
    def html_index(cls, directory, path_info, backend_info):
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
        <title>Index of {path_info}</title>
 </head>
 <body>
        <h1>Index of {path_info}</h1>
  <table>
   {table_header}
   {table_separator}
   {table_parent}
   {table_rows}
   {table_separator}
  </table>
 <address>mini-buildd {mbd_version} ({backend_info})</address>
 </body>
</html>
""".format(path_info=path_info,
           table_header=cls._TABLE_HEADER,
           table_separator=cls._TABLE_SEPARATOR,
           table_parent=cls._TABLE_ROW.format(name="../", mod="&nbsp;", size="PARENT"),
           table_rows="\n".join(table_rows(directory.rstrip(r"\/"))),
           mbd_version=mini_buildd.__version__,
           backend_info=backend_info), encoding=mini_buildd.setup.CHAR_ENCODING)


class HttpD(metaclass=abc.ABCMeta):
    DOC_MISSING_HTML_TEMPLATE = """\
<html><body>
<h1>{status} (<tt>mini-buildd-doc</tt> not installed?)</h1>
Maybe package <b><tt>mini-buildd-doc</tt></b> needs to be installed to make the manual available.
</body></html>
"""

    @abc.abstractmethod
    def add_static(self, route, directory, with_index=False, match="", with_doc_missing_error=False):
        "Serve static files from a directory."
        pass

    @abc.abstractmethod
    def __init__(self, bind, wsgi_app):
        """
        Setup HTTP server.

        :param bind: the bind address to use.
        :type bind: string
        :param wsgi_app: the web application to process.
        :type wsgi_app: WSGI-application
        """
        pass

    def __init__(self):
        # Serve mini_buildd webapp's static directory
        self.add_static("static",
                        "{p}/mini_buildd/static".format(p=mini_buildd.setup.PY_PACKAGE_PATH))

        # Serve mini-buildd's HTML manual
        self.add_static("doc",
                        mini_buildd.setup.MANUAL_DIR,
                        with_doc_missing_error=True)

        # Serve repositories with index support
        self.add_static("repositories",
                        mini_buildd.setup.REPOSITORIES_DIR,
                        with_index=True,
                        match=r"^/.+/(pool|dists)/.*")

        # Serve logs with index support
        self.add_static("log",
                        mini_buildd.setup.LOG_DIR,
                        with_index=True,
                        match=r"^/.+/.*")

    @abc.abstractmethod
    def run(self):
        "Run the HTTP server. Must be implemented by backend."
        pass


def run(bind, wsgi_app):
    """
    Run the Web Server.

    :param bind: the bind address to use.
    :type bind: string
    :param wsgi_app: the web application to process.
    :type wsgi_app: WSGI-application

    """
    from mini_buildd.httpd_cherrypy import Backend
    Backend(bind, wsgi_app).run()
