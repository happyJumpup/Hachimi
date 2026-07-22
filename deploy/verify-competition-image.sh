#!/usr/bin/env bash

set -Eeuo pipefail

readonly image_ref="${1:?usage: verify-competition-image.sh <image-ref>}"
readonly smoke_name="hachimi-image-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
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
  rm -rf "${work_dir}"
}
trap cleanup EXIT

fail() {
  printf 'competition image verification failed: %s\n' "$1" >&2
  if docker_cmd inspect "${smoke_name}" >/dev/null 2>&1; then
    docker_cmd logs "${smoke_name}" >&2 || true
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
    find /workspace -type f \( \
      -name ".env" -o -name ".env.*" -o \
      -name "*.mp4" -o -name "*.mov" -o -name "*.mkv" -o \
      -name "*.avi" -o -name "*.webm" -o -name "*.wav" -o \
      -name "*.mp3" -o -name "*.ogg" -o -name "*.m4a" -o \
      -name "*.aac" -o -name "*.flac" -o -name "*.trace" \
    \) -print

    find /workspace -type f \( \
      -name "*.bmp" -o -name "*.gif" -o -name "*.jpeg" -o \
      -name "*.jpg" -o -name "*.png" -o -name "*.tif" -o \
      -name "*.tiff" -o -name "*.webp" -o -name "*.ass" -o \
      -name "*.srt" -o -name "*.ssa" -o -name "*.vtt" \
    \) -print | while IFS= read -r artifact_path; do
      case "${artifact_path}" in
        *.webp)
          artifact_sha="$(sha256sum "${artifact_path}" | awk "{print \$1}")"
          case "${artifact_sha}" in
            5518f49229cc0331bfdf9c5351e6a7806bf97cac6c882b2d5dc40e620f85561c|\
            bdab0d00707684a80f44f31fc09f0325ac0b608060ca746b63e15e6d940b44ab|\
            730f5c6b4b2b91d11ea23085eac3739a4d22841014d7c21752b207b63ff4db30|\
            d775c21bc2df5bd2156638247066f7f836b9b800c9d8f3044f595a81f906f91c|\
            74ffadcabdb1124680efcb0dbf2d4b2c2f5c5a88b79a6d811cf108a8552a92a9)
              ;;
            *) printf "%s\n" "${artifact_path}" ;;
          esac
          ;;
        *) printf "%s\n" "${artifact_path}" ;;
      esac
    done

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

docker_cmd run --detach \
  --name "${smoke_name}" \
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

printf 'competition image verification passed: %s\n' "${image_ref}"
