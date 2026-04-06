#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <name> <url> [max_latency_ms]" >&2
  exit 2
fi

name="$1"
url="$2"
max_latency_ms="${3:-2000}"

tmp_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

curl_output="$(curl --silent --show-error --max-time 15 --location --output "$tmp_file" --write-out "%{http_code} %{time_total}" "$url")"
http_code="$(echo "$curl_output" | awk '{print $1}')"
time_total_seconds="$(echo "$curl_output" | awk '{print $2}')"
latency_ms="$(awk -v seconds="$time_total_seconds" 'BEGIN { printf "%.0f", seconds * 1000 }')"

if [ "$http_code" != "200" ]; then
  echo "::error title=Healthcheck failed::$name returned HTTP $http_code for $url"
  echo "Response body:"
  cat "$tmp_file"
  exit 1
fi

if [ "$latency_ms" -gt "$max_latency_ms" ]; then
  echo "::error title=Latency threshold exceeded::$name latency ${latency_ms}ms exceeded ${max_latency_ms}ms for $url"
  echo "Response body:"
  cat "$tmp_file"
  exit 1
fi

if ! grep -Eq '"status"\s*:\s*"(ok|healthy)"' "$tmp_file"; then
  echo "::error title=Unexpected health payload::$name response does not contain status=ok|healthy for $url"
  echo "Response body:"
  cat "$tmp_file"
  exit 1
fi

echo "$name health check passed: http=$http_code latency=${latency_ms}ms threshold=${max_latency_ms}ms"
