# Third-party notices

This repository is MIT licensed. Runtime and development dependencies retain
their own licenses.

- `imageio-ffmpeg` locates the development FFmpeg executable used for local
  media-window extraction. Its wheel-provided executable is not copied into the
  repository or the production GHCR image. Production mounts a separately
  managed, checksummed FFmpeg executable read-only from the server. The release
  owner must audit and pin that exact binary plus its complete configuration line,
  and preserve its applicable license/source
  obligations; see `licenses/FFMPEG_RUNTIME.md`.
- `Barlow Condensed` is bundled as a browser font through
  `@fontsource/barlow-condensed`. Copyright 2017 The Barlow Project Authors;
  licensed under the SIL Open Font License 1.1. See
  `licenses/Barlow-Condensed-OFL-1.1.txt`.
- The historical `Hachimi_demo` repository is GPL-3.0. It may be consulted for
  product behaviour only. No source code or UI component from it may be copied
  into this repository.
- The local smoke-test video is not part of this repository and must not be
  redistributed until its rights are independently confirmed.
