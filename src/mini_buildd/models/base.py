"""
Generic module for models of the django app *mini_buildd*.

Naming conventions
==================

Model class and field names
---------------------------
All model class names and all field names must be **human
readable with no abbreviations** (as django, per default,
displays the internal names intelligently to the end user).

*Model class names* must be in **CamelCase**.

*Field names* must be all **lowercase** and **seperatedy by underscores**.

For example, **don't** try to do sort of "grouping" using names like::

  email_notify
  email_allow_regex

This should rather read::

  notify
  allow_emails_to

To group fields together for the end user, use AdminModel's *fieldset* option.

Methods
-------
Any methods that represent mini-buildd logic should go into the models
directly, but must be prefixed with "mbd_". This avoids conflicts
with method names form the django model's class, but still keeps the
logic where it belongs.

"""

import datetime
import re
import pickle
import base64
import logging

import django.db.models
import django.contrib.messages
import django.contrib.admin
import django.contrib.auth.models
import django.db.models.signals
import django.core.exceptions
import django.template.response
import django.utils.timezone

import mini_buildd.config

from mini_buildd.models.msglog import MsgLog
LOG = logging.getLogger(__name__)


# Default action 'delete_selected' action does not call
# custom delete, nor does it ask prior to deletion.
#
# See: https://docs.djangoproject.com/en/dev/ref/contrib/admin/actions/
#
# So we just disable this default action. You can still delete
# single objects from the model's form.
try:
    django.contrib.admin.site.disable_action("delete_selected")
except BaseException as e:
    LOG.warning("Error disabling delete action (ignoring): {e}".format(e=e))


class Model(django.db.models.Model):
    """
    Abstract father model for all mini-buildd models.

    This just makes sure no config is changed or deleted while
    the daemon is running.
    """

    extra_options = django.db.models.TextField(blank=True, editable=True,
                                               help_text="""\
Extra/experimental options (in the form 'KEY: VALUE' per line) a
model might support.

Note that this is basically just a workaround to easily add
options to a model without changing the database scheme; i.e.,
these options may best be described as a staging area, or list
of 'unofficial features'.

The resp. model documentation should describe what extra options
are actually supported by the current model.
""")

    # May be used by any model for persistent python state
    pickled_data = django.db.models.TextField(blank=True, editable=False)

    class Meta():
        abstract = True
        app_label = "mini_buildd"

    class Admin(django.contrib.admin.ModelAdmin):
        @classmethod
        def _mbd_on_change(cls, request, obj):
            """Global actions to take when an object changes."""
            for o in obj.mbd_get_reverse_dependencies():
                o.mbd_set_changed(request)
                o.save()

        @classmethod
        def _mbd_on_activation(cls, request, obj):
            """Global actions to take when an object becomes active."""

        @classmethod
        def _mbd_on_deactivation(cls, request, obj):
            """Global actions to take when an object becomes inactive."""

        def save_model(self, request, obj, form, change):
            if change:
                self._mbd_on_change(request, obj)

            obj.save()

        def delete_model(self, request, obj):
            self._mbd_on_change(request, obj)

            is_prepared_func = getattr(obj, "mbd_is_prepared", None)
            if is_prepared_func and is_prepared_func():
                self.mbd_remove(request, obj)

            obj.delete()

    @classmethod
    def mbd_get_daemon(cls):
        from mini_buildd.daemon import get
        return get()

    def mbd_get_extra_options(self):
        result = {}
        for line in self.extra_options.splitlines():
            lkey, _lsep, lvalue = line.partition(":")
            if lkey:
                result[lkey] = lvalue.lstrip()
        return result

    def mbd_get_extra_option(self, key, default=None):
        return self.mbd_get_extra_options().get(key, default)

    def mbd_get_pickled_data(self, default=None):
        try:
            return pickle.loads(base64.decodebytes(bytes(self.pickled_data, encoding=mini_buildd.config.CHAR_ENCODING)))
        except BaseException as e:
            mini_buildd.config.log_exception(LOG, "Ignoring unpickling error", e)
            return default

    def mbd_set_pickled_data_pickled(self, pickled_data):
        self.pickled_data = str(base64.encodebytes(pickled_data), encoding=mini_buildd.config.CHAR_ENCODING)

    def mbd_set_pickled_data(self, data):
        self.mbd_set_pickled_data_pickled(pickle.dumps(data, pickle.HIGHEST_PROTOCOL))

    @classmethod
    def mbd_validate_regex(cls, regex, value, field_name):
        if not re.match(regex, value):
            raise django.core.exceptions.ValidationError("{n} field does not match regex {r}".format(n=field_name, r=regex))

    def mbd_get_dependencies(self):
        LOG.debug("No dependencies for {s}".format(s=self))
        return []

    def mbd_get_reverse_dependencies(self):
        LOG.debug("No reverse dependencies for {s}".format(s=self))
        return []

    @classmethod
    def mbd_get_or_create(cls, msglog, **kwargs):
        """Like get_or_create, but adds a info message."""
        obj, created = cls.objects.get_or_create(**kwargs)
        if created:
            msglog.info("Created: {o}".format(o=obj))
        else:
            msglog.debug("Already exists: {o}".format(o=obj))
        return obj, created


