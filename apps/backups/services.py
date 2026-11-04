"""Full-database export and the backup engine.

A backup is ONE zip file:
  database.json   every table (Django dumpdata) - restore with `python manage.py restore_backup`
  db.sqlite3      a consistent copy of the database file (only when you use SQLite)
  csv/            one readable CSV per table (optional)
  media/          uploaded files: product photos, label PDFs (optional)
  manifest.json, README-RESTORE.txt
"""
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from .models import BackupRun, BackupSettings

PREFIX = 'tatkal-backup-'
NAME_RE = re.compile(r'^tatkal-backup-\d{8}-\d{6}\.zip$')
_EXCLUDE = ['contenttypes', 'auth.permission', 'sessions', 'admin.logentry']


def exclude_list():
    """Only name apps/models that are installed in this project (dumpdata stops on an unknown one)."""
    out = []
    for label in _EXCLUDE:
        try:
            apps.get_model(label) if '.' in label else apps.get_app_config(label)
            out.append(label)
        except (LookupError, ValueError):
            pass
    return out
RETRY_MINUTES = 5
STALE_LOCK = timedelta(minutes=45)


def local_dir():
    base = Path(getattr(settings, 'BASE_DIR', os.getcwd()))
    d = Path(getattr(settings, 'BACKUP_LOCAL_DIR', base / 'backup_files'))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path():
    db = settings.DATABASES['default']
    if db['ENGINE'].endswith('sqlite3') and str(db['NAME']) not in ('', ':memory:') and Path(str(db['NAME'])).exists():
        return Path(str(db['NAME']))
    return None


def _models():
    skip = {'contenttypes.contenttype', 'auth.permission', 'sessions.session', 'admin.logentry'}
    return [m for m in apps.get_models() if m._meta.label_lower not in skip and not m._meta.proxy and m._meta.managed]


README = """TATKAL PICK BACKUP
Created: {when}

Restore (this REPLACES everything in the current database):
  1. Install the project, run:  python manage.py migrate
  2. python manage.py restore_backup "<path to this zip>"
  (add --no-media to leave uploaded files alone)

With SQLite you can also stop the server and copy db.sqlite3 from this zip over your project's db.sqlite3.
The csv/ folder is for reading in Excel only, it is not used for restoring.
"""


def build_zip(dest_path, include_media=False, include_csv=True):
    """Write the backup zip to dest_path (a Path). Returns the manifest dict."""
    dest_path = Path(dest_path)
    tmp = Path(tempfile.mkdtemp(prefix='tp-bk-', dir=str(local_dir())))
    try:
        dump = tmp / 'database.json'
        with open(dump, 'w', encoding='utf-8') as fh:
            call_command('dumpdata', use_natural_foreign_keys=True, use_natural_primary_keys=True,
                         exclude=exclude_list(), stdout=fh, verbosity=0)
        counts = {}
        with zipfile.ZipFile(dest_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.write(dump, 'database.json')
            if include_csv:
                for m in _models():
                    names = [f.attname for f in m._meta.concrete_fields]
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(names)
                    n = 0
                    for row in m.objects.values_list(*names).iterator(chunk_size=2000):
                        w.writerow(['' if v is None else v for v in row])
                        n += 1
                    counts[m._meta.label] = n
                    zf.writestr(f'csv/{m._meta.label}.csv', '\ufeff' + buf.getvalue())
            else:
                for m in _models():
                    counts[m._meta.label] = m.objects.count()
            dbp = _db_path()
            if dbp:
                copy = tmp / 'db.sqlite3'
                src, dst = sqlite3.connect(str(dbp)), sqlite3.connect(str(copy))
                try:
                    src.backup(dst)
                finally:
                    dst.close(); src.close()
                zf.write(copy, 'db.sqlite3')
            media_files = 0
            root = Path(str(getattr(settings, 'MEDIA_ROOT', '') or ''))
            if include_media and root.is_dir():
                for f in root.rglob('*'):
                    if f.is_file():
                        zf.write(f, 'media/' + f.relative_to(root).as_posix())
                        media_files += 1
            now = timezone.localtime()
            manifest = {'created': now.isoformat(), 'django': django.get_version(), 'engine': settings.DATABASES['default']['ENGINE'],
                        'include_media': include_media, 'media_files': media_files, 'include_csv': include_csv, 'rows': counts,
                        'total_rows': sum(counts.values())}
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))
            zf.writestr('README-RESTORE.txt', README.format(when=now.strftime('%d %b %Y %H:%M')))
        return manifest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify_zip(path):
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            raise IOError(f'Backup file is damaged ({bad}).')
        if 'database.json' not in zf.namelist():
            raise IOError('Backup file has no database.json.')


