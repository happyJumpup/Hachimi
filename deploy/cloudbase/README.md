# CloudBase competition foundation

This directory defines the reviewed, non-secret foundation for the temporary TrainPal competition entry. `foundation-plan.json` is an operator contract, not a Tencent Cloud CLI or API payload. It is safe to commit because it contains environment-variable names but no secret values.

The product content is deliberately absent. Do not upload or select the 19-minute, 7-minute, or existing short video while `assets_deferred_until_content_approval` is true.

## Intended service

| Setting | Required value |
| --- | --- |
| CloudBase environment | `bizhao-d8grp8yqd81759fbb` (`ap-shanghai`) |
| Service name | `trainpal-demo` |
| Deployment form | Existing repository Dockerfile, immutable commit SHA |
| Container port | `8000` |
| Public network | Disabled through private gates; short canary before announcement |
| CPU / memory | 2 vCPU / 4 GiB |
| Maximum instances | 1 |
| Minimum instances outside judging | 0 |
| Minimum instances for warm-up and judging | 1 |
| Platform request timeout | At least 240 seconds |
| Application workers | 1 |
| Logs | Standard output only |

Do not create a second service for the frontend. The existing image serves the compiled SPA and FastAPI from one origin. Do not set the maximum instance count above one while active runs live in process memory. Keep the public-network switch and HTTP Access mapping disabled until the private gates pass. Minimum instances `0` reduces idle compute but does not prevent a public request from cold-starting the service.

## Default-domain impact

The Tencent-managed default domain is sufficient for the bounded demo without an ICP-filed custom domain. It has two visible limitations:

1. A first-time visitor can receive a Tencent Cloud risk notice and must click **确定访问**.
2. The platform can restrict traffic it considers abnormal, so the link should not be promoted as a durable public production endpoint.

Put this sentence next to the QR code or judging instructions:

> 首次打开会看到腾讯云安全提示，请点击“确定访问”进入演示。

Anyone with the link may browse the product pages. Provider-backed analysis remains a separate paid action protected by the judge code; `PUBLIC_ANALYSIS_CONCURRENCY=0` must not be relaxed for passersby.

## Secret and configuration handling

Enter secret values only in CloudBase's encrypted environment-variable or secret interface. Never place them in this directory, an image layer, a screenshot, a shell history transcript, or application logs. The required secret variable names are listed in the plan.

The local `.env.local` may be read to transfer authorized values directly into the cloud console, but values must not be printed during the transfer. Generate a new production `ACCESS_COOKIE_SECRET`; do not reuse a browser cookie or provider key. Set `JUDGE_ANALYSIS_CONCURRENCY=3` and keep `PUBLIC_ANALYSIS_CONCURRENCY=0`.

`TRUSTED_PROXY_CIDRS` must be the narrow CloudBase ingress proxy range observed or documented for the service. The application deliberately rejects an empty value in production. Never use `0.0.0.0/0` or `::/0` to bypass this gate.

## Deferred asset boundary

The current image intentionally excludes the FFmpeg executable and controlled media. The production readiness endpoint therefore remains not-ready until all of the following have been approved and staged:

- the source manifest and smoke annotations;
- the controlled media set and public HTTPS media base URL;
- a non-GPL, non-nonfree FFmpeg executable;
- the FFmpeg binary SHA-256 and configuration-line SHA-256;
- container paths matching the plan.

CloudBase Run local storage is memory-backed and disappears with the instance. Raw uploads must stay transient. Durable controlled assets should use a read-only mounted object store or a startup staging flow with hash verification; the exact content and mount are intentionally deferred.

## Execution sequence

1. Validate the contract locally:

   ```powershell
   .\deploy\cloudbase\preflight.ps1
   ```

   This runs both the strict plan validator and a pinned CloudBase CLI command-surface check. It does not authenticate, upload code, or submit a deployment.

2. Build and verify a baseline competition image. Record an immutable registry digest (`...@sha256:...`), not only a mutable tag.
3. CloudBase CLI 3.6.4 does not implement the `cloudrun deploy --dry-run` option currently shown in the CloudBase CLI documentation. Do not substitute a guessed non-interactive command. After explicit action-time approval, create the private service with the verified baseline image:

   ```powershell
   $env:TRAINPAL_BASELINE_IMAGE_DIGEST = '<registry/image@sha256:digest>'
   npx --yes --package @cloudbase/cli@3.6.4 tcb -e bizhao-d8grp8yqd81759fbb -r ap-shanghai cloudrun deploy -s trainpal-demo --port 8000 --imageUrl $env:TRAINPAL_BASELINE_IMAGE_DIGEST
   ```

   Do not add `--force` or `--traffic`. The interactive prompt is the required final review of the target account, service, access mode, and price-affecting configuration.

4. In CloudBase Run, create `trainpal-demo` only after the console shows the full resource and price implications. `foundation-plan.json` is the source of truth for the confirmed access end, public/warm durations, pricing assumption, and derived compute ceilings; `preflight.ps1` rejects a derived ceiling above the authorized budget. Requests, traffic, storage, builds, and add-ons must still be checked separately in the console.
5. Configure non-secret values and transfer secrets without echoing them while public access remains disabled. Stage the approved media/FFmpeg contract, pass the baseline private gates through the internal service path, and retain its compatible configuration snapshot.
6. Build and verify the candidate image as a different immutable digest. Deploy it as a second version with `--traffic` while the public-network switch remains disabled. Pass the candidate private gates; the verified baseline from step 5 is now the rollback target. CloudBase requires a separate manual promotion, and `--traffic` does not replace the public-network switch.
7. Read the confirmed demo end from `foundation-plan.json`. Enable a short, unannounced public canary and run public health/ready, judge SSE, three concurrent judge analyses, over-capacity 429, second-browser browse, and rollback-to-baseline smoke tests. Switch forward to the candidate again only after the rollback result is recorded. Disable public access immediately if a gate fails.
8. Announce the link only after the canary passes. Before judging, set the minimum instances to one.
9. The submission cutoff and confirmed demo end are distinct values in `foundation-plan.json`. Keep the minimum instance count at zero outside explicitly announced judging periods. At the confirmed end or the plan's maximum public duration, whichever comes first, disable the public-network switch and HTTP Access mapping, return the minimum to zero, and review unused billable resources.

The public service is not ready merely because the container starts. ADR-0029 lists the release gates and rollback rule.
