# -*- coding: utf-8 -*-

import re
import logging

import twisted.internet.reactor
import twisted.internet.endpoints
import twisted.web.wsgi
import twisted.web.static
import twisted.web.resource
import twisted.python.log

import mini_buildd.misc
import mini_buildd.httpd

LOG = logging.getLogger(__name__)


class Site(twisted.web.server.Site):
    """Twisted Suite allowing access log via python logger.

    twisted Site class already allows to give a path for the access
    log, but no means to rotate. Essentially, this class merely exists
    to be able to make use of python's RotatingFileHandler (see
    httpd.py).
    """
    def __init__(self, access_log, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._access_log = access_log
        self._access_log.setLevel(logging.INFO)

    def log(self, request):
        line = self._logFormatter(self._logDateTime, request)  # This is the original line used in twisted Site class.
        self._access_log.info(line)


class RootResource(twisted.web.resource.Resource):
    """
    Twisted root resource needed to mix static and wsgi resources.
    """
    def __init__(self, wsgi_resource):
        super().__init__()
        self._wsgi_resource = wsgi_resource

    def getChild(self, path, request):
        request.prepath.pop()
        request.postpath.insert(0, path)
        return self._wsgi_resource


class FileResource(twisted.web.static.File):
    """
    Twisted static resource enhanced with switchable index and regex matching support.
    """
    def __init__(self, *args, with_index=False, uri_regex=".*", **kwargs):
        super().__init__(*args, **kwargs)
        self.mbd_with_index = with_index
        self.mbd_uri_regex = re.compile(uri_regex)

    def directoryListing(self):
        if not self.mbd_with_index:
            return self.forbidden
        return super().directoryListing()

    def getChild(self, path, request):
        if not self.mbd_uri_regex.match(request.uri.decode("utf-8")):
            return self.forbidden
        child = super().getChild(path, request)
        child.mbd_with_index = self.mbd_with_index
        child.mbd_uri_regex = self.mbd_uri_regex
        return child


class HttpD(mini_buildd.httpd.HttpD):
    def _add_route(self, route, directory, with_index=False, uri_regex=".*", with_doc_missing_error=False):  # pylint: disable=unused-argument
        static = FileResource(with_index=with_index, uri_regex=uri_regex, path=directory)

        if with_doc_missing_error:
            static.childNotFound = twisted.web.resource.NoResource(self.DOC_MISSING_HTML)

        for k, v in self._mime_types.items():
            static.contentTypes[".{}".format(k)] = v
        self.resource.putChild(bytes(route, encoding=self._char_encoding), static)

    def __init__(self, wsgi_app):
        super().__init__(["ssl", "tcp6", "tcp", "unix"])

        # Logging
        twisted.python.log.PythonLoggingObserver(loggerName=__name__).start()

        # HTTP setup
        self.resource = RootResource(twisted.web.wsgi.WSGIResource(twisted.internet.reactor, twisted.internet.reactor.getThreadPool(), wsgi_app))  # pylint: disable=no-member
        self.site = Site(self._access_log, self.resource)

        for ep in self._endpoints:
            twisted.internet.endpoints.serverFromString(twisted.internet.reactor, ep.desc).listen(self.site)

        # Generic
        self._add_routes()

    def run(self):
        twisted.internet.reactor.run(installSignalHandlers=0)  # pylint: disable=no-member
