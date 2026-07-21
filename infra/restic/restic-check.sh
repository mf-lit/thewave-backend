#!/bin/bash
# Restic Backup
set -e

send_healthcheck() {
	URI="/${1}"
        [ "$1" == "success" ] && URI=""
	curl -fsS -m 10 --retry 5 -o /dev/null https://hc-ping.com/${HEALTHCHECK_CHECK_UUID}${URI}
}

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

set -o allexport
source ${SCRIPT_DIR}/restic.env
set +o allexport

send_healthcheck start
restic -r ${RESTIC_REPOSITORY} check --cache-dir=$RESTIC_CACHE_DIR --read-data && \
send_healthcheck success || send_healthcheck fail 
