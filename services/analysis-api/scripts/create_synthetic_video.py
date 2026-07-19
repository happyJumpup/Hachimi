import asyncio
from pathlib import Path

import imageio_ffmpeg

from hakimi_analysis.settings import PROJECT_ROOT


async def create_video(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x101820:s=390x694:r=15",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=16000",
        "-t",
        "4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-y",
        str(output),
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("synthetic video generation failed") from RuntimeError(
            stderr.decode("utf-8", errors="replace")
        )


if __name__ == "__main__":
    asyncio.run(create_video(PROJECT_ROOT / "tmp" / "e2e-source.mp4"))
