#!/bin/sh
set -eu

persisted_pdf_file_size_capacity=2147483647

reject_unpersistable_pdf_size() {
    echo >&2 "PDF_MAX_FILE_SIZE_BYTES must not exceed the persisted file-size capacity of ${persisted_pdf_file_size_capacity} bytes; got '${PDF_MAX_FILE_SIZE_BYTES}'."
    exit 1
}

case "${PDF_MAX_FILE_SIZE_BYTES}" in
    ''|*[!0-9]*)
        echo >&2 "PDF_MAX_FILE_SIZE_BYTES must be a positive integer byte count; got '${PDF_MAX_FILE_SIZE_BYTES}'."
        exit 1
        ;;
esac

normalized_pdf_max_file_size_bytes="${PDF_MAX_FILE_SIZE_BYTES}"
while [ "${normalized_pdf_max_file_size_bytes#0}" != "${normalized_pdf_max_file_size_bytes}" ]; do
    normalized_pdf_max_file_size_bytes="${normalized_pdf_max_file_size_bytes#0}"
done
if [ -z "${normalized_pdf_max_file_size_bytes}" ]; then
    normalized_pdf_max_file_size_bytes=0
fi

# Bound digit width before numeric comparison so untrusted input cannot exceed
# the shell's integer range.
if [ "${#normalized_pdf_max_file_size_bytes}" -gt "${#persisted_pdf_file_size_capacity}" ]; then
    reject_unpersistable_pdf_size
fi

if [ "${normalized_pdf_max_file_size_bytes}" -le 0 ]; then
    echo >&2 "PDF_MAX_FILE_SIZE_BYTES must be greater than zero; got '${PDF_MAX_FILE_SIZE_BYTES}'."
    exit 1
fi

if [ "${normalized_pdf_max_file_size_bytes}" -gt "${persisted_pdf_file_size_capacity}" ]; then
    reject_unpersistable_pdf_size
fi
