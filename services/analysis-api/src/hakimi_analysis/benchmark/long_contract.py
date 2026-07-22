"""Shared frozen constants for the isolated long-video study."""

MAX_LONG_PRIMARY_DEMO_SECONDS = 120.0

# Qwen's temporary-OSS multipart path is sensitive to needlessly large source
# encodes on the local SOCKS egress path.  This projection preserves the whole
# source-clock window while producing the bounded visual representation that
# the Qwen arm is actually evaluated with.
QWEN_VIDEO_PROJECTION_VERSION = "qwen-h264-short-edge-160-2fps-crf35-v3"
QWEN_VIDEO_PROJECTION_MIN_SHORT_EDGE = 160
QWEN_VIDEO_PROJECTION_FPS = 2
QWEN_VIDEO_PROJECTION_CRF = 35
