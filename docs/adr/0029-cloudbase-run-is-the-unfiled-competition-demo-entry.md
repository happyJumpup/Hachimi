# ADR-0029: CloudBase Run is the temporary unfiled competition demo entry

- Status: Accepted for the competition window
- Date: 2026-07-22
- Decision owners: TrainPal team
- Supersedes: ADR-0011 only for the public edge and host topology

## Context

The team has no ICP-filed custom domain and needs a public HTTPS entry for judges and invited visitors before 2026-07-23 11:00 Asia/Shanghai. The existing CloudBase environment already supplies a Tencent-managed default domain, while the product and controlled media set are still being finalized.

CloudBase default domains are explicitly development and test facilities. A first-time visitor can see a Tencent Cloud risk notice and must confirm access. Traffic judged abnormal by the platform can also be restricted. Those constraints are acceptable for a bounded competition demo but not for a production launch claim.

The API keeps active analysis runs in process memory and its temporary analysis storage is ephemeral. CloudBase Run local storage is memory-backed and is not durable across instance replacement. Scaling the service to multiple instances would therefore split run state and make SSE polling nondeterministic.

## Decision

Use one CloudBase Run service named `trainpal-demo` in environment `bizhao-d8grp8yqd81759fbb` as the temporary same-origin web and API entry.

- Serve the built SPA and `/api/v1/*` from the same container. Keep the service public-network switch and any HTTP Access mapping disabled through the private release gates. Then open a short public-canary window for public-domain gates; announce the link only after that canary passes.
- Run exactly one Uvicorn worker and set the service maximum instance count to one.
- Use 2 vCPU and 4 GiB memory for the judging window. The extra memory protects the application from CloudBase Run's memory-backed temporary storage and local media preprocessing.
- Keep the minimum instance count at zero outside the judging window as an additional cold-start cost control. This does not make the service private: only disabling its public-network switch and HTTP Access mapping closes public access. Set the minimum to one before the final smoke test and keep it warm through the judging window.
- Set the platform request timeout to at least 240 seconds. The application still owns its stricter analysis timeout and SSE progress contract.
- Send application logs to standard output only. Never log uploaded media, provider keys, judge codes, cookie secrets, transcripts, or raw provider payloads.
- Allow unauthenticated visitors to browse the product pages and existing demo presentation. Set `JUDGE_ANALYSIS_CONCURRENCY=3` to preserve ADR-0011's competition boundary. Keep `PUBLIC_ANALYSIS_CONCURRENCY=0`: starting provider-backed analysis requires the judge code, so there is no anonymous paid analysis path.
- Keep all source media, FFmpeg, and manifests outside the image until their hashes and licenses have been approved. Production readiness must fail closed until those assets are mounted or staged and verified.
- Do not enable more instances, a queue, persistent raw uploads, or a database as part of this deadline exception.
- Keep the existing Lighthouse host available as an operational fallback, not as a second active application instance.

The team must tell first-time visitors: “首次打开会看到腾讯云安全提示，请点击‘确定访问’进入演示。” A short URL and QR code may point to the default domain, but the default domain must not be presented as a permanent production domain.

## Preserved invariants from ADR-0011

ADR-0011 still governs single-instance semantics, single-worker execution, bounded concurrency, privacy, controlled media, capacity evidence, immutable image identity, and rollback. This ADR changes only the public edge from a Caddy/custom-domain Lighthouse topology to CloudBase Run's Tencent-managed HTTPS edge for the competition window.

## Readiness gates

Before any public exposure, all private gates must pass:

1. The deployed image is identified by an immutable commit SHA.
2. Provider, access-control, proxy, static web, media manifest, audited FFmpeg, and writable temporary-storage checks pass through the internal service path.
3. At least one previous immutable version and its compatible non-secret configuration snapshot are retained.
4. The operator records the deployed image digest, configuration revision, and rollback target without recording secret values.

Next, enable a short public canary without announcing the link. All public-canary gates must pass:

1. `/api/v1/health` returns 200 and `/api/v1/ready` returns `{"status":"ready"}` through the public domain.
2. One judge-code analysis succeeds through the public entry, including SSE progress and result rendering.
3. Three concurrent judge-code analyses remain within the selected 2-vCPU/4-GiB memory envelope, and an additional over-capacity request receives the documented 429 response rather than destabilizing the instance.
4. A second browser can open the entry after acknowledging the default-domain notice and browse without a judge code.
5. Switching traffic to the retained version and compatible configuration restores a ready public endpoint; switch forward again only after the rollback smoke is recorded.
6. The operator records the public smoke time and capacity evidence without recording secret values.

If any gate fails, disable the public-network switch and any HTTP Access mapping. Returning to zero minimum instances is an additional cost control, not an access-control mechanism. The team must not claim that the public demo is ready.

## Cost and expiry guard

The total Tencent Cloud spend authorized for this task is RMB 300. Using the published 360 CCU / RMB 76 reference as a conservative rounded assumption of RMB 0.22 per CCU-hour, one 2-CCU warm instance for at most 18 hours has a compute ceiling of RMB 7.92. Console-priced requests, traffic, storage, builds, or add-ons are not covered by that estimate and require confirmation against the remaining budget before creation. The 2026-07-23 11:00 submission deadline is not assumed to be the judging end. The operator must confirm the actual demo end before enabling public access, and the public/warm window may never exceed 18 hours from first enable. At the confirmed demo end or the 18-hour cap, whichever comes first, disable public access, return the minimum instance count to zero, and review or remove unused billable resources.

## Consequences

The team gets a public HTTPS entry without waiting for ICP filing and retains one deployable same-origin container. Visitors may encounter an extra confirmation screen, the URL is visibly a development domain, cold starts are possible outside the judging window, and instance replacement loses in-memory runs. These are accepted deadline trade-offs, not the long-term production architecture.
