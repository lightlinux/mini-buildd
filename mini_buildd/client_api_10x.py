# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import sys
import pickle
import urllib2

import mini_buildd.misc


class Daemon(object):
    def _log(self, message):
        print("{host}: {m}".format(host=self.host, m=message), file=sys.stderr)

    def _log_daemon_messages(self, headers):
        "Stolen from mini-buildd-tool"
        msgs_header = "x-mini-buildd-message"
        for msg in [v for k, v in headers.items() if msgs_header == k[:len(msgs_header)]]:
            self._log("HTTP Header Message: {m}".format(host=self.host, m=mini_buildd.misc.b642u(msg)))

    def __init__(self, host, port="8066", proto="http"):
        self.host = host
        self.port = port
        self.proto = proto
        self.url = "{proto}://{host}:{port}".format(proto=proto, host=host, port=port)
        self.api_url = "{url}/mini_buildd/api".format(url=self.url)
        self.auto_confirm = True

    def set_auto_confirm(self, confirm=True):
        self.auto_confirm = confirm

    def django_pseudo_configure(self):
        """
        This is needed (to be called once) to properly unpickle python instances from API calls that actually deliver model instances.
        """
        import django
        import django.conf
        import django.core.management
        import mini_buildd.models

        django.conf.settings.configure(
            DATABASES = {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=["mini_buildd"])

        mini_buildd.models.import_all()
        django.setup()
        django.core.management.call_command("migrate", interactive=False, run_syncdb=True, verbosity=0)

    def login(self, user):
        "Login. Use the user's mini-buildd keyring for auth, like mini-buildd-tool."
        keyring = mini_buildd.misc.Keyring("mini-buildd")
        mini_buildd.misc.web_login("{host}:{port}".format(host=self.host, port=self.port), user, keyring)

    def call(self, command, args={}, output="python"):
        if self.auto_confirm:
            args["confirm"] = command
        http_get_args = "&".join("{k}={v}".format(k=k, v=v) for k, v in args.items())
        url = "{api_url}?command={command}&output={output}&{args}".format(api_url=self.api_url, command=command, output=output, args=http_get_args)
        self._log("API call URL: {}".format(url))

        try:
            response = urllib2.urlopen(url)
            self._log("HTTP Status: {status}".format(status=response.getcode()))
            if output == "python":
                return pickle.loads(response.read())
            else:
                return response.read()
        except urllib2.HTTPError as e:
            self._log_daemon_messages(e.headers)

    def _bulk_migrate(self, packages, repositories=None, codenames=None, suites=None):
        status = self.call("status")

        if repositories is None:
            repositories = list(status.repositories.keys())
        if suites is None:
            suites = ["unstable", "testing"]

        for package in packages:
            for repository in repositories:
                iter_codenames = codenames
                if iter_codenames is None:
                    iter_codenames = list(status.repositories[repository])
                for codename in iter_codenames:
                    for suite in suites:
                        dist = "{c}-{r}-{s}".format(c=codename, r=repository, s=suite)
                        self.call("migrate", {"package": package, "distribution": dist})
