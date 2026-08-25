#!/bin/sh
set -eu

runtime_config_path=/usr/share/nginx/html/runtime-config.js
runtime_config_temp_path="${runtime_config_path}.tmp"
runtime_config_global=__APP_RUNTIME_CONFIG__

escape_javascript_string() {
    awk 'BEGIN { ORS = "" }
        {
            if (NR > 1) printf "\\n"
            gsub(/\\/, "\\\\")
            gsub(/\"/, "\\\"")
            gsub(/\r/, "\\r")
            gsub(/\t/, "\\t")
            printf "%s", $0
        }'
}

{
    printf 'window.%s = Object.freeze({\n' "${runtime_config_global}"
    first=1
    for key in ${FRONTEND_RUNTIME_CONFIG_KEYS:-}; do
        case "${key}" in
            VITE_*) ;;
            *)
                printf 'Invalid frontend runtime configuration key: %s\n' "${key}" >&2
                exit 1
                ;;
        esac
        case "${key#VITE_}" in
            ''|*[!A-Z0-9_]*)
                printf 'Invalid frontend runtime configuration key: %s\n' "${key}" >&2
                exit 1
                ;;
        esac
        printenv "${key}" >/dev/null || continue
        value="$(printenv "${key}")"
        escaped_value="$(printf '%s' "${value}" | escape_javascript_string)"
        [ -n "${first}" ] || printf ',\n'
        first=''
        printf '  "%s": "%s"' "${key}" "${escaped_value}"
    done
    printf '\n%s\n' '});'
} >"${runtime_config_temp_path}"

chmod 644 "${runtime_config_temp_path}"
mv "${runtime_config_temp_path}" "${runtime_config_path}"
