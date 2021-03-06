import os
import pickle
import logging

import django.core.exceptions
import django.http
import django.shortcuts
import django.template
import django.views.generic.base

import mini_buildd.daemon

import mini_buildd.models.gnupg
import mini_buildd.models.repository
import mini_buildd.models.chroot

from mini_buildd.models.msglog import MsgLog
LOG = logging.getLogger(__name__)


class AccountProfileView(django.views.generic.base.TemplateView):
    """Add repositories to context."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"repositories": mini_buildd.models.repository.Repository.mbd_get_prepared()})
        return context


def _add_messages(response, msgs):
    """Add all texts in messages (must be one line each) as custom HTTP headers (using base64 with UTF-8 char encoding)."""
    n = 0
    for msg in msgs:
        response["X-Mini-Buildd-Message-{n}".format(n=n)] = mini_buildd.misc.u2b64(msg)
        n += 1


def _add_api_messages(response, api_cmd, msgs=None):
    """Add all user messages from api_cmd, plus optional extra messages."""
    _add_messages(response,
                  (api_cmd.msglog.plain.splitlines()[::-1] if api_cmd else [])
                  + (msgs if msgs else []))


def _referer(request, output):
    """output=referer[REFERER_URL]."""
    return output[7:] if (output[:7] == "referer" and output[7:]) else request.META.get("HTTP_REFERER", "/")


def error(request, code, meaning, description, api_cmd=None):
    # Note: Adding api_cmd if applicable; this will enable automated api links even on error pages.
    MsgLog(LOG, request).error("{code} {meaning}: {description}".format(code=code, meaning=meaning, description=description))
    output = request.GET.get("output", "html")
    if output[:7] == "referer":
        response = django.shortcuts.redirect(_referer(request, output))
    else:
        response = django.shortcuts.render(request,
                                           "mini_buildd/error.html",
                                           {"code": code,
                                            "meaning": meaning,
                                            "description": description,
                                            "api_cmd": api_cmd},
                                           status=code)
    _add_api_messages(response, api_cmd, ["E: {d}".format(d=description)])
    return response


def error400_bad_request(request, description="Bad request", api_cmd=None):
    return error(request,
                 400,
                 "Bad Request",
                 description,
                 api_cmd)


def error401_unauthorized(request, description="Missing authorization", api_cmd=None):
    return error(request,
                 401,
                 "Unauthorized",
                 description,
                 api_cmd)


def error404_not_found(request, description="The requested resource could not be found", api_cmd=None):
    return error(request,
                 404,
                 "Not Found",
                 description,
                 api_cmd)


def error405_method_not_allowed(request, description="The resource does not allow this request", api_cmd=None):
    return error(request,
                 405,
                 "Method Not Allowed",
                 description,
                 api_cmd)


def error500_internal(request, description="Sorry, something went wrong", api_cmd=None):
    return error(request,
                 500,
                 "Internal Server Error",
                 description,
                 api_cmd)


def home(request):
    return django.shortcuts.render(request,
                                   "mini_buildd/home.html",
                                   {"daemon": mini_buildd.daemon.get(),
                                    "repositories": mini_buildd.models.repository.Repository.mbd_get_active_or_auto_reactivate(),
                                    "chroots": mini_buildd.models.chroot.Chroot.mbd_get_active_or_auto_reactivate(),
                                    "remotes": mini_buildd.models.gnupg.Remote.mbd_get_active_or_auto_reactivate()})


def log(request, repository, package, version):
    def get_logs(installed):
        pkg_log = mini_buildd.misc.PkgLog(repository, installed, package, version)
        result = {"changes": None,
                  "changes_path": None,
                  "buildlogs": dict((k, pkg_log.make_relative(v)) for k, v in pkg_log.buildlogs.items())}
        if pkg_log.changes:
            with mini_buildd.misc.open_utf8(pkg_log.changes) as cf:
                result["changes"] = cf.read()
            result["changes_path"] = pkg_log.make_relative(pkg_log.changes)
        return result

    return django.shortcuts.render(request,
                                   "mini_buildd/log.html",
                                   {"repository": repository,
                                    "package": package,
                                    "version": version,
                                    "logs": [("Installed", get_logs(installed=True)),
                                             ("Failed", get_logs(installed=False))]})


LIVE_BUILDLOGS_404 = """\
This live buildlog is not yet (or no longer) available.

