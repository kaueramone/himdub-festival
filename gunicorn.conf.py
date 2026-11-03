"""Render process settings; exclude query strings and credentials from access logs."""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = 1
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"
access_log_format = "%(m)s %(U)s %(s)s %(L)s"
capture_output = True
