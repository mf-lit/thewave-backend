#!/bin/bash
# Restic Backup
set -e

send_healthcheck() {
	URI="/${1}"
        [ "$1" == "success" ] && URI=""
	/usr/bin/curl -fsS -m 10 --retry 5 -o /dev/null https://hc-ping.com/${HEALTHCHECK_BACKUP_UUID}${URI}
}

DOCKER_VOLS_DIR="/thewave/docker_vols"

backup_sqlite_databases() {
	local db rel dest
	while IFS= read -r -d '' db; do
		head -c 16 "$db" | LC_ALL=C grep -qa "^SQLite format 3" || continue
		rel="${db#${DOCKER_VOLS_DIR}/}"
		dest="${DB_BACKUP_DIR}/${rel}"
		mkdir -p "$( /usr/bin/dirname -- "$dest" )"
		/usr/bin/sqlite3 "$db" ".backup '${dest}'"
	done < <(find "$DOCKER_VOLS_DIR" -type f \( -name "*.sqlite" -o -name "*.sqlite3" -o -name "*.db" -o -name "*.db3" \) -print0)
}

SCRIPT_DIR=$( cd -- "$( /usr/bin/dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

set -o allexport
source ${SCRIPT_DIR}/restic.env
set +o allexport

send_healthcheck start
backup_sqlite_databases && \
/usr/bin/restic -r ${RESTIC_REPOSITORY} backup --cache-dir=$RESTIC_CACHE_DIR -x --exclude-caches --exclude-if-present .nobackup --files-from ${SCRIPT_DIR}/restic.list && \
/usr/bin/restic -r ${RESTIC_REPOSITORY} forget --cache-dir=$RESTIC_CACHE_DIR --keep-within-hourly 3d --keep-within-daily 30d --keep-weekly 12 --keep-monthly 12 --keep-yearly 1 --tag "" --prune && \
send_healthcheck success || send_healthcheck fail
