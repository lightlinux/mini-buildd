# -*- coding: utf-8 -*-

import logging

import twisted.internet.reactor
import twisted.internet.endpoints
import twisted.web.wsgi
import twisted.web.static
import twisted.web.resource
import twisted.python.log

import mini_buildd.misc
import mini_buildd.setup

LOG = logging.getLogger(__name__)


class Backend(mini_buildd.httpd.HttpD):
    class RootResource(twisted.web.resource.Resource):
        """
        For some reason, twisted 'WSGIResource' cannot act as a root resource, so this workaround is needed
        """
        def __init__(self, wsgi_resource):
            super().__init__()
            self._wsgi_resource = wsgi_resource

        def getChild(self, path, request):
            request.prepath.pop()
            request.postpath.insert(0, path)
            return self._wsgi_resource

    def add_static(self, route, directory, with_index=False, match="", with_doc_missing_error=False):  # pylint: disable=unused-argument
        # NOT IMPL: with_index, match, with_doc_missing_error
        self.resource.putChild(bytes(route, encoding=mini_buildd.setup.CHAR_ENCODING), twisted.web.static.File(directory))

    def __init__(self, bind, wsgi_app):
        # Logging
        twisted.python.log.PythonLoggingObserver(loggerName=__name__).start()

        # HTTP setup
        self.resource = self.RootResource(twisted.web.wsgi.WSGIResource(twisted.internet.reactor, twisted.internet.reactor.getThreadPool(), wsgi_app))  # pylint: disable=no-member
        self.site = twisted.web.server.Site(self.resource)
        self.endpoint = twisted.internet.endpoints.TCP4ServerEndpoint(twisted.internet.reactor, mini_buildd.misc.HoPo(bind).port)
        self.endpoint.listen(self.site)

        # Generic
        super().__init__()

    def run(self):
        twisted.internet.reactor.run(installSignalHandlers=0)  # pylint: disable=no-member
