# -*- coding: utf-8 -*-

import os
import logging
import string
import random

import django
import django.utils.safestring
import mini_buildd
import mini_buildd.api


LOG = logging.getLogger(__name__)

register = django.template.Library()


@register.filter
def mbd_dict_get(dict_, key):
    return dict_.get(key)


@register.filter
def mbd_dirname(path):
    return os.path.dirname(path)


@register.simple_tag
def mbd_jquery_path():
    return "admin/js/vendor/jquery/jquery.js"


@register.simple_tag
def mbd_title():
    return mini_buildd.daemon.get().get_title()


@register.filter
def mbd_daemon_is_running(_dummy):
    return mini_buildd.daemon.get().is_running()


@register.inclusion_tag("includes/mbd_model_count.html")
def mbd_model_count(model):
    ret = {}
    model_class = eval("mini_buildd.models.{m}".format(m=model))  # pylint: disable=eval-used
    if getattr(model_class, "mbd_is_prepared", None):
        # Status model
        ret["active"] = model_class.objects.filter(status__exact=model_class.STATUS_ACTIVE).count()
        ret["prepared"] = model_class.objects.filter(status__exact=model_class.STATUS_PREPARED).count()
        ret["removed"] = model_class.objects.filter(status__exact=model_class.STATUS_REMOVED).count()
    ret["total"] = model_class.objects.all().count()
    return ret


@register.inclusion_tag("includes/mbd_packager_status.html")
def mbd_packager_status(packages):
    return {"packages": packages}


@register.inclusion_tag("includes/mbd_builder_status.html")
def mbd_builder_status(builds):
    return {"builds": builds}


@register.inclusion_tag("includes/mbd_manage_subscriptions.html")
def mbd_manage_subscriptions(repositories, package=""):
    return {"repositories": repositories,
            "package": package}


@register.inclusion_tag("includes/mbd_admin_index_table_header.html")
def mbd_admin_index_table_header(title):
    return {"title": title}


@register.inclusion_tag("includes/mbd_admin_index_table_row.html")
def mbd_admin_index_table_row(app,  # pylint: disable=too-many-arguments
                              model_name,
                              model_path,
                              hide_add=False,
                              wiz0_function="",
                              wiz0_name="",
                              wiz0_title="",
                              wiz1_function="",
                              wiz1_name="",
                              wiz1_title="",
                              wiz2_function="",
                              wiz2_name="",
                              wiz2_title=""):
    return {"app": app,
            "model_name": model_name,
            "model_path": model_path,
            "hide_add": hide_add,
            "wiz0_function": wiz0_function,
            "wiz0_name": wiz0_name,
            "wiz0_title": wiz0_title,
            "wiz1_function": wiz1_function,
            "wiz1_name": wiz1_name,
            "wiz1_title": wiz1_title,
            "wiz2_function": wiz2_function,
            "wiz2_name": wiz2_name,
            "wiz2_title": wiz2_title}


@register.inclusion_tag("includes/mbd_admin_auto_setup.html")
def mbd_admin_auto_setup():
    return {}


@register.inclusion_tag("includes/mbd_api_call.html")
def mbd_api_call(cmd, user, hidden=None, show_extra=True, name=None, title=None, **kwargs):
    def _kwargs(prefix):
        return {k[len(prefix):]: v for k, v in kwargs.items() if k.startswith(prefix)}

    api_cls = mini_buildd.api.COMMANDS_DICT.get(cmd, None)
    auth_err = api_cls.auth_err(user)
    values = _kwargs("value_")
    api_cmd = api_cls({**api_cls.get_default_args(), **values})

    hidden_params = values.keys() if hidden is None else hidden.split(",")
    show_params = [m for m in api_cmd.args_mandatory if m not in hidden_params]
    extra_params = [key for key in api_cmd.args.keys() if key not in show_params] if show_extra else []

    return {"api_cmd": api_cmd,
            "tag_id": "mbd-api-call-{}".format("".join(random.choices(string.ascii_lowercase + string.digits, k=16))),
            "hidden_params": hidden_params,
            "show_params": show_params,
            "extra_params": extra_params,
            "name": name,
            "title": title,
            "auth_err": auth_err}


def _mbd_e2n(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except BaseException as e:
        LOG.warning("Function failed: {f}: {e}".format(f=func, e=e))
        return None


def _mbd_distribution_options(repository, value_prefix="", **suiteoption_filter):
    result = ""
    for d in repository.mbd_distribution_strings(**suiteoption_filter):
        result += '<option value="{p}{d}">{d}</option>'.format(p=value_prefix, d=d)
    return django.utils.safestring.mark_safe(result)


@register.simple_tag
def mbd_distribution_options(repository, value_prefix="", **suiteoption_filter):
    "Generate 'option' HTML tags for 'select' form input for distribution strings."
    return _mbd_e2n(_mbd_distribution_options, repository, value_prefix=value_prefix, **suiteoption_filter)


@register.simple_tag
def mbd_distribution_apt_line(distribution, repository, suite_option):
    return _mbd_e2n(distribution.mbd_get_apt_line, repository, suite_option)


@register.simple_tag
def mbd_distribution_apt_sources_list(distribution, repository, suite_option):
    return _mbd_e2n(distribution.mbd_get_apt_sources_list, repository, suite_option)


@register.simple_tag
def mbd_distribution_apt_preferences(distribution, repository, suite_option):
    return _mbd_e2n(distribution.mbd_get_apt_preferences, repository, suite_option)


@register.simple_tag
def mbd_repository_desc(repository, distribution, suite_option):
    return _mbd_e2n(repository.mbd_get_description, distribution, suite_option)


@register.simple_tag
def mbd_repository_mandatory_version(repository, dist, suite):
    return _mbd_e2n(repository.layout.mbd_get_mandatory_version_regex, repository, dist, suite)


@register.simple_tag
def mbd_build_status(success, failed):
    result = ""

    try:
        bres = success if success else failed
        # Uff: Currently, we need to parse bres_stat string here: "Build=status, Lintian=status"
        bres_stat = bres.get("bres_stat")
        sbuild_status = bres_stat.partition(",")[0].partition("=")[2]
        lintian_status = bres_stat.partition(",")[2].partition("=")[2]

        # sbuild build log stati
        # The only real documentation on this seems to be here: https://www.debian.org/devel/buildd/wanna-build-states
        build_colors = {"successful": "green", "skipped": "blue", "given-back": "yellow", "attempted": "magenta", "failed": "red"}

        # lintian build log stati
        lintian_colors = {"pass": "green", "fail": "red", "None": "blue"}

        # return "[BL]" html-style colorized
        result = "[<span style=\"color:{b}\">B</span><span style=\"color:{l}\">L</span>]".format(
            b=build_colors.get(sbuild_status, "black"),
            l=lintian_colors.get(lintian_status, "black"))
    except BaseException as e:
        LOG.warning("Some error generating build status (ignoring): {e}".format(e=e))

    return django.utils.safestring.mark_safe(result)
