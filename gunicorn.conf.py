# Request log to stdout. %(U)s is the path without the query string, and the
# referer is left out, so values carried in URLs are never logged.
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms "%(a)s"'

# PUR-027: /pay and a cancel may wait for Payments, so one slow call must not
# hold every request. Each worker has its own connection and serves one
# request at a time (PUR-024); the migrations take an advisory lock.
workers = 3


def post_worker_init(worker):
    """PUR-027: log whether Payments answers (in gunicorn only, so tests never
    call it)."""
    from purchase.payments_client import check_link

    check_link()
