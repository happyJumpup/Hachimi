# Server-managed FFmpeg runtime

The production application image intentionally removes the executable bundled by
`imageio-ffmpeg`. The Tencent Cloud host supplies one separately managed executable
at `/opt/hachimi/shared/bin/ffmpeg`, mounted read-only into the application.

Before release, record all of the following in the deployment evidence:

- FFmpeg version, upstream distribution URL and SHA-256;
- the complete configure line shown by `ffmpeg -version`;
- SHA-256 of that exact, trimmed `configuration:` line;
- the exact applicable LGPL/GPL license text and corresponding-source location;
- the release owner and reviewer who checked the record.

Set `FFMPEG_EXPECTED_SHA256` and `FFMPEG_EXPECTED_CONFIGURATION_SHA256` to the two
audited digests. Production readiness verifies the executable bit, binary digest,
successful `ffmpeg -version` execution, exact configuration-line digest and absence
of `--enable-gpl` / `--enable-nonfree` before accepting traffic.

For the intended LGPL boundary, use an unmodified build without `--enable-gpl` or
`--enable-nonfree`. The application uses FFmpeg's built-in `mpeg4` encoder for
accurate non-keyframe video windows and PCM audio extraction; it does not require
`libx264` or another GPL encoder. If the supplied
binary enables GPL components, the release owner must satisfy the resulting GPL
distribution obligations before deployment. This file is an engineering release
gate, not legal advice.

Authoritative reference: <https://ffmpeg.org/legal.html>.
