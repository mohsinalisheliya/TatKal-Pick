from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """One row per login: which role the person has and, for cashiers, which prices they may use."""
    ROLE_CHOICES = [('master', 'Master Admin'), ('admin', 'Admin'), ('operator', 'Dispatch'), ('cashier', 'Cashier')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='cashier')
    # comma separated keys from roles.TIERS, e.g. "reselling,wholesale". Only used for cashiers.
    price_tiers = models.CharField(max_length=60, blank=True, default='reselling')
    default_tier = models.CharField(max_length=10, blank=True, default='reselling')
    # biggest discount (%) this cashier may give; blank = project default
    max_discount = models.PositiveSmallIntegerField(null=True, blank=True)
    # may this cashier cancel (void) their own bills at the counter?
    can_void = models.BooleanField(default=False)
    phone = models.CharField(max_length=20, blank=True, default='')
    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user} ({self.role})'


class AuditLog(models.Model):
    """Who did what, when, from where. Rows are never edited or deleted by the app."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='audit_logs')
    username = models.CharField(max_length=150, blank=True, default='', db_index=True)
    role = models.CharField(max_length=10, blank=True, default='')
    action = models.CharField(max_length=30, db_index=True)
    detail = models.CharField(max_length=500, blank=True, default='')
    path = models.CharField(max_length=300, blank=True, default='')
    method = models.CharField(max_length=8, blank=True, default='')
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.username} {self.action}'
