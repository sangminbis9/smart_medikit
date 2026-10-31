#!/bin/sh
# Python 시작 전에 라이브러리 검색 경로를 준비해 Linux 동적 로더에 반영한다.
set -eu
mkdir -p /tmp/medikit-ocr/lib
export LD_LIBRARY_PATH="/tmp/medikit-ocr/lib:${LD_LIBRARY_PATH:-/var/lang/lib:/lib64:/usr/lib64:/var/runtime:/var/task:/opt/lib}"
exec "$@"
