# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import django.views.generic.base
import django.contrib.admin
import django.contrib.auth.views

# pylint: disable=import-error,no-name-in-module
try:
    # django >= 1.6
    from django.conf.urls import patterns, include, url
    DJANGO_UID_HASH = "b64"
except ImportError:
    # django 1.5
    from django.conf.urls.defaults import patterns, include, url
    DJANGO_UID_HASH = "b36"
# pylint: enable=import-error,no-name-in-module

django.contrib.admin.autodiscover()

# pylint: disable=no-value-for-parameter
urlpatterns = patterns(
    "",
    # mini_buildd
    (r"^$", django.views.generic.base.RedirectView.as_view(url="/mini_buildd/", permanent=False)),
    (r"^mini_buildd/", include("mini_buildd.urls")),
    # admin
    (r"^admin/doc/", include("django.contrib.admindocs.urls")),
    (r"^admin/", include(django.contrib.admin.site.urls)),
    # registration
    url(r'^accounts/password/reset/confirm/(?P<uid' + DJANGO_UID_HASH + '>[0-9A-Za-z]+)-(?P<token>.+)/$', django.contrib.auth.views.password_reset_confirm, name='auth_password_reset_confirm'),
    (r'^accounts/', include("registration.backends.default.urls")),
    # registration: This extra line is needed for p-d-registration since some django update...
    (r'^accounts/', include('django.contrib.auth.urls')),
)
# pylint: enable=no-value-for-parameter
