"""A tiny background thread: once a minute, ask 'is a backup due?'. Safe with several server processes (DB lock)."""
import logging
import threading
import time

log = logging.getLogger('backups')
_started = False


def _loop():
    time.sleep(30)                          # let the server finish starting
    while True:
        try:
            from . import services
            services.check_and_run()
        except Exception as exc:            # tables missing before migrate, DB busy, drive removed ...
            log.warning('backup check skipped: %s', exc)
        time.sleep(60)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name='tatkal-backup', daemon=True).start()
