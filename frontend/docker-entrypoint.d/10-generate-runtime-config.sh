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
    env | sed -n 's/^\(VITE_[A-Z0-9_]*\)=.*/\1/p' | sort -u | while IFS= read -r key; do
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
