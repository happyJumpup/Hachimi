#!/usr/bin/env bash

set -Eeuo pipefail

readonly image_ref="${1:?usage: verify-competition-image.sh <image-ref>}"
readonly smoke_name="hachimi-image-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
readonly model_trap_name="${smoke_name}-model-trap"
readonly smoke_network="${smoke_name}-network"
readonly work_dir="$(mktemp -d)"

docker_cmd() {
  MSYS_NO_PATHCONV=1 docker "$@"
}

if python3 --version >/dev/null 2>&1; then
  readonly python_cmd="python3"
elif python --version >/dev/null 2>&1; then
  readonly python_cmd="python"
else
  printf 'competition image verification failed: Python 3 is required\n' >&2
  exit 2
fi

cleanup() {
  docker_cmd rm --force "${smoke_name}" >/dev/null 2>&1 || true
  docker_cmd rm --force "${model_trap_name}" >/dev/null 2>&1 || true
  docker_cmd network rm "${smoke_network}" >/dev/null 2>&1 || true
  rm -rf "${work_dir}"
}
trap cleanup EXIT

fail() {
  printf 'competition image verification failed: %s\n' "$1" >&2
  if docker_cmd inspect "${smoke_name}" >/dev/null 2>&1; then
    docker_cmd logs "${smoke_name}" >&2 || true
  fi
  if docker_cmd inspect "${model_trap_name}" >/dev/null 2>&1; then
    docker_cmd logs "${model_trap_name}" >&2 || true
  fi
  exit 1
}

docker_cmd image inspect "${image_ref}" >/dev/null

readonly configured_user="$(docker_cmd image inspect --format '{{.Config.User}}' "${image_ref}")"
if [[ "${configured_user}" != "10001:10001" ]]; then
  fail "runtime user must be 10001:10001, got '${configured_user}'"
fi

readonly saved_image="${work_dir}/image.tar"
docker_cmd image save "${image_ref}" >"${saved_image}"
"${python_cmd}" "$(dirname "$0")/audit-image-layers.py" "${saved_image}"

audit_output="$({
  docker_cmd run --rm --entrypoint /bin/sh "${image_ref}" -eu -c '
    if [ ! -f /workspace/contracts/gymti-questionnaire.v1.json ]; then
      printf "%s\n" /workspace/contracts/gymti-questionnaire.v1.json
    fi

    find /workspace -type f \( \
      -name ".env" -o -name ".env.*" -o \
      -name "*.mp4" -o -name "*.mov" -o -name "*.mkv" -o \
      -name "*.avi" -o -name "*.webm" -o -name "*.wav" -o \
      -name "*.mp3" -o -name "*.ogg" -o -name "*.m4a" -o \
      -name "*.aac" -o -name "*.flac" -o -name "*.trace" \
    \) -print

    # Registered WebP files have already been checked by path-independent SHA-256
    # in audit-image-layers.py. Keep this runtime pass for visual formats that are
    # never allowed in the release image and for subtitle artifacts.
    find /workspace -type f \( \
      -name "*.bmp" -o -name "*.gif" -o -name "*.jpeg" -o \
      -name "*.jpg" -o -name "*.png" -o -name "*.tif" -o \
      -name "*.tiff" -o -name "*.ass" -o -name "*.srt" -o \
      -name "*.ssa" -o -name "*.vtt" \
    \) -print

    find /workspace -type f \( \
      -iname "*transcript*" -o -iname "*transcription*" -o -iname "*subtitle*" \
    \) -print

    find /workspace/apps/web/dist -type f -name "*.map" -print

    for forbidden_path in \
      /workspace/.git \
      /workspace/.github \
      /workspace/docs \
      /workspace/node_modules \
      /workspace/apps/web/tests \
      /workspace/services/analysis-api/tests \
      /workspace/services/analysis-api/scripts; do
      if [ -e "${forbidden_path}" ]; then
        printf "%s\n" "${forbidden_path}"
      fi
    done

    find / -xdev -type f \( \
      -name "ffmpeg" -o -name "ffmpeg.exe" -o \
      -path "*/imageio_ffmpeg/binaries/ffmpeg-*" \
    \) -print 2>/dev/null | while IFS= read -r ffmpeg_path; do
      if [ "${ffmpeg_path}" != "/opt/trainpal/ffmpeg/bin/ffmpeg" ]; then
        printf "%s\n" "${ffmpeg_path}"
      fi
    done

    find /workspace/tmp/analysis-runs -mindepth 1 -print -quit
  '
} | sed '/^[[:space:]]*$/d')"

if [[ -n "${audit_output}" ]]; then
  printf '%s\n' "${audit_output}" >&2
  fail "forbidden release content is present"
fi

docker_cmd run --rm \
  --entrypoint /workspace/services/analysis-api/.venv/bin/python \
  "${image_ref}" \
  -c 'from pathlib import Path; from hakimi_analysis.readiness import validate_ffmpeg_build_receipt; raise SystemExit(0 if validate_ffmpeg_build_receipt(Path("/opt/trainpal/ffmpeg/bin/ffmpeg"), Path("/opt/trainpal/ffmpeg/receipt.json")) else 1)' \
  || fail "registered FFmpeg build receipt is invalid"

docker_cmd network create "${smoke_network}" >/dev/null
docker_cmd run --detach \
  --name "${model_trap_name}" \
  --network "${smoke_network}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m,mode=1777 \
  --entrypoint services/analysis-api/.venv/bin/python \
  "${image_ref}" \
  -c '
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class TrapHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        print("unexpected-gymti-model-call", flush=True)
        self.send_response(503)
        self.end_headers()

    def log_message(self, _format, *_args):
        return

