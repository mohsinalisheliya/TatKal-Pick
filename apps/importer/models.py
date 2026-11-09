from django.conf import settings
from django.db import models



    
class ImportMapping(models.Model):
    """A saved "my column X is your field Y" mapping, so the next file of the same shape needs no clicking."""
    kind = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    # {field_key: {"col": "<header text>"} or {"fixed": "<text>"}}
    mapping = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('kind', 'name')
        ordering = ['kind', 'name']

    def __str__(self):
        return f'{self.kind}: {self.name}'


class ImportJob(models.Model):
    STATUS = [('uploaded', 'Uploaded'), ('mapped', 'Mapped'), ('done', 'Imported'), ('failed', 'Failed')]
    kind = models.CharField(max_length=20)
    filename = models.CharField(max_length=200)
    file = models.FileField(upload_to='imports/%Y/%m/')
    status = models.CharField(max_length=10, choices=STATUS, default='uploaded')
    # {field_key: {"col": <index>} | {"fixed": "text"}}
    mapping = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
