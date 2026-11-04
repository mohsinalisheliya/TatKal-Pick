from django.core.management.base import BaseCommand

from backups import services


class Command(BaseCommand):
    help = 'Run the automatic backup if it is due (use --force to back up right now). Run every 15 minutes from Windows Task Scheduler.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **opt):
        run = services.check_and_run(force=opt['force'])
        if run is None:
            self.stdout.write('Nothing to do.')
        else:
            self.stdout.write(f'{run.get_status_display()}: {run.message}')
