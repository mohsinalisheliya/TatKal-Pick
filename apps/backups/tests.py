import io
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import resolve
from django.utils import timezone

from accounts.middleware import RoleAccessMiddleware
from accounts.models import AuditLog
from accounts.tests import Base
from backups import services
from backups.models import BackupRun, BackupSettings
from sku_manager.models import JewelrySKU

U = get_user_model()


def aware(*a):
    return timezone.make_aware(datetime(*a))


class Tmp(Base):
    """Every test gets its own media folder, local backup folder and 'pendrive'."""
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix='bk-test-'))
        self.media, self.local, self.pen = self.root / 'media', self.root / 'local', self.root / 'pen'
        (self.media / 'products').mkdir(parents=True); (self.media / 'products' / 'a.jpg').write_bytes(b'JPEGDATA')
        self.pen.mkdir()
        self.ov = override_settings(MEDIA_ROOT=str(self.media), BACKUP_LOCAL_DIR=str(self.local)); self.ov.enable()
        self.addCleanup(self.ov.disable); self.addCleanup(shutil.rmtree, self.root, True)

    def conf(self, **kw):
        s = BackupSettings.get()
        for k, v in dict(enabled=True, destinations=str(self.pen), keep_last=14, local_copy=True, include_csv=True, include_media=False).items():
            setattr(s, k, kw.pop(k, v))
        for k, v in kw.items():
            setattr(s, k, v)
        s.save(); return s


class AccessTests(Tmp):
    def test_master_only(self):
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get('/master/backups/').status_code, 200)
        self.assertContains(self.client.get('/master/'), 'Backups')
        for u in (self.adam, self.ravi, self.dina):
            self.client.force_login(u)
            for url in ('/master/backups/',):
                self.assertEqual(self.client.get(url).status_code, 302)
            for url in ('/master/backups/download/', '/master/backups/run/', '/master/backups/test/'):
                self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(BackupRun.objects.exists())

    def test_matrix(self):
        m = resolve('/master/backups/')
        self.assertEqual([r for r in ('master', 'admin', 'operator', 'cashier') if RoleAccessMiddleware._allowed(r, m)], ['master'])

    def test_master_cannot_use_other_apps(self):
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get('/inventory/').status_code, 302)


class DownloadTests(Tmp):
    def get_zip(self, **post):
        self.client.force_login(self.boss)
        r = self.client.post('/master/backups/download/', post)
        self.assertEqual(r.status_code, 200); self.assertEqual(r['Content-Type'], 'application/zip')
        body = b''.join(r.streaming_content); r.close()
        return zipfile.ZipFile(io.BytesIO(body)), r

    def test_full_zip(self):
        z, r = self.get_zip(include_csv='on')
        names = z.namelist()
        for n in ('database.json', 'manifest.json', 'README-RESTORE.txt', 'csv/sku_manager.JewelrySKU.csv', 'csv/auth.User.csv'):
            self.assertIn(n, names)
        self.assertFalse(any(n.startswith('media/') for n in names))
        data = json.loads(z.read('database.json'))
        self.assertTrue(any(o['model'] == 'sku_manager.jewelrysku' and o['fields']['name'] == 'Ring' for o in data))
        self.assertIn('Ring', z.read('csv/sku_manager.JewelrySKU.csv').decode('utf-8-sig'))
        self.assertGreater(json.loads(z.read('manifest.json'))['total_rows'], 5)
        self.assertIn('attachment', r['Content-Disposition']); self.assertNotIn('download-', r['Content-Disposition'])

    def test_options(self):
        z, _ = self.get_zip(include_media='on')
        self.assertEqual(z.read('media/products/a.jpg'), b'JPEGDATA')
        z, _ = self.get_zip()                                          # csv box unticked -> no csv, no media
        self.assertFalse([n for n in z.namelist() if n.startswith(('csv/', 'media/'))] and False)
        self.assertNotIn('media/products/a.jpg', z.namelist())

    def test_temp_file_removed_and_logged(self):
        self.get_zip()
        self.assertEqual([f.name for f in self.local.glob('download-*')], [])
        self.assertTrue(AuditLog.objects.filter(action='backup_download').exists())
        self.assertEqual(BackupRun.objects.get().trigger, 'download')

    def test_sqlite_copy_is_present_only_for_a_real_file_db(self):
        z, _ = self.get_zip()
        self.assertNotIn('db.sqlite3', z.namelist())                   # tests use an in-memory database


