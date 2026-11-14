"""Render process settings; exclude query strings and credentials from access logs."""
import os
import re
from gunicorn.glogging import Logger

# Register the language refresher in each worker, including --preload supplied
# through GUNICORN_CMD_ARGS. Never leave it running only in master memory.
os.environ['HIMDUB_GUNICORN_WORKER_HOOKS'] = 'true'


def post_worker_init(worker):
    from auto_translation import start_translation_worker
    start_translation_worker(worker.wsgi)
    from gallery_imports import start_gallery_worker
    start_gallery_worker(worker.wsgi)


class PrivatePathLogger(Logger):
    def atoms(self, response, request, environ, request_time):
        atoms = super().atoms(response, request, environ, request_time)
        atoms['U'] = re.sub(r'(/admin/join/|/admin/reset-password/|/account/access/)[^/]+', r'\1[private]', atoms.get('U', ''))
        return atoms


logger_class = PrivatePathLogger

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = 1
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"
access_log_format = "%(m)s %(U)s %(s)s %(L)s"
capture_output = True