def new_name():
    return f'{PREFIX}{timezone.localtime():%Y%m%d-%H%M%S}.zip'


# ------------------------------------------------------------------ destinations
def check_destination(path):
    """-> (ok, message). A drive that is not plugged in is reported, never created as a plain folder."""
    p = Path(path)
    try:
        if not p.exists():
            if not p.parent.exists():
                return False, 'Folder or drive not found (is the pendrive plugged in?)'
            p.mkdir()
        probe = p / '.tatkal-write-test'
        probe.write_text('ok')
        probe.unlink()
        free = shutil.disk_usage(str(p)).free
        return True, f'Ready, {free / 1024 ** 3:.1f} GB free'
    except Exception as exc:
        return False, f'Cannot write there: {exc}'


def _copy_to(src, folder):
    ok, msg = check_destination(folder)
    if not ok:
        return False, msg
    target = Path(folder) / src.name
    part = Path(folder) / (src.name + '.part')
    try:
        shutil.copyfile(src, part)
        if part.stat().st_size != src.stat().st_size:
            raise IOError('copied file has a different size')
        os.replace(part, target)
        verify_zip(target)
        return True, 'Copied'
    except Exception as exc:
        try:
            part.unlink()
        except OSError:
            pass
        return False, f'Copy failed: {exc}'


def prune(folder, keep, protect=()):
    """Delete the oldest tatkal-backup-*.zip files beyond `keep`. Never touches other files."""
    folder = Path(folder)
    if not folder.is_dir():
        return
    files = sorted((f for f in folder.iterdir() if NAME_RE.match(f.name)), key=lambda f: f.name)
    for f in files[:-keep] if keep > 0 else []:
        if str(f) in protect:
            continue
        try:
            f.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ the backup job
def _claim():
    cutoff = timezone.now() - STALE_LOCK
    BackupSettings.get()
    got = BackupSettings.objects.filter(pk=1).filter(running_since__isnull=True).update(running_since=timezone.now())
    if not got:
        got = BackupSettings.objects.filter(pk=1, running_since__lt=cutoff).update(running_since=timezone.now())
    return bool(got)


def run_backup(trigger='manual'):
    """Build the zip, copy it to every destination, prune old ones. Returns the BackupRun (or None if one is already running)."""
    if not _claim():
        return None
    run = BackupRun.objects.create(trigger=trigger)
    s = BackupSettings.get()
    detail, local_file = [], None
    try:
        s.last_attempt = timezone.now()
        local_file = local_dir() / new_name()
        manifest = build_zip(local_file, s.include_media, s.include_csv)
        verify_zip(local_file)
        run.filename, run.size_bytes = local_file.name, local_file.stat().st_size
        dests = s.dest_list()
        failed = []
        for d in dests:
            ok, msg = _copy_to(local_file, d)
            detail.append({'where': d, 'ok': ok, 'message': msg})
            if ok:
                prune(d, s.keep_last)
            else:
                failed.append(d)
        if s.local_copy or not dests:
            detail.insert(0, {'where': f'This computer ({local_dir()})', 'ok': True, 'message': 'Saved'})
        keep_local = s.local_copy or not dests or bool(failed)
        if keep_local:
            prune(local_dir(), max(s.keep_last, 1), protect=(str(local_file),))
        else:
            local_file.unlink(missing_ok=True)
        run.status = 'failed' if (dests and len(failed) == len(dests) and not s.local_copy) else ('partial' if failed else 'ok')
        if run.status == 'failed':
            run.message = 'No backup drive could be reached. The backup is kept on this computer and will be copied when the drive is back.'
        elif failed:
            run.message = 'Saved, but not copied to: ' + ', '.join(failed) + '. Will retry automatically.'
        else:
            run.message = f"Backup complete ({manifest['total_rows']} rows)."
        s.pending_file = str(local_file) if failed else ''
        s.pending_dests = failed
        if run.status != 'failed' or keep_local:
            s.last_success = timezone.now()
    except Exception as exc:
        run.status, run.message = 'failed', f'{type(exc).__name__}: {exc}'
        if local_file and local_file.exists() and not run.filename:
            local_file.unlink(missing_ok=True)
    finally:
        run.detail, run.finished_at = detail, timezone.now()
        run.save()
        s.running_since = None
        s.save(update_fields=['last_success', 'last_attempt', 'running_since', 'pending_file', 'pending_dests'])
    return run


