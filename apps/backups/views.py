"""Master admin: download the whole database, and set up automatic backups to a pendrive."""
import os
import sys
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required

from . import services
from .models import BackupRun, BackupSettings

master_only = role_required('master')
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _task_command():
    manage = Path(getattr(settings, 'BASE_DIR', os.getcwd())) / 'manage.py'
    return (f'schtasks /Create /SC MINUTE /MO 15 /TN "TatkalBackup" '
            f'/TR "\\"{sys.executable}\\" \\"{manage}\\" run_backups"')


def _int(post, key, lo, hi, label, errors, default):
    try:
        v = int(post.get(key, default))
    except (TypeError, ValueError):
        errors.append(f'{label} must be a whole number.')
        return default
    if not lo <= v <= hi:
        errors.append(f'{label} must be between {lo} and {hi}.')
        return default
    return v


@master_only
def backups_home(request):
    s = BackupSettings.get()
    errors = []
    form = None
    if request.method == 'POST':
        p = request.POST
        form = p
        freq = p.get('frequency', 'daily')
        if freq not in dict(BackupSettings.FREQ):
            errors.append('Choose how often to back up.')
        every = _int(p, 'every_hours', 1, 168, 'Hours between backups', errors, s.every_hours)
        keep = _int(p, 'keep_last', 1, 365, 'Backups to keep', errors, s.keep_last)
        weekday = _int(p, 'weekday', 0, 6, 'Day of the week', errors, s.weekday)
        try:
            at = datetime.strptime(p.get('at_time', '21:00').strip(), '%H:%M').time()
        except ValueError:
            errors.append('Time must look like 21:30.'); at = s.at_time
        dests = [d.strip().strip('"') for d in p.get('destinations', '').splitlines() if d.strip()]
        if len(dests) > 5:
            errors.append('Use at most 5 backup folders.')
        enabled, local = p.get('enabled') == 'on', p.get('local_copy') == 'on'
        if enabled and not dests and not local:
            errors.append('Add a backup folder (for example the pendrive) or keep a copy on this computer.')
        if not errors:
            s.enabled, s.frequency, s.every_hours, s.keep_last, s.weekday, s.at_time = enabled, freq, every, keep, weekday, at
            s.destinations = '\n'.join(dests)
            s.include_media, s.include_csv, s.local_copy = p.get('include_media') == 'on', p.get('include_csv') == 'on', local
            s.save()
            audit.log(request, 'backup_settings', f"auto={'on' if enabled else 'off'} {freq} to {len(dests)} folder(s), keep {keep}")
            messages.success(request, 'Backup settings saved.')
            return redirect('backups_home')
    if request.GET.get('checked'):
        pass
    runs = list(BackupRun.objects.all()[:25])
    st = services.status(s)
    return render(request, 'backups/home.html', {
        'tab': 'backups', 's': s, 'errors': errors, 'form': form, 'runs': runs, 'st': st,
        'weekdays': list(enumerate(WEEKDAYS)), 'freqs': BackupSettings.FREQ, 'dests': s.dest_list(),
        'task_cmd': _task_command(), 'local_dir': services.local_dir(), 'running': bool(s.running_since)})


@master_only
@require_POST
def backups_now(request):
    run = services.run_backup('manual')
    if run is None:
        messages.error(request, 'A backup is already running. Wait a minute and try again.')
    else:
        (messages.success if run.status == 'ok' else messages.error)(request, f'{run.get_status_display()}: {run.message}')
        audit.log(request, 'backup_run', f'{run.status} {run.filename}')
    return redirect('backups_home')


@master_only
@require_POST
def backups_test(request):
    dests = BackupSettings.get().dest_list()
    if not dests:
        messages.error(request, 'Add a backup folder first, then save.')
    for d in dests:
        ok, msg = services.check_destination(d)
        (messages.success if ok else messages.error)(request, f'{d}: {msg}')
    return redirect('backups_home')


class _AutoDelete:
    """File handle that removes the temporary zip once the download has been sent."""
    def __init__(self, path):
        self.path, self.name = path, str(path)
        self._f = open(path, 'rb')

    def read(self, n=-1):
        return self._f.read(n)

    def close(self):
        self._f.close()
        try:
            os.remove(self.path)
        except OSError:
            pass


@master_only
@require_POST
def backups_download(request):
    """Build a full backup and send it to the browser (nothing is kept on the server)."""
    media = request.POST.get('include_media') == 'on'
    csv_files = request.POST.get('include_csv', 'on') == 'on'
    path = services.local_dir() / ('download-' + services.new_name())
    run = BackupRun.objects.create(trigger='download')
    try:
        manifest = services.build_zip(path, media, csv_files)
        services.verify_zip(path)
    except Exception as exc:
        run.status, run.message, run.finished_at = 'failed', f'{type(exc).__name__}: {exc}', timezone.now()
        run.save()
        path.unlink(missing_ok=True)
        messages.error(request, f'Could not build the backup: {exc}')
        return redirect('backups_home')
    run.status, run.filename, run.size_bytes, run.finished_at = 'ok', path.name, path.stat().st_size, timezone.now()
    run.message = f"Downloaded ({manifest['total_rows']} rows{', with uploaded files' if media else ''})."
    run.save()
    audit.log(request, 'backup_download', run.message)
    resp = FileResponse(_AutoDelete(path), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{path.name.replace("download-", "")}"'
    resp['Content-Length'] = str(path.stat().st_size)
    return resp
