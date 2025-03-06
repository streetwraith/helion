#!/bin/sh
set -e
gunicorn helion.wsgi --log-file -