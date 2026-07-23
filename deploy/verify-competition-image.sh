#!/usr/bin/env bash

set -Eeuo pipefail

readonly image_ref="${1:?usage: verify-competition-image.sh <image-ref>}"
readonly smoke_name="hachimi-image-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
readonly model_trap_name="${smoke_name}-model-trap"
readonly smoke_network="${smoke_name}-network"
readonly fixture_volume="${smoke_name}-fixture"
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
  docker_cmd volume rm "${fixture_volume}" >/dev/null 2>&1 || true
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

docker_cmd volume create "${fixture_volume}" >/dev/null
docker_cmd run --rm --interactive --user 0:0 \
  --volume "${fixture_volume}:/audit" \
  --entrypoint /bin/sh \
  "${image_ref}" -eu -s <<'FIXTURE'
mkdir -p /audit/media
/opt/trainpal/ffmpeg/bin/ffmpeg \
  -hide_banner -loglevel error \
  -f lavfi -i color=c=black:s=16x16:r=1 \
  -t 1 -c:v mpeg4 -an /audit/media/fixture.mp4
for index in 1 2 3 4 5; do
  cp /audit/media/fixture.mp4 "/audit/media/media-${index}.mp4"
done
rm /audit/media/fixture.mp4
/workspace/services/analysis-api/.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

media_root = Path("/audit/media")
sources = []
for index in range(1, 6):
    media_path = media_root / f"media-{index}.mp4"
    sources.append(
        {
            "id": f"audit-source-{index}",
            "title": "Runtime image verification fixture",
            "media_path": media_path.name,
            "duration_seconds": 1.0,
            "sha256": hashlib.sha256(media_path.read_bytes()).hexdigest(),
            "origin_url": None,
        }
    )
Path("/audit/media-manifest.json").write_text(
    json.dumps({"version": 1, "sources": sources}),
    encoding="utf-8",
)
PY
chmod -R a+rX /audit
FIXTURE
docker_cmd run --rm \
  --volume "${fixture_volume}:/audit:ro" \
  --entrypoint /workspace/services/analysis-api/.venv/bin/python \
  "${image_ref}" \
  -c 'from pathlib import Path; from hakimi_analysis.media import probe_duration_sync; from hakimi_analysis.sources import SourceCatalog; catalog = SourceCatalog.from_manifest(manifest_path=Path("/audit/media-manifest.json"), media_root=Path("/audit/media"), public_media_base_url="https://image-audit.invalid/media", duration_probe=probe_duration_sync); raise SystemExit(0 if catalog.source_count == 5 else 1)' \
  || fail "runtime media fixture validation failed"

docker_cmd network create "${smoke_network}" >/dev/null
docker_cmd run --detach \
  --name "${model_trap_name}" \
  --network "${smoke_network}" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m,mode=1777 \
  --entrypoint services/analysis-api/.venv/bin/python \
  "${image_ref}" \
  -c '
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class TrapHandler(BaseHTTPRequestHandler):
    call_count = 0

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(content_length))
        gymti_payload = json.loads(request["messages"][1]["content"])
        type(self).call_count += 1
        print("gymti-provider-call", flush=True)
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != "Bearer release-smoke-trap-key":
            self.send_error(401)
            return
        if self.call_count == 4:
            self.send_response(503)
            self.end_headers()
            return
        if self.call_count == 1:
            content = json.dumps({"question_id": "q01_energy_after_work"})
        elif self.call_count == 2:
            content = json.dumps({
                "contract_version": gymti_payload["contract_version"],
                "questionnaire_version": gymti_payload["questionnaire_version"],
                "scoring_version": gymti_payload["scoring_version"],
                "formal_result_id": gymti_payload["formal_result_id"],
                "secondary_result_id": gymti_payload["secondary_result_id"],
                "coach_style_id": gymti_payload["coach_style_id"],
                "reason_codes": gymti_payload["reason_codes"],
                "narrative_id": gymti_payload["candidate_narrative_ids"][0],
            })
        else:
            content = json.dumps({"question_id": "not-a-legal-candidate"})
        payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
  --volume "${fixture_volume}:/runtime-fixture:ro" \
  --env APP_ENV=production \
  --env ANALYSIS_PROVIDER=cloud \
  --env ARK_API_KEY=image-audit-provider-key \
  --env VOLC_ASR_API_KEY=image-audit-asr-key \
  --env JUDGE_ACCESS_CODE=image-audit-judge-code-32-bytes \
  --env ACCESS_COOKIE_SECRET=image-audit-cookie-secret-at-least-32-bytes \
  --env CORS_ORIGINS=https://image-audit.invalid \
  --env LOCAL_UPLOAD_ENABLED=true \
  --env TRUSTED_PROXY_CIDRS= \
  --env PUBLIC_ANALYSIS_CONCURRENCY=1 \
  --env JUDGE_ANALYSIS_CONCURRENCY=0 \
  --env SOURCE_MANIFEST_PATH=/runtime-fixture/media-manifest.json \
  --env SOURCE_MEDIA_ROOT=/runtime-fixture/media \
  --env PUBLIC_MEDIA_BASE_URL=https://image-audit.invalid/media \
  --env GYMTI_LLM_ENABLED=true \
  --env GYMTI_LLM_RETENTION_CONFIRMED=true \
  --env GYMTI_LLM_CONCURRENCY=3 \
  --env GYMTI_LLM_API_KEY=release-smoke-trap-key \
  --env GYMTI_LLM_MODEL=doubao-seed-2-0-mini-260428 \
  --env GYMTI_LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3 \
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

if ! docker_cmd exec --interactive \
  --env APP_ENV=test \
  --env ANALYSIS_PROVIDER=test \
  --env GYMTI_LLM_ENABLED=true \
  --env GYMTI_LLM_RETENTION_CONFIRMED=true \
  --env GYMTI_LLM_CONCURRENCY=3 \
  --env GYMTI_LLM_API_KEY=release-smoke-trap-key \
  --env GYMTI_LLM_MODEL=image-audit-trap-model \
  --env GYMTI_LLM_BASE_URL="http://${model_trap_name}:8081/v1" \
  --env GYMTI_LLM_MAX_ATTEMPTS=1 \
  --env GYMTI_LLM_TIMEOUT_SECONDS=1 \
  "${smoke_name}" \
  services/analysis-api/.venv/bin/python - \
  <"$(dirname "$0")/smoke-gymti-image.py"
then
  fail "public GYMTI image smoke failed"
fi

if ! model_trap_logs="$(docker_cmd logs "${model_trap_name}" 2>&1)"; then
  fail "could not inspect GYMTI model trap logs"
fi
provider_calls="$(grep --fixed-strings --count 'gymti-provider-call' <<<"${model_trap_logs}" || true)"
if [[ "${provider_calls}" != "4" ]]; then
  fail "GYMTI image smoke did not exercise the configured provider contract"
fi
if [[ "$(docker_cmd inspect --format '{{.State.Running}}' "${model_trap_name}")" != "true" ]]; then
  fail "GYMTI model trap exited during the image smoke"
fi

printf 'competition image verification passed: %s\n' "${image_ref}"