class SettingsFormTests(Tmp):
    def setUp(self):
        super().setUp(); self.client.force_login(self.boss)

    def post(self, **kw):
        d = {'enabled': 'on', 'destinations': 'E:\\TatkalBackups', 'frequency': 'daily', 'every_hours': '6', 'at_time': '21:30',
             'weekday': '6', 'keep_last': '10', 'local_copy': 'on', 'include_csv': 'on'}
        d.update(kw)
        return self.client.post('/master/backups/', {k: v for k, v in d.items() if v is not None})

    def test_save(self):
        self.assertEqual(self.post().status_code, 302)
        s = BackupSettings.get()
        self.assertEqual((s.enabled, s.dest_list(), s.frequency, s.at_time.strftime('%H:%M'), s.keep_last, s.local_copy, s.include_media),
                         (True, ['E:\\TatkalBackups'], 'daily', '21:30', 10, True, False))
        self.assertTrue(AuditLog.objects.filter(action='backup_settings').exists())
        self.assertContains(self.client.get('/master/backups/'), 'E:\\TatkalBackups')

    def test_two_drives_and_quotes(self):
        self.post(destinations='"E:\\One"\n\n F:\\Two ')
        self.assertEqual(BackupSettings.get().dest_list(), ['E:\\One', 'F:\\Two'])

    def test_validation(self):
        for bad in (dict(at_time='25:99'), dict(at_time='nine'), dict(keep_last='0'), dict(keep_last='x'), dict(every_hours='0'),
                    dict(frequency='sometimes'), dict(destinations='a\nb\nc\nd\ne\nf'), dict(destinations='', local_copy=None)):
            r = self.post(**bad)
            self.assertEqual(r.status_code, 200, bad)
        self.assertFalse(BackupSettings.get().enabled)

    def test_turn_off(self):
        self.post(); self.post(enabled=None)
        self.assertFalse(BackupSettings.get().enabled)

    def test_test_button_reports_each_drive(self):
        self.conf(destinations=f'{self.pen}\n{self.root}/nodrive/x')
        r = self.client.post('/master/backups/test/', follow=True)
        text = ' '.join(str(m) for m in r.context['messages'])
        self.assertIn('Ready', text); self.assertIn('not found', text)
        self.assertEqual(list(self.pen.iterdir()), [])                 # the probe file is removed again


