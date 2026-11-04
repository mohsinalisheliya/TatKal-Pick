from django.core.management.base import BaseCommand, CommandError

from backups import services


class Command(BaseCommand):
    help = 'Restore a backup zip. Replaces ALL data in the current database.'

    def add_arguments(self, parser):
        parser.add_argument('zipfile')
        parser.add_argument('--no-media', action='store_true')
        parser.add_argument('--yes', action='store_true', help='do not ask')

    def handle(self, *args, **opt):
        if not opt['yes']:
            if input('This REPLACES all current data with the backup. Type YES to continue: ').strip() != 'YES':
                raise CommandError('Cancelled.')
        try:
            safety = services.restore(opt['zipfile'], with_media=not opt['no_media'])
        except Exception as exc:
            raise CommandError(f'Restore failed, nothing was changed if the error came before the data step: {exc}')
        self.stdout.write(self.style.SUCCESS(f'Restored. A copy of the data from before the restore is in {safety}'))
