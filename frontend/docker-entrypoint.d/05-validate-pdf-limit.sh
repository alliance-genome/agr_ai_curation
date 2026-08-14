#!/bin/sh
set -eu

case "${PDF_MAX_FILE_SIZE_BYTES}" in
    ''|*[!0-9]*)
        echo >&2 "PDF_MAX_FILE_SIZE_BYTES must be a positive integer byte count; got '${PDF_MAX_FILE_SIZE_BYTES}'."
        exit 1
        ;;
esac

if [ "${PDF_MAX_FILE_SIZE_BYTES}" -le 0 ]; then
    echo >&2 "PDF_MAX_FILE_SIZE_BYTES must be greater than zero; got '${PDF_MAX_FILE_SIZE_BYTES}'."
    exit 1
fi
