# Audited FFmpeg runtime

The production image removes the executable bundled by `imageio-ffmpeg` and builds
FFmpeg 8.1.2 from the official signed source release. The multi-stage Docker build
downloads the tarball and detached signature from `ffmpeg.org`, imports the official
release key, checks fingerprint
`FCF986EA15E6E293A5644F10B4322F04D67658D8`, and verifies the signature before
compiling.

Before release, record all of the following in the deployment evidence:

- FFmpeg version, upstream distribution URL and SHA-256;
- the complete configure line shown by `ffmpeg -version`;
- SHA-256 of that exact, trimmed `configuration:` line;
- the exact applicable LGPL/GPL license text and corresponding-source location;
- the release owner and reviewer who checked the record.

The runtime is installed under `/opt/trainpal/ffmpeg` with shared libraries and the
applicable LGPL text. `/opt/trainpal/ffmpeg/receipt.json` records the source URL,
signing-key fingerprint, version, binary SHA-256, complete configure line, and its
SHA-256. Production readiness binds that receipt to the executable and rejects a
version, hash, configure-line, path, GPL, or nonfree mismatch.

The build is unmodified and does not enable `--enable-gpl` or `--enable-nonfree`.
Shared libraries preserve the relinking boundary. The application uses FFmpeg's
built-in `mpeg4` encoder for
accurate non-keyframe video windows and PCM audio extraction; it does not require
`libx264` or another GPL encoder. If the supplied
binary enables GPL components, the release owner must satisfy the resulting GPL
distribution obligations before deployment. This file is an engineering release
gate, not legal advice.

Official source and verification instructions: <https://ffmpeg.org/download.html>.
Licensing reference: <https://ffmpeg.org/legal.html>.
