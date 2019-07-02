import logging

import django.conf
import django.core.handlers.wsgi
import django.core.management
import django.contrib.messages.constants

import mini_buildd.setup
import mini_buildd.models
import mini_buildd.models.msglog

LOG = logging.getLogger(__name__)


class WebApp(django.core.handlers.wsgi.WSGIHandler):
    """mini-buildd's web application."""

    def __init__(self):
        LOG.info("Generating web application...")
        super().__init__()
        mini_buildd.models.import_all()

        LOG.info("Migrating database (migrate)...")
        try:
            django.core.management.call_command("migrate", interactive=False, run_syncdb=True, verbosity=0)
        except django.db.OperationalError as e:
            LOG.warning("OperationalError on migrate ({}). Retrying with '--fake-initial'...".format(e))
            django.core.management.call_command("migrate", interactive=False, run_syncdb=True, fake_initial=True, verbosity=0)

        LOG.info("Clean up python-registration (cleanupregistration)...")
        django.core.management.call_command("cleanupregistration", interactive=False, verbosity=0)

        # django 1.8 no longer per default runs this; Doing this once, manually
        LOG.info("Run django internal checks (check)...")
        django.core.management.call_command("check")

    @classmethod
    def set_admin_password(cls, password):
        """
        Set the password for the administrator.

        :param password: The password to use.
        :type password: string
        """
        # This import needs the django app to be already configured (since django 1.5.2)
        from django.contrib.auth import models

        try:
            user = models.User.objects.get(username="admin")
            LOG.info("Updating 'admin' user password...")
            user.set_password(password)
            user.save()
        except models.User.DoesNotExist:
            LOG.info("Creating initial 'admin' user...")
            models.User.objects.create_superuser("admin", "root@localhost", password)

    @classmethod
    def remove_system_artifacts(cls):
        """
        Bulk-remove all model instances that might have produced cruft on the system.

        I.e., outside mini-buildd's home.
        """
        # This import needs the django app to be already configured
        from mini_buildd.models.chroot import Chroot
        Chroot.Admin.mbd_action(None, Chroot.mbd_get_prepared(), "remove")

    @classmethod
    def loaddata(cls, file_name):
        django.core.management.call_command("loaddata", file_name)

    @classmethod
    def dumpdata(cls, app_path):
        LOG.info("Dumping data for: {a}".format(a=app_path))
        django.core.management.call_command("dumpdata", app_path, indent=2, format="json")
