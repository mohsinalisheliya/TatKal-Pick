from django.db import models


class BackupSettings(models.Model):
    """One row (pk=1): what to back up, where, and when."""
    FREQ = [('hours', 'Every few hours'), ('daily', 'Every day at a fixed time'), ('weekly', 'Every week')]
    enabled = models.BooleanField(default=False)
    destinations = models.TextField(blank=True, default='', help_text='One folder per line, e.g. E:\\TatkalBackups')
    frequency = models.CharField(max_length=10, choices=FREQ, default='daily')
    every_hours = models.PositiveSmallIntegerField(default=6)
    at_time = models.TimeField(default='21:00')
    weekday = models.PositiveSmallIntegerField(default=6)          # 0 = Monday ... 6 = Sunday
    keep_last = models.PositiveSmallIntegerField(default=14)
    include_media = models.BooleanField(default=False)
    include_csv = models.BooleanField(default=True)
    local_copy = models.BooleanField(default=True)
    last_success = models.DateTimeField(null=True, blank=True)
    last_attempt = models.DateTimeField(null=True, blank=True)
    running_since = models.DateTimeField(null=True, blank=True)
    pending_file = models.CharField(max_length=500, blank=True, default='')      # a backup still to be copied to a missing drive
    pending_dests = models.JSONField(default=list, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def dest_list(self):
        return [d.strip().strip('"') for d in (self.destinations or '').splitlines() if d.strip()]


class BackupRun(models.Model):
    TRIGGERS = [('manual', 'Back up now'), ('auto', 'Scheduled'), ('retry', 'Retry'), ('download', 'Download')]
    STATUS = [('ok', 'OK'), ('partial', 'Partly done'), ('failed', 'Failed')]
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=10, choices=TRIGGERS)
    status = models.CharField(max_length=10, choices=STATUS, default='failed')
    filename = models.CharField(max_length=200, blank=True, default='')
    size_bytes = models.BigIntegerField(default=0)
    message = models.TextField(blank=True, default='')
    detail = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-started_at']
