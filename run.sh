#!/bin/sh
set -e
gunicorn helion.wsgi --timeout 300 --log-file -