Please just retry later if this build is currently pending.
"""


def live_buildlogs(_request, logfile):
    buildlog = os.path.join(mini_buildd.config.SPOOL_DIR, logfile)
    if not os.path.exists(buildlog):
        return django.http.HttpResponse(LIVE_BUILDLOGS_404, content_type="text/plain")
    return django.http.FileResponse(open(buildlog, "rb"), content_type="text/plain")


def api(request):  # pylint: disable=too-many-return-statements,too-many-branches
    api_cmd = None
    try:
        if request.method != 'GET':
            return error400_bad_request(request, "API: Allows GET requests only")

        # Call API index if called with no argument
        if not request.GET:
            return django.shortcuts.render(request,
                                           "mini_buildd/api_index.html",
                                           {"COMMANDS": mini_buildd.api.COMMANDS_DEFAULTS,
                                            "COMMAND_GROUP": mini_buildd.api.COMMAND_GROUP})

        # Get API class from 'command' parameter
        command = request.GET.get("command", None)
        if command not in mini_buildd.api.COMMANDS_DICT:
            return error400_bad_request(request, "API: Unknown command '{c}'".format(c=command))
        api_cls = mini_buildd.api.COMMANDS_DICT[command]

        # Authentication
        auth_err = api_cls.auth_err(request.user)
        if auth_err:
            return error401_unauthorized(request, auth_err)

        # Generate command object
        api_cmd = api_cls(request.GET, daemon=mini_buildd.daemon.get(), request=request, msglog=MsgLog(LOG, request))

        # HTML output by default
        output = request.GET.get("output", "html")

        # Check if we need a running daemon
        if api_cls.NEEDS_RUNNING_DAEMON and not mini_buildd.daemon.get().is_running():
            return error405_method_not_allowed(request, "API: '{c}': Needs running daemon".format(c=command))

        # Check confirmable calls
        if api_cls.CONFIRM and request.GET.get("confirm", None) != command:
            if output in ("html", "referer"):
                return django.shortcuts.render(request,
                                               "mini_buildd/api_confirm.html",
                                               {"api_cmd": api_cmd,
                                                "referer": _referer(request, output)})
            return error401_unauthorized(request, "API: '{c}': Needs to be confirmed".format(c=command))

        # Show api command name and user calling it.
        api_cmd.msglog.info("API call '{c}' by user '{u}'".format(c=command, u=request.user))

        # Run API call (dep-injection via daemon object)
        api_cmd.run()

        # Generate API call output
        response = None
        if output == "html":
            response = django.shortcuts.render(request,
                                               ["mini_buildd/api_{c}.html".format(c=command),
                                                "mini_buildd/api_default.html".format(c=command)],
                                               {"api_cmd": api_cmd,
                                                "repositories": mini_buildd.models.repository.Repository.mbd_get_prepared()})

        elif output == "plain":
            response = django.http.HttpResponse(api_cmd.__str__().encode(mini_buildd.config.CHAR_ENCODING),
                                                content_type="text/plain; charset={charset}".format(charset=mini_buildd.config.CHAR_ENCODING))

        elif output == "python":
            response = django.http.HttpResponse(pickle.dumps(api_cmd, pickle.HIGHEST_PROTOCOL),
                                                content_type="application/python-pickle")

        elif output[:7] == "referer":
            # Add all plain result lines as info messages on redirect
            for line in api_cmd.__str__().splitlines():
                api_cmd.msglog.info("Result: {line}".format(line=line))
            response = django.shortcuts.redirect(_referer(request, output))
        else:
            response = django.http.HttpResponseBadRequest("<h1>Unknown output type '{o}'</h1>".format(o=output))

        # Add all user messages as as custom HTTP headers
        _add_api_messages(response, api_cmd)

        return response

    except BaseException as e:
        # This might as well be just an internal error; in case of no bug in the code, 405 fits better though.
        # ['wontfix' unless we refactor to diversified exception classes]
        mini_buildd.config.log_exception(LOG, "API call error", e)
        return error405_method_not_allowed(request, "API call error: {e}".format(e=e), api_cmd=api_cmd)
