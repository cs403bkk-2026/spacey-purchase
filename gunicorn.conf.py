# Request log to stdout. %(U)s is the path without the query string, and the
# referer is left out, so values carried in URLs are never logged.
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms "%(a)s"'