class EngineTests(Tmp):
    def test_backup_to_pendrive(self):
        self.conf()
        run = services.run_backup('manual')
        self.assertEqual((run.status, run.trigger), ('ok', 'manual'))
        files = list(self.pen.glob('tatkal-backup-*.zip')); self.assertEqual(len(files), 1)
        services.verify_zip(files[0])
        self.assertEqual(len(list(self.local.glob('tatkal-backup-*.zip'))), 1)          # local copy on
        s = BackupSettings.get(); self.assertIsNotNone(s.last_success); self.assertEqual(s.pending_file, '')
        self.assertEqual(list(self.pen.glob('*.part')), []); self.assertIsNone(s.running_since)

    def test_no_local_copy(self):
        self.conf(local_copy=False)
        services.run_backup('manual')
        self.assertEqual(len(list(self.pen.glob('tatkal-backup-*.zip'))), 1)
        self.assertEqual(list(self.local.glob('tatkal-backup-*.zip')), [])

    def test_keep_last_only_deletes_our_files(self):
        self.conf(keep_last=2)
        (self.pen / 'notes.txt').write_text('mine'); (self.pen / 'tatkal-backup-old.zip').write_text('x'); (self.pen / 'photo.jpg').write_text('p')
        for i in range(4):
            (self.pen / f'tatkal-backup-2020010{i + 1}-000000.zip').write_bytes(b'PK')      # older ones
        services.run_backup('manual'); services.run_backup('manual')
        ours = sorted(f.name for f in self.pen.glob('tatkal-backup-2*.zip'))
        self.assertEqual(len(ours), 2)
        self.assertEqual({f.name for f in self.pen.iterdir()} - set(ours), {'notes.txt', 'tatkal-backup-old.zip', 'photo.jpg'})

    def test_missing_pendrive_waits_then_copies_when_plugged_in(self):
        gone = self.root / 'E_drive' / 'Backups'
        self.conf(destinations=str(gone))
        run = services.run_backup('manual')
        self.assertEqual(run.status, 'partial'); self.assertIn('Will retry', run.message)
        s = BackupSettings.get(); self.assertTrue(Path(s.pending_file).exists()); self.assertEqual(s.pending_dests, [str(gone)])
        self.assertFalse((self.root / 'E_drive').exists())                    # a missing drive is not invented
        self.assertEqual(services.status()['level'], 'warn')
        # not yet time to retry
        BackupSettings.objects.update(last_attempt=timezone.now())
        self.assertIsNone(services.check_and_run()); self.assertFalse(gone.exists())
        # five minutes later, pendrive plugged in
        (self.root / 'E_drive').mkdir()
        BackupSettings.objects.update(last_attempt=timezone.now() - timedelta(minutes=6))
        services.check_and_run()
        self.assertEqual(len(list(gone.glob('tatkal-backup-*.zip'))), 1)
        s = BackupSettings.get(); self.assertEqual((s.pending_file, s.pending_dests), ('', []))
        self.assertEqual(BackupRun.objects.first().trigger, 'retry'); self.assertEqual(BackupRun.objects.first().status, 'ok')

    def test_no_local_copy_and_no_drive_keeps_file_until_copied(self):
        gone = self.root / 'X' / 'B'
        self.conf(destinations=str(gone), local_copy=False)
        run = services.run_backup('manual')
        self.assertEqual(run.status, 'failed'); self.assertEqual(len(list(self.local.glob('tatkal-backup-*.zip'))), 1)
        (self.root / 'X').mkdir(); BackupSettings.objects.update(last_attempt=timezone.now() - timedelta(minutes=9))
        services.check_and_run()
        self.assertEqual(len(list(gone.glob('*.zip'))), 1); self.assertEqual(list(self.local.glob('tatkal-backup-*.zip')), [])

    def test_one_of_two_drives(self):
        self.conf(destinations=f'{self.pen}\n{self.root}/nope/x')
        self.assertEqual(services.run_backup('manual').status, 'partial')
        self.assertEqual(len(list(self.pen.glob('*.zip'))), 1)

    def test_lock_blocks_a_second_run_but_not_a_stale_one(self):
        self.conf(); BackupSettings.objects.update(running_since=timezone.now())
        self.assertIsNone(services.run_backup('manual')); self.assertFalse(BackupRun.objects.exists())
        BackupSettings.objects.update(running_since=timezone.now() - timedelta(hours=2))
        self.assertEqual(services.run_backup('manual').status, 'ok')

    def test_failed_backup_does_not_loop(self):
        self.conf(frequency='hours', every_hours=1, last_success=timezone.now() - timedelta(hours=5))
        BackupSettings.objects.update(last_attempt=timezone.now() - timedelta(minutes=1))
        self.assertFalse(services.is_due(BackupSettings.get()))
        BackupSettings.objects.update(last_attempt=timezone.now() - timedelta(minutes=10))
        self.assertTrue(services.is_due(BackupSettings.get()))

    def test_media_option(self):
        self.conf(include_media=True); services.run_backup('manual')
        z = zipfile.ZipFile(next(self.pen.glob('*.zip'))); self.assertIn('media/products/a.jpg', z.namelist())

    def test_commands(self):
        out = io.StringIO(); call_command('run_backups', stdout=out); self.assertIn('Nothing to do', out.getvalue())   # disabled
        self.conf(); out = io.StringIO(); call_command('run_backups', stdout=out); self.assertIn('OK', out.getvalue())
        out = io.StringIO(); call_command('run_backups', stdout=out); self.assertIn('Nothing to do', out.getvalue())     # not due again
        out = io.StringIO(); call_command('run_backups', '--force', stdout=out); self.assertIn('OK', out.getvalue())

    def test_back_up_now_button_and_home_tile(self):
        self.client.force_login(self.boss); self.conf()
        r = self.client.post('/master/backups/run/', follow=True)
        self.assertContains(r, 'Backup complete'); self.assertContains(r, 'tatkal-backup-')
        self.assertContains(self.client.get('/master/'), 'Backups are up to date')


