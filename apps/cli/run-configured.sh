#!/usr/bin/env bash
# Use the same deployment environment for run, answer and resume.
set -eu

if [ "$#" -lt 2 ]; then
    echo "Usage: bash run-configured.sh CONFIG COMMAND [ARG...]" >&2
    exit 2
fi

config_file=$1
shift
case "$config_file" in
    /*) ;;
    *) config_file="$PWD/$config_file" ;;
esac

set -a
source "$config_file"
set +a
exec resagent2 "$@"