ThreadingHTTPServer(("0.0.0.0", 8081), TrapHandler).serve_forever()
' >/dev/null
if [[ "$(docker_cmd inspect --format '{{.State.Running}}' "${model_trap_name}")" != "true" ]]; then
  fail "GYMTI model trap exited before the image smoke"
fi

docker_cmd run --detach \
  --name "${smoke_name}" \
  --network "${smoke_network}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
  --tmpfs /workspace/tmp/analysis-runs:rw,noexec,nosuid,size=128m,uid=10001,gid=10001,mode=0700 \
  --env APP_ENV=production \
  --env ANALYSIS_PROVIDER=cloud \
  --env ARK_API_KEY=image-audit-provider-key \
  --env VOLC_ASR_API_KEY=image-audit-asr-key \
  --env JUDGE_ACCESS_CODE=image-audit-judge-code-32-bytes \
  --env ACCESS_COOKIE_SECRET=image-audit-cookie-secret-at-least-32-bytes \
  --env CORS_ORIGINS=https://image-audit.invalid \
  --env LOCAL_UPLOAD_ENABLED=true \
  --env TRUSTED_PROXY_CIDRS= \
  --env PUBLIC_ANALYSIS_CONCURRENCY=0 \
  --env JUDGE_ANALYSIS_CONCURRENCY=3 \
  --env GYMTI_LLM_ENABLED=true \
  --env GYMTI_LLM_RETENTION_CONFIRMED=true \
  --env GYMTI_LLM_API_KEY=release-smoke-trap-key \
  --env GYMTI_LLM_BASE_URL="http://${model_trap_name}:8081/v1" \
  --env GYMTI_LLM_MAX_ATTEMPTS=1 \
  --env GYMTI_LLM_TIMEOUT_SECONDS=1 \
  --publish 127.0.0.1::8000 \
  "${image_ref}" >/dev/null

readonly port_binding="$(docker_cmd port "${smoke_name}" 8000/tcp | head -n 1)"
readonly host_port="${port_binding##*:}"
if [[ ! "${host_port}" =~ ^[0-9]+$ ]]; then
  fail "could not resolve the published API port"
fi
readonly base_url="http://127.0.0.1:${host_port}"

healthy=false
for _ in $(seq 1 45); do
  if curl --fail --silent \
    "${base_url}/api/v1/health" \
    --output "${work_dir}/health.json"; then
    healthy=true
    break
  fi
  if [[ "$(docker_cmd inspect --format '{{.State.Running}}' "${smoke_name}")" != "true" ]]; then
    fail "container exited before becoming healthy"
  fi
  sleep 1
done

if [[ "${healthy}" != "true" ]]; then
  fail "health endpoint did not become ready within 45 seconds"
fi
grep --fixed-strings --quiet '"status":"ok"' "${work_dir}/health.json" \
  || fail "health endpoint returned an unexpected payload"

ready_status="$(curl --silent --show-error --output "${work_dir}/ready.json" \
  --write-out '%{http_code}' "${base_url}/api/v1/ready")"
[[ "${ready_status}" == "200" ]] || fail "ready endpoint returned HTTP ${ready_status}"
grep --fixed-strings --quiet '"status":"ready"' "${work_dir}/ready.json" \
  || fail "ready endpoint returned an unexpected payload"

root_status="$(curl --silent --show-error --output "${work_dir}/root.html" \
  --write-out '%{http_code}' "${base_url}/")"
[[ "${root_status}" == "200" ]] || fail "SPA root returned HTTP ${root_status}"
grep --fixed-strings --quiet '<div id="app"></div>' "${work_dir}/root.html" \
  || fail "SPA root did not return the built application shell"

history_status="$(curl --silent --show-error --output "${work_dir}/history.html" \
  --write-out '%{http_code}' "${base_url}/mine")"
[[ "${history_status}" == "200" ]] || fail "SPA history route returned HTTP ${history_status}"
grep --fixed-strings --quiet '<div id="app"></div>' "${work_dir}/history.html" \
  || fail "SPA history route did not return the built application shell"

api_status="$(curl --silent --show-error --output "${work_dir}/api-404.json" \
  --write-out '%{http_code}' "${base_url}/api/v1/not-a-route")"
[[ "${api_status}" == "404" ]] || fail "unknown API route returned HTTP ${api_status}"
if grep --fixed-strings --quiet '<div id="app"></div>' "${work_dir}/api-404.json"; then
  fail "unknown API route incorrectly returned the SPA shell"
fi

if ! docker_cmd exec --interactive "${smoke_name}" \
  services/analysis-api/.venv/bin/python - \
  <"$(dirname "$0")/smoke-gymti-image.py"
then
  fail "anonymous GYMTI image smoke failed"
fi

if ! model_trap_logs="$(docker_cmd logs "${model_trap_name}" 2>&1)"; then
  fail "could not inspect GYMTI model trap logs"
fi
if grep --fixed-strings --quiet 'unexpected-gymti-model-call' <<<"${model_trap_logs}"; then
  fail "anonymous GYMTI image smoke reached the configured model"
fi
if [[ "$(docker_cmd inspect --format '{{.State.Running}}' "${model_trap_name}")" != "true" ]]; then
  fail "GYMTI model trap exited during the image smoke"
fi

printf 'competition image verification passed: %s\n' "${image_ref}"
