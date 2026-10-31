from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import update_session_auth_hash

from .audit import log
from .roles import home_name


class RoleLoginView(LoginView):
    """One login page for everybody. After sign-in each role lands on its own app."""
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        nxt = self.get_redirect_url()
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={self.request.get_host()}):
            return nxt                      # RoleAccessMiddleware still bounces it if the role may not open it
        return reverse(home_name(self.request.user))


def role_home(request):
    if not request.user.is_authenticated:
        return redirect('login')
    return redirect(home_name(request.user))


def logout_view(request):
    auth_logout(request)            # the user_logged_out signal writes the audit row
    return redirect('login')


def password_change(request):
    if not request.user.is_authenticated:
        return redirect('login')
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        log(request, 'password_change', 'Changed own password')
        messages.success(request, 'Password changed.')
        return redirect(home_name(user))
    return render(request, 'accounts/password_change.html', {'form': form})


from .decorators import role_required  # noqa: E402


@role_required('operator')
def operator_home(request):
    """Landing page of the Dispatch role: the three tools they are allowed to use."""
    from django.utils import timezone
    from sku_manager.models import DispatchLog
    today = timezone.localdate()
    return render(request, 'accounts/operator_home.html', {
        'active_page': 'operator_home',
        'dispatched_today': DispatchLog.objects.filter(dispatched_at__date=today).exclude(platform_name='Counter (POS)').count(),
    })
