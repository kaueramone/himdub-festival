"""Render process settings; exclude query strings and credentials from access logs."""
import os
import re
from gunicorn.glogging import Logger


class PrivatePathLogger(Logger):
    def atoms(self, response, request, environ, request_time):
        atoms = super().atoms(response, request, environ, request_time)
        atoms['U'] = re.sub(r'(/admin/join/|/account/access/)[^/]+', r'\1[private]', atoms.get('U', ''))
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
