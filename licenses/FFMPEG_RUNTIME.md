# Server-managed FFmpeg runtime

The production application image intentionally removes the executable bundled by
`imageio-ffmpeg`. The Tencent Cloud host supplies one separately managed executable
at `/opt/hachimi/shared/bin/ffmpeg`, mounted read-only into the application.

Before release, record all of the following in the deployment evidence:

- FFmpeg version, upstream distribution URL and SHA-256;
- the complete configure line shown by `ffmpeg -version`;
- the exact applicable LGPL/GPL license text and corresponding-source location;
- the release owner and reviewer who checked the record.

For the intended LGPL boundary, use an unmodified build without `--enable-gpl` or
`--enable-nonfree`. The application performs video stream copy and PCM audio
extraction; it does not require `libx264` or another GPL encoder. If the supplied
binary enables GPL components, the release owner must satisfy the resulting GPL
distribution obligations before deployment. This file is an engineering release
gate, not legal advice.

Authoritative reference: <https://ffmpeg.org/legal.html>.