class StatusModel(Model):
    """Abstract model class for all models that carry a status. See Manual: :ref:`admin_configuration`."""

    # The main statuses: removed, prepared, active
    STATUS_REMOVED = 0
    STATUS_PREPARED = 1
    STATUS_ACTIVE = 2
    STATUS_CHOICES = (
        (STATUS_REMOVED, "Removed"),
        (STATUS_PREPARED, "Prepared"),
        (STATUS_ACTIVE, "Active"))
    STATUS_COLORS = {
        STATUS_REMOVED: {"bg": "red", "fg": "black"},
        STATUS_PREPARED: {"bg": "yellow", "fg": "black"},
        STATUS_ACTIVE: {"bg": "green", "fg": "white"}}
    status = django.db.models.IntegerField(choices=STATUS_CHOICES, default=STATUS_REMOVED, editable=False)

    # Statuses of the prepared data, relevant for status "Prepared" only.
    # For "Removed" it's always NONE, for "Active" it's always the stamp of the last check.
    CHECK_NONE = datetime.datetime(datetime.MINYEAR, 1, 1, tzinfo=datetime.timezone.utc)
    CHECK_CHANGED = datetime.datetime(datetime.MINYEAR, 1, 2, tzinfo=datetime.timezone.utc)
    CHECK_FAILED = datetime.datetime(datetime.MINYEAR, 1, 3, tzinfo=datetime.timezone.utc)
    CHECK_REACTIVATE = datetime.datetime(datetime.MINYEAR, 1, 4, tzinfo=datetime.timezone.utc)
    _CHECK_MAX = CHECK_REACTIVATE
    CHECK_STRINGS = {
        CHECK_NONE: {"char": "-", "string": "Unchecked -- please run check"},
        CHECK_CHANGED: {"char": "*", "string": "Changed -- please prepare again"},
        CHECK_FAILED: {"char": "x", "string": "Failed -- please fix and check again"},
        CHECK_REACTIVATE: {"char": "A", "string": "Failed in active state -- will auto-activate when check succeeds again"}}
    last_checked = django.db.models.DateTimeField(default=CHECK_NONE, editable=False)

    LETHAL_DEPENDENCIES = True

    # Obsoleted by CHECK_REACTIVATE prepared data state (but we need to keep it to not change the db scheme)
    auto_reactivate = django.db.models.BooleanField(default=False, editable=False)

    class Meta(Model.Meta):
        abstract = True

    class Admin(Model.Admin):
        def save_model(self, request, obj, form, change):
            if change:
                obj.mbd_set_changed(request)
            super().save_model(request, obj, form, change)

        @classmethod
        def _mbd_run_dependencies(cls, request, obj, func, **kwargs):
            """
            Run action for all dependencies.

            But don't fail and run all checks for models with
            LETHAL_DEPENDENCIES set to False. Practical use case is
            the Daemon model only, where we want to run all checks on
            all dependencies, but not fail ourselves.
            """
            for o in obj.mbd_get_dependencies():
                try:
                    func(request, o, **kwargs)
                except BaseException as e:
                    if obj.LETHAL_DEPENDENCIES:
                        raise
                    MsgLog(LOG, request).warning("Check on '{o}' failed: {e}".format(o=o, e=e))

        @classmethod
        def mbd_prepare(cls, request, obj):
            if not obj.mbd_is_prepared():
                # Fresh prepare
                cls._mbd_run_dependencies(request, obj, cls.mbd_prepare)
                obj.mbd_prepare(request)
                obj.status, obj.last_checked = obj.STATUS_PREPARED, obj.CHECK_NONE
                obj.save()
                MsgLog(LOG, request).info("Prepared: {o}".format(o=obj))
            elif obj.mbd_is_changed():
                # Update data on change
                cls._mbd_run_dependencies(request, obj, cls.mbd_prepare)
                obj.mbd_sync(request)
                obj.status, obj.last_checked = obj.STATUS_PREPARED, obj.CHECK_NONE
                obj.save()
                MsgLog(LOG, request).info("Synced: {o}".format(o=obj))
            else:
                MsgLog(LOG, request).info("Already prepared: {o}".format(o=obj))

        @classmethod
        def mbd_check(cls, request, obj, force=False, needs_activation=False):
            if obj.mbd_is_prepared() and not obj.mbd_is_changed():
                try:
                    # Also run for all status dependencies
                    cls._mbd_run_dependencies(request, obj, cls.mbd_check,
                                              force=force,
                                              needs_activation=obj.mbd_is_active() or obj.last_checked == obj.CHECK_REACTIVATE)

                    if force or obj.mbd_needs_check():
                        last_checked = obj.last_checked

                        obj.mbd_check(request)

                        # Handle special flags
                        reactivated = False
                        if obj.last_checked == obj.CHECK_REACTIVATE:
                            obj.status = StatusModel.STATUS_ACTIVE
                            reactivated = True
                            MsgLog(LOG, request).info("Auto-reactivated: {o}".format(o=obj))

                        # Finish up
                        obj.last_checked = django.utils.timezone.now()
                        obj.save()

                        # Run activation hook if reactivated
                        if reactivated:
                            cls._mbd_on_activation(request, obj)

                        MsgLog(LOG, request).info("Checked ({f}, last={c}): {o}".format(f="forced" if force else "scheduled", c=last_checked, o=obj))
                    else:
                        MsgLog(LOG, request).info("Needs no check: {o}".format(o=obj))

                    if needs_activation and not obj.mbd_is_active():
                        raise Exception("Not active, but a (tobe-)active item depends on it. Activate this first: {o}".format(o=obj))
                except BaseException:
                    # Check failed, auto-deactivate and re-raise exception
                    obj.last_checked = max(obj.last_checked, obj.CHECK_FAILED)
                    if obj.mbd_is_active():
                        obj.status, obj.last_checked = obj.STATUS_PREPARED, obj.CHECK_REACTIVATE
                        MsgLog(LOG, request).error("Automatically deactivated: {o}".format(o=obj))
                    obj.save()
                    raise
            else:
                raise Exception("Can't check removed or changed object (run 'prepare' first): {o}".format(o=obj))

        @classmethod
        def mbd_activate(cls, request, obj):
            if obj.mbd_is_prepared() and obj.mbd_is_checked():
                cls._mbd_run_dependencies(request, obj, cls.mbd_activate)
                obj.status = obj.STATUS_ACTIVE
                obj.save()
                cls._mbd_on_activation(request, obj)
                MsgLog(LOG, request).info("Activated: {o}".format(o=obj))
            elif obj.mbd_is_prepared() and (obj.last_checked == obj.CHECK_FAILED or obj.last_checked == obj.CHECK_NONE):
                obj.last_checked = obj.CHECK_REACTIVATE
                obj.save()
                MsgLog(LOG, request).info("Will auto-activate when check succeeds: {o}".format(o=obj))
            elif obj.mbd_is_active():
                MsgLog(LOG, request).info("Already active: {o}".format(o=obj))
            else:
                raise Exception("Prepare and check first: {o}".format(o=obj))

        @classmethod
        def mbd_deactivate(cls, request, obj):
            obj.status = min(obj.STATUS_PREPARED, obj.status)
            if obj.last_checked == obj.CHECK_REACTIVATE:
                obj.last_checked = obj.CHECK_FAILED
            obj.save()
            cls._mbd_on_deactivation(request, obj)
            MsgLog(LOG, request).info("Deactivated: {o}".format(o=obj))

        @classmethod
        def mbd_remove(cls, request, obj):
            if obj.mbd_is_prepared():
                obj.mbd_remove(request)
                obj.status, obj.last_checked = obj.STATUS_REMOVED, obj.CHECK_NONE
                obj.save()
                MsgLog(LOG, request).info("Removed: {o}".format(o=obj))
            else:
                MsgLog(LOG, request).info("Already removed: {o}".format(o=obj))

        @classmethod
        def mbd_action(cls, request, queryset, action, **kwargs):
            """
            Try to run action on each object in queryset.

            Emit error message on failure, but don't fail ourself.
            """
            for o in queryset:
                try:
                    getattr(cls, "mbd_" + action)(request, o, **kwargs)
                except BaseException as e:
                    mini_buildd.config.log_exception(MsgLog(LOG, request), "{a} failed: {o}".format(a=action, o=o), e)

        def mbd_action_prepare(self, request, queryset):
            self.mbd_action(request, queryset, "prepare")
        mbd_action_prepare.short_description = "Prepare"

        def mbd_action_check(self, request, queryset):
            self.mbd_action(request, queryset, "check", force=True)
        mbd_action_check.short_description = "Check"

        def mbd_action_activate(self, request, queryset):
            self.mbd_action(request, queryset, "activate")
        mbd_action_activate.short_description = "Activate"

        def mbd_action_deactivate(self, request, queryset):
            self.mbd_action(request, queryset, "deactivate")
        mbd_action_deactivate.short_description = "Deactivate"

        def mbd_action_remove(self, request, queryset):  # pylint: disable=inconsistent-return-statements
            if request.POST.get("confirm"):
                self.mbd_action(request, queryset, "remove")
            else:
                return django.template.response.TemplateResponse(
                    request,
                    "admin/confirm.html",
                    {
                        "title": ("Are you sure?"),
                        "queryset": queryset,
                        "action": "mbd_action_remove",
                        "desc": """\
Unpreparing means all the data associated by preparation will be
removed from the system. Especially for repositories,
this would mean losing all packages!
""",
                        "action_checkbox_name": django.contrib.admin.helpers.ACTION_CHECKBOX_NAME})
        mbd_action_remove.short_description = "Remove"

        def mbd_action_pc(self, request, queryset):
            self.mbd_action(request, queryset, "prepare")
            self.mbd_action(request, queryset, "check")
        mbd_action_pc.short_description = "PC"

        def mbd_action_pca(self, request, queryset):
            self.mbd_action_pc(request, queryset)
            self.mbd_action(request, queryset, "activate")
        mbd_action_pca.short_description = "PCA"

        @classmethod
        def mbd_meta_pca_all(cls, msglog):
            """Run prepare, check, and activate for all objects of this model."""
            cls.mbd_action(msglog.request, cls.mbd_model.objects.all(), "prepare")
            cls.mbd_action(msglog.request, cls.mbd_model.objects.all(), "check")
            cls.mbd_action(msglog.request, cls.mbd_model.objects.all(), "activate")

        def colored_status(self, obj):  # pylint: disable=no-self-use
            return django.utils.html.format_html(
                '<div style="font-weight:bold;background-color:{bc};color:{fc};padding:2px 0px 2px 5px" title="{t}">{o}</div>',
                bc=obj.STATUS_COLORS[obj.status].get("bg"),
                fc=obj.STATUS_COLORS[obj.status].get("fg"),
                t=obj.mbd_get_status_display(typ="string"),
                o=obj.mbd_get_status_display(typ="char"))

        actions = [mbd_action_prepare, mbd_action_check, mbd_action_pc, mbd_action_activate, mbd_action_pca, mbd_action_deactivate, mbd_action_remove]
        list_display = ["colored_status", "__str__"]
        list_display_links = ["__str__"]

    @property
    def days_until_recheck(self):
        """
        Field temporarily implemented as extra_option.

        .. note:: Currently using 6 days as default value -- as it copes better with the current setup to restart
                  mini-buildd weekly (via cron.weekly) to actually achieve automated checking.
        """
        return int(self.mbd_get_extra_option("Days-Until-Recheck", "6"))

    def mbd_set_changed(self, request):
        if self.mbd_is_active():
            self.status = self.STATUS_PREPARED
            MsgLog(LOG, request).warning("Deactivated due to changes: {o}".format(o=self))
        self.last_checked = self.CHECK_CHANGED
        MsgLog(LOG, request).warning("Marked as changed: {o}".format(o=self))

    #
    # Action hooks helpers
    #
    def _mbd_remove_and_prepare(self, request):
        mini_buildd.models.base.StatusModel.Admin.mbd_remove(request, self)
        mini_buildd.models.base.StatusModel.Admin.mbd_prepare(request, self)

    #
    # Status abstractions and helpers
    #
    def mbd_is_prepared(self):
        return self.status >= self.STATUS_PREPARED

    def mbd_is_active(self):
        return self.status >= self.STATUS_ACTIVE

    def mbd_is_checked(self):
        return self.last_checked > self._CHECK_MAX

    def mbd_needs_check(self):
        return not self.mbd_is_checked() or self.last_checked < (django.utils.timezone.now() - datetime.timedelta(days=self.days_until_recheck))

    def mbd_is_changed(self):
        return self.last_checked == self.CHECK_CHANGED

    @classmethod
    def mbd_get_active(cls):
        return cls.objects.filter(status__gte=cls.STATUS_ACTIVE)

    @classmethod
    def mbd_get_active_or_auto_reactivate(cls):
        return cls.objects.filter(django.db.models.Q(status__gte=cls.STATUS_ACTIVE) | django.db.models.Q(last_checked=cls.CHECK_REACTIVATE))

    @classmethod
    def mbd_get_prepared(cls):
        return cls.objects.filter(status__gte=cls.STATUS_PREPARED)

    def mbd_get_check_display(self, typ="string"):
        return {"string": self.last_checked.strftime("Checked on %Y-%m-%d %H:%M"), "char": "C"}[typ] if self.mbd_is_checked() else self.CHECK_STRINGS[self.last_checked][typ]

    def mbd_get_status_display(self, typ="string"):
        return "{s} ({p})".format(s=self.get_status_display(),
                                  p=self.mbd_get_check_display(typ))
