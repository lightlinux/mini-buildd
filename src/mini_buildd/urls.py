import django.views.generic.base
import django.views.generic.detail
import django.contrib.admin
import django.contrib.auth.views
from django.conf.urls import include, url

import mini_buildd.views
import mini_buildd.models.repository

django.contrib.admin.autodiscover()

urlpatterns = [
    # mini_buildd
    url(r"^$", django.views.generic.base.RedirectView.as_view(url="/mini_buildd/", permanent=False)),
    url(r"^mini_buildd/", include([
        url(r"^$", mini_buildd.views.home),
        url(r"^log/(.+)/(.+)/(.+)/$", mini_buildd.views.log),
        url(r"^live-buildlogs/(.+\.buildlog)$", mini_buildd.views.live_buildlogs),
        url(r"^repositories/(?P<pk>.+)/$", django.views.generic.detail.DetailView.as_view(model=mini_buildd.models.repository.Repository)),
        url(r"^api$", mini_buildd.views.api),
        url(r"^accounts/profile/$", mini_buildd.views.AccountProfileView.as_view(template_name="mini_buildd/account_profile.html")),
    ])),
    # admin
    url(r"^admin/doc/", include("django.contrib.admindocs.urls")),
    url(r"^admin/", django.contrib.admin.site.urls),
    # registration
    url(r"^accounts/password/reset/confirm/(?P<uidb64>[0-9A-Za-z]+)-(?P<token>.+)/$", django.contrib.auth.views.PasswordResetConfirmView.as_view(), name="auth_password_reset_confirm"),
    url(r"^accounts/", include("registration.backends.model_activation.urls")),
    # registration: This extra line is needed for p-d-registration since some django update...
    url(r"^accounts/", include("django.contrib.auth.urls")),
]

django.conf.urls.handler400 = mini_buildd.views.error400_bad_request
django.conf.urls.handler401 = mini_buildd.views.error401_unauthorized
django.conf.urls.handler404 = mini_buildd.views.error404_not_found
django.conf.urls.handler500 = mini_buildd.views.error500_internal
