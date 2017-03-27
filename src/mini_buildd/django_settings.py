# -*- coding: utf-8 -*-

import os
import logging
import random

import django
import django.conf

import mini_buildd.setup
import mini_buildd.models.msglog

LOG = logging.getLogger(__name__)

MBD_INSTALLED_APPS = (
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.admindocs",
    "django.contrib.staticfiles",
    "registration",
    "mini_buildd")

MBD_STATIC_CONTEXT = None


def context_processor(_request):
    global MBD_STATIC_CONTEXT  # pylint: disable=global-statement
    if MBD_STATIC_CONTEXT is None:
        MBD_STATIC_CONTEXT = {"mbd_version": mini_buildd.__version__}
        try:
            from platform import python_version
            from cherrypy import __version__ as cherrypy_version
            from pyftpdlib import __ver__ as pyftpdlib_version
            from registration import get_version as registration_version

            MBD_STATIC_CONTEXT["mbd_components"] = {
                "python": python_version(),
                "django": django.get_version(),
                "django-registration": registration_version(),
                "cherrypy": cherrypy_version,
                "pyftpdlib": pyftpdlib_version}
        except:  # pylint: disable=bare-except
            pass

    return MBD_STATIC_CONTEXT


class SMTPCreds(object):
    """
    SMTP creds string parser. Format "USER:PASSWORD@smtp|ssmtp://HOST:PORT".

    >>> d = SMTPCreds(":@smtp://localhost:25")
    >>> (d.user, d.password, d.protocol, d.host, d.port)
    ('', '', 'smtp', 'localhost', 25)
    >>> d = SMTPCreds("kuh:sa:ck@smtp://colahost:44")
    >>> (d.user, d.password, d.protocol, d.host, d.port)
    ('kuh', 'sa:ck', 'smtp', 'colahost', 44)
    """
    def __init__(self, creds):
        self.creds = creds
        at = creds.partition("@")

        usrpass = at[0].partition(":")
        self.user = usrpass[0]
        self.password = usrpass[2]

        smtp = at[2].partition(":")
        self.protocol = smtp[0]

        hopo = smtp[2].partition(":")
        self.host = hopo[0][2:]
        self.port = int(hopo[2])


def get_django_secret_key(home):
    """
    This method creates *once* django's SECRET_KEY and/or returns it.

    :param home: mini-buildd's home directory.
    :type home: string
    :returns: string -- the (created) key.
    """
    secret_key_filename = os.path.join(home, ".django_secret_key")

    # the key to create or read from file
    secret_key = ""

    if not os.path.exists(secret_key_filename):
        # use same randomize-algorithm as in "django/core/management/commands/startproject.py"
        secret_key = "".join([random.choice("abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)") for _i in range(50)])
        with os.fdopen(os.open(secret_key_filename, os.O_CREAT | os.O_WRONLY, 0o600), "w") as secret_key_fd:
            secret_key_fd.write(secret_key)
    else:
        with open(secret_key_filename, "r") as existing_file:
            secret_key = existing_file.read()

    return secret_key


def configure(smtp_string, loglevel):
    """
    Configure django.
    """
    LOG.info("Setting up django...")

    smtp = SMTPCreds(smtp_string)
    debug = "webapp" in mini_buildd.setup.DEBUG

    settings = {
        "DEBUG": debug,
        "MESSAGE_LEVEL": mini_buildd.models.msglog.MsgLog.level2django(loglevel),

        "ALLOWED_HOSTS": ["*"],

        "EMAIL_HOST": smtp.host,
        "EMAIL_PORT": smtp.port,
        "EMAIL_USE_TLS": smtp.protocol == "ssmtp",
        "EMAIL_HOST_USER": smtp.user,
        "EMAIL_HOST_PASSWORD": smtp.password,

        "DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3",
                                  "NAME": os.path.join(mini_buildd.setup.HOME_DIR, "config.sqlite")}},

        "TIME_ZONE": None,
        "USE_L10N": True,
        "SECRET_KEY": get_django_secret_key(mini_buildd.setup.HOME_DIR),
        "ROOT_URLCONF": "mini_buildd.root_urls",
        "STATIC_URL": mini_buildd.setup.STATIC_URL,
        "ACCOUNT_ACTIVATION_DAYS": 3,
        "LOGIN_REDIRECT_URL": "/mini_buildd/",

        "MIDDLEWARE_CLASSES": (
            "django.middleware.common.CommonMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware"),

        "INSTALLED_APPS": MBD_INSTALLED_APPS
    }

    settings.update({
        "TEMPLATES": [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [
                    "{p}/mini_buildd/templates".format(p=mini_buildd.setup.PY_PACKAGE_PATH)
                ],
                "APP_DIRS": True,
                "OPTIONS": {
                    "debug": debug,
                    "context_processors": [
                        # former django default (>= 1.9, the default is empty):
                        "django.contrib.auth.context_processors.auth",
                        "django.template.context_processors.debug",
                        "django.template.context_processors.i18n",
                        "django.template.context_processors.media",
                        "django.template.context_processors.static",
                        "django.template.context_processors.tz",
                        "django.contrib.messages.context_processors.messages",
                        # mini-buildd custom:
                        "mini_buildd.django_settings.context_processor"
                    ],
                },
            },
        ],
    })

    django.conf.settings.configure(**settings)
    django.setup()


def pseudo_configure():
    """
    Pseudo-configure django. Use this where you need mini-buildd's model classes, but no actual instance.

    Example: Sphinx doc creation, API clients for unpickling model instances.
    """
    django.conf.settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        MIDDLEWARE_CLASSES=(),
        INSTALLED_APPS=MBD_INSTALLED_APPS)

    django.setup()