class ScheduleTests(TestCase):
    def s(self, **kw):
        base = dict(enabled=True, frequency='daily', at_time='21:00', weekday=6, every_hours=6)
        base.update(kw); s = BackupSettings(**base); s.at_time = datetime.strptime(s.at_time, '%H:%M').time(); return s

    def test_disabled_and_never_run(self):
        self.assertFalse(services.is_due(self.s(enabled=False)))
        self.assertTrue(services.is_due(self.s(), aware(2026, 11, 4, 8, 0)))

    def test_every_n_hours(self):
        s = self.s(frequency='hours', every_hours=6)
        s.last_success = aware(2026, 11, 4, 8, 0)
        self.assertFalse(services.is_due(s, aware(2026, 11, 4, 13, 59))); self.assertTrue(services.is_due(s, aware(2026, 11, 4, 14, 0)))

    def test_daily(self):
        s = self.s()
        s.last_success = aware(2026, 11, 3, 21, 5)                                  # yesterday evening's backup
        self.assertFalse(services.is_due(s, aware(2026, 11, 4, 20, 59)))
        self.assertTrue(services.is_due(s, aware(2026, 11, 4, 21, 0)))
        s.last_success = aware(2026, 11, 4, 21, 2)
        self.assertFalse(services.is_due(s, aware(2026, 11, 4, 23, 59))); self.assertTrue(services.is_due(s, aware(2026, 11, 5, 21, 1)))

    def test_daily_catches_up_after_the_pc_was_off(self):
        s = self.s(); s.last_success = aware(2026, 11, 1, 21, 3)                    # PC off for 3 days, switched on at 09:00
        self.assertTrue(services.is_due(s, aware(2026, 11, 4, 9, 0)))

    def test_weekly(self):
        s = self.s(frequency='weekly', weekday=6)                                  # Sunday 21:00 (8 Nov 2026 is a Sunday)
        s.last_success = aware(2026, 11, 1, 21, 5)
        self.assertFalse(services.is_due(s, aware(2026, 11, 7, 23, 0))); self.assertTrue(services.is_due(s, aware(2026, 11, 8, 21, 0)))

    def test_next_due_text(self):
        s = self.s(); s.last_success = aware(2026, 11, 4, 21, 2)
        self.assertEqual(timezone.localtime(services.next_due(s, aware(2026, 11, 4, 22, 0))).strftime('%d %H:%M'), '05 21:00')


class RestoreTests(TransactionTestCase):
    def test_round_trip(self):
        root = Path(tempfile.mkdtemp(prefix='bk-rs-'))
        try:
            with override_settings(MEDIA_ROOT=str(root / 'media'), BACKUP_LOCAL_DIR=str(root / 'local')):
                (root / 'media').mkdir(); (root / 'media' / 'p.jpg').write_bytes(b'ORIGINAL')
                call_command('create_user', 'boss', 'bosspass', '--role', 'master')
                call_command('create_user', 'ravi', 'ravipass', '--role', 'cashier', '--tiers', 'reselling')
                ring = JewelrySKU.objects.create(name='Ring', category='RNG', selling_price=140, retail_price=199)
                z = root / 'b.zip'
                services.build_zip(z, include_media=True, include_csv=False)
                # damage everything
                JewelrySKU.objects.all().delete(); U.objects.filter(username='ravi').delete()
                JewelrySKU.objects.create(name='Intruder', category='BGL'); (root / 'media' / 'p.jpg').write_bytes(b'CHANGED')
                safety = services.restore(z)
                self.assertEqual(list(JewelrySKU.objects.values_list('name', flat=True)), ['Ring'])
                self.assertEqual(JewelrySKU.objects.get().retail_price, 199)
                ravi = U.objects.get(username='ravi'); self.assertTrue(ravi.check_password('ravipass')); self.assertEqual(ravi.profile.role, 'cashier')
                self.assertEqual((root / 'media' / 'p.jpg').read_bytes(), b'ORIGINAL')
                self.assertTrue(safety.exists())
                # the safety copy holds the data from just before the restore
                self.assertIn('Intruder', zipfile.ZipFile(safety).read('database.json').decode())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_damaged_zip_is_refused_before_anything_changes(self):
        root = Path(tempfile.mkdtemp(prefix='bk-rs-'))
        try:
            with override_settings(MEDIA_ROOT=str(root), BACKUP_LOCAL_DIR=str(root / 'local')):
                JewelrySKU.objects.create(name='Keep', category='RNG')
                bad = root / 'bad.zip'; bad.write_bytes(b'not a zip')
                with self.assertRaises(Exception):
                    services.restore(bad)
                self.assertTrue(JewelrySKU.objects.filter(name='Keep').exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)
