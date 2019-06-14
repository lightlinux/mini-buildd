import re
import os.path
import logging

import twisted.internet.reactor
import twisted.internet.endpoints
import twisted.web.wsgi
import twisted.web.static
import twisted.web.resource
import twisted.logger
import twisted.python.logfile

import mini_buildd.misc
import mini_buildd.httpd

LOG = logging.getLogger(__name__)


class Site(twisted.web.server.Site):
    def _openLogFile(self, path):
        return twisted.python.logfile.LogFile(os.path.basename(path), directory=os.path.dirname(path), rotateLength=5000000, maxRotatedFiles=9)


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
        super().__init__()

        # Bend twisted (not access.log) logging to ours
        twisted.logger.globalLogPublisher.addObserver(twisted.logger.STDLibLogObserver(name=__name__))

        # HTTP setup
        self.resource = RootResource(twisted.web.wsgi.WSGIResource(twisted.internet.reactor, twisted.internet.reactor.getThreadPool(), wsgi_app))  # pylint: disable=no-member
        self.site = Site(self.resource, logPath=mini_buildd.setup.ACCESS_LOG_FILE)

        for ep in self._endpoints:
            twisted.internet.endpoints.serverFromString(twisted.internet.reactor, ep.desc).listen(self.site)

        # Generic
        self._add_routes()

    def run(self):
        twisted.internet.reactor.run(installSignalHandlers=0)  # pylint: disable=no-member
