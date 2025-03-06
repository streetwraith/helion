#!/bin/sh
set -e
gunicorn helion.wsgi --timeout 600 --log-file -