#!/bin/sh
set -eu

export LIBGL_ALWAYS_SOFTWARE=1
export GDK_BACKEND=x11
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
exec xvfb-run -a -s '-screen 0 1024x768x24 -nolisten tcp' /opt/bambu/AppRun "$@"
