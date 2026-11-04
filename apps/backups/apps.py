import os
import sys

from django.apps import AppConfig
from django.conf import settings


class BackupsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backups'

    def ready(self):
        """Start the background checker when the web server runs (not for migrate, tests, shell...).
        Turn off with AUTO_BACKUP_THREAD = False in settings.py and use the scheduled command instead."""
        if not getattr(settings, 'AUTO_BACKUP_THREAD', True):
            return
        argv = ' '.join(sys.argv).lower()
        is_manage = os.path.basename(sys.argv[0]).lower().startswith('manage')
        if is_manage:
            serving = 'runserver' in argv and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in argv)
        else:
            serving = any(w in argv for w in ('gunicorn', 'waitress', 'uvicorn', 'daphne', 'hypercorn', 'mod_wsgi'))
        if serving and 'test' not in argv:
            from . import scheduler
            scheduler.start()