def retry_pending():
    """The pendrive was missing last time: copy the waiting backup now if it is back."""
    s = BackupSettings.get()
    f = Path(s.pending_file) if s.pending_file else None
    if not f or not f.exists() or not s.pending_dests or not _claim():
        if s.pending_file and (not f or not f.exists()):
            BackupSettings.objects.filter(pk=1).update(pending_file='', pending_dests=[])
        return None
    run = BackupRun.objects.create(trigger='retry', filename=f.name, size_bytes=f.stat().st_size)
    detail, still = [], []
    try:
        for d in s.pending_dests:
            ok, msg = _copy_to(f, d)
            detail.append({'where': d, 'ok': ok, 'message': msg})
            (prune(d, s.keep_last) if ok else still.append(d))
        run.status = 'partial' if still else 'ok'
        run.message = ('Still waiting for: ' + ', '.join(still)) if still else 'Copied to the drive that was missing.'
        if not still and not s.local_copy:
            f.unlink(missing_ok=True)
        BackupSettings.objects.filter(pk=1).update(pending_file=str(f) if still else '', pending_dests=still, last_attempt=timezone.now())
    finally:
        run.detail, run.finished_at = detail, timezone.now()
        run.save()
        BackupSettings.objects.filter(pk=1).update(running_since=None)
    return run


# ------------------------------------------------------------------ schedule
def scheduled_time(s, now):
    """The most recent moment a backup should have started (None for 'every N hours')."""
    now = timezone.localtime(now)
    t = s.at_time
    if isinstance(t, str):
        t = datetime.strptime(t, '%H:%M').time()
    cand = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if s.frequency == 'daily':
        return cand if cand <= now else cand - timedelta(days=1)
    back = (now.weekday() - s.weekday) % 7
    cand = cand - timedelta(days=back)
    return cand if cand <= now else cand - timedelta(days=7)


def next_due(s, now=None):
    now = now or timezone.now()
    if s.frequency == 'hours':
        base = s.last_success or now
        return base + timedelta(hours=max(1, s.every_hours))
    due = scheduled_time(s, now)
    if s.last_success and s.last_success >= due:
        due = due + timedelta(days=1 if s.frequency == 'daily' else 7)
    return due


def is_due(s, now=None):
    now = now or timezone.now()
    if not s.enabled:
        return False
    if s.last_attempt and now - s.last_attempt < timedelta(minutes=RETRY_MINUTES):
        return False                         # just tried (also stops a failing backup from looping every minute)
    if s.last_success is None:
        return True
    return next_due(s, now) <= now


def check_and_run(force=False):
    """Called every minute by the background thread, or by `manage.py run_backups` from Task Scheduler."""
    s = BackupSettings.get()
    if force:
        return run_backup('manual')
    if s.pending_file and (not s.last_attempt or timezone.now() - s.last_attempt >= timedelta(minutes=RETRY_MINUTES)):
        retry_pending()
        s = BackupSettings.get()
    if is_due(s):
        return run_backup('auto')
    return None


def status(s=None):
    """For the pages: a short traffic-light summary."""
    s = s or BackupSettings.get()
    now = timezone.now()
    if not s.enabled:
        level, text = 'off', 'Automatic backup is off.'
    elif s.pending_file:
        level, text = 'warn', 'Waiting for the backup drive: ' + ', '.join(s.pending_dests)
    elif not s.last_success:
        level, text = 'warn', 'No backup has run yet.'
    else:
        overdue = next_due(s, now) + timedelta(hours=max(2, s.every_hours if s.frequency == 'hours' else 24)) < now
        level, text = ('warn', 'The last backup is overdue.') if overdue else ('ok', 'Backups are up to date.')
    return {'level': level, 'text': text, 'last': s.last_success, 'next': next_due(s, now) if s.enabled else None}


# ------------------------------------------------------------------ restore
def restore(zip_path, with_media=True):
    zip_path = Path(zip_path)
    verify_zip(zip_path)
    safety = local_dir() / f'pre-restore-{timezone.localtime():%Y%m%d-%H%M%S}.zip'
    build_zip(safety, include_media=False, include_csv=False)
    tmp = Path(tempfile.mkdtemp(prefix='tp-rs-', dir=str(local_dir())))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract('database.json', tmp)
            if with_media:
                root = Path(str(getattr(settings, 'MEDIA_ROOT', '') or ''))
                for name in zf.namelist():
                    if name.startswith('media/') and not name.endswith('/'):
                        rel = Path(name[len('media/'):])
                        if '..' in rel.parts or rel.is_absolute():
                            continue
                        target = root / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(name) as src, open(target, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
        with transaction.atomic():
            call_command('flush', interactive=False, verbosity=0, inhibit_post_migrate=True)
            call_command('loaddata', str(tmp / 'database.json'), verbosity=0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return safety
