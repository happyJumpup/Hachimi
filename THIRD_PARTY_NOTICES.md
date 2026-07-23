# Third-party notices

This repository is MIT licensed. Runtime and development dependencies retain
their own licenses.

- `imageio-ffmpeg` locates the development FFmpeg executable used for local
  media-window extraction. Its wheel-provided executable is not copied into the
  repository or production image. The production container builds FFmpeg 8.1.2
  from verified upstream signed source, installs the audited LGPL-only shared
  build and receipt under `/opt/trainpal/ffmpeg`, and rejects any second binary
  or GPL/nonfree configuration. The release owner must preserve the exact
  binary, configuration, receipt, source and license obligations; see
  `licenses/FFMPEG_RUNTIME.md`.
- `Barlow Condensed` is bundled as a browser font through
  `@fontsource/barlow-condensed`. Copyright 2017 The Barlow Project Authors;
  licensed under the SIL Open Font License 1.1. See
  `licenses/Barlow-Condensed-OFL-1.1.txt`.
- The historical `Hachimi_demo` repository is GPL-3.0. It may be consulted for
  product behaviour only. No source code or UI component from it may be copied
  into this repository.
- The local smoke-test video is not part of this repository and must not be
  redistributed until its rights are independently confirmed.
