#!/usr/bin/env bash
set -euo pipefail

SOLR_HOME="${SOLR_HOME:-/var/solr/data}"
OCR_LIVE_CORE_NAME="${OCR_LIVE_CORE_NAME:-ocr}"
SOLR_DOCKER_ENTRYPOINT="${OCR_SOLR_DOCKER_ENTRYPOINT:-/opt/solr/docker/scripts/docker-entrypoint.sh}"

refuse_duplicate_definitions() {
    local core_name="$1" properties_file declared_name
    CORE_DEFINITION_COUNT=0
    [[ -d "$SOLR_HOME" ]] || return 0
    while IFS= read -r -d '' properties_file; do
        declared_name="$(grep '^name=' "$properties_file" | sed 's/^name=//' || true)"
        [[ -n "$declared_name" ]] || declared_name="$(basename "$(dirname "$properties_file")")"
        if [[ "$declared_name" == "$core_name" ]]; then
            CORE_DEFINITION_COUNT=$((CORE_DEFINITION_COUNT + 1))
        fi
    done < <(find "$SOLR_HOME" -type f -name core.properties -print0)
    (( CORE_DEFINITION_COUNT <= 1 )) && return 0
    printf 'OCR Solr startup error: core "%s" has %s core.properties definitions under %s; refusing to start.\n' \
        "$core_name" "$CORE_DEFINITION_COUNT" "$SOLR_HOME" >&2
    return 1
}

start_solr() {
    "$SOLR_DOCKER_ENTRYPOINT" "$@" &
    SOLR_PID=$!
}

wait_for_live_core() {
    local deadline=$((SECONDS + ${OCR_SOLR_STARTUP_TIMEOUT:-120})) \
        ping_url="http://127.0.0.1:${SOLR_PORT:-8983}/solr/${1}/admin/ping"
    while (( SECONDS < deadline )); do
        kill -0 "$SOLR_PID" 2>/dev/null || return 1
        curl --fail --silent --max-time 1 "$ping_url" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

live_core_has_init_failure() {
    local core_name="$1" status_url response
    status_url="http://127.0.0.1:${SOLR_PORT:-8983}/solr/admin/cores"
    response="$(curl --fail --silent --max-time 1 --get "$status_url" \
        --data-urlencode action=STATUS --data-urlencode "core=$core_name" \
        --data-urlencode wt=json --data-urlencode indent=false || true)"
    grep -Eq '"initFailures"[[:space:]]*:[[:space:]]*\{[[:space:]]*"' <<<"$response"
}

stop_solr() {
    kill -TERM "$SOLR_PID" 2>/dev/null || true
    wait "$SOLR_PID" 2>/dev/null || true
}

run_live_core() {
    local core_name="$1" error="did not become ready"
    shift
    start_solr "$@"
    trap 'stop_solr' INT TERM
    if ! wait_for_live_core "$core_name"; then
        live_core_has_init_failure "$core_name" && error="reports initFailures"
        printf 'OCR Solr startup error: live core "%s" %s; refusing to serve.\n' \
            "$core_name" "$error" >&2
        stop_solr
        return 1
    fi
    wait "$SOLR_PID"
}

main() {
    if [[ "${1:-}" != solr-precreate || "$#" -lt 2 ]]; then
        exec "$SOLR_DOCKER_ENTRYPOINT" "$@"
    fi
    local requested_core="$2"
    refuse_duplicate_definitions "$requested_core" || return 1
    if (( CORE_DEFINITION_COUNT == 1 )); then set -- solr-foreground; fi
    [[ "$requested_core" == "$OCR_LIVE_CORE_NAME" ]] || exec "$SOLR_DOCKER_ENTRYPOINT" "$@"
    run_live_core "$requested_core" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
