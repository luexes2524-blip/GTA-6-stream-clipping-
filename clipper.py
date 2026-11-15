#!/usr/bin/env python3
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel
from supabase import create_client


def require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def resolve_stream_url(video_url: str) -> str:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    if info is None:
        raise RuntimeError("Could not resolve the YouTube URL.")

    formats = info.get("formats") or []
    if not formats:
        if info.get("url"):
            return info["url"]
        if info.get("hls_url"):
            return info["hls_url"]
        raise RuntimeError("No stream URL found in the YouTube metadata.")

    preferred = None
    for f in formats:
        if not f.get("url"):
            continue
        if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none"):
            preferred = f
            break

    if preferred and preferred.get("url"):
        return preferred["url"]

    for f in formats:
        if f.get("url"):
            return f["url"]

    raise RuntimeError("No usable video stream URL was found.")


def run_ffmpeg(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def download_segment(video_url: str, start_seconds: int, duration_seconds: int, output_path: Path) -> None:
    stream_url = resolve_stream_url(video_url)

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(start_seconds),
        "-t",
        str(duration_seconds),
        "-i",
        stream_url,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    run_ffmpeg(ffmpeg_cmd)


def extract_audio(video_path: Path, audio_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        str(audio_path),
    ]
    run_ffmpeg(cmd)


def format_timestamp(seconds: float) -> str:
    total_milliseconds = int((seconds % 1) * 1000)
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{total_milliseconds:03d}"


def generate_srt(segments, output_path: Path) -> None:
    lines = []
    for index, segment in enumerate(segments, start=1):
        start = float(segment.start)
        end = float(segment.end)
        lines.append(str(index))
        lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        lines.append(segment.text.strip())
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def transcribe_audio(audio_path: Path, srt_path: Path) -> None:
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), vad_filter=True, language=None)
    segment_list = list(segments)
    generate_srt(segment_list, srt_path)


def add_captions(video_path: Path, srt_path: Path, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"subtitles='{str(srt_path)}':force_style='FontName=Arial,Fontsize=24,PrimaryColour=&HFFFFFF,BackColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run_ffmpeg(cmd)


def upload_to_supabase(file_path: Path, storage_path: str, title: str, source_url: str) -> str:
    supabase_url = require_env("SUPABASE_URL")
    service_role_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    bucket_name = os.getenv("SUPABASE_BUCKET", "clips")

    client = create_client(supabase_url, service_role_key)

    with file_path.open("rb") as f:
        file_bytes = f.read()

    storage = client.storage.from_(bucket_name)
    storage.upload(
        storage_path,
        file_bytes,
        {"content-type": "video/mp4", "upsert": "true"},
    )

    public_url = storage.get_public_url(storage_path)

    row = {
        "title": title,
        "source_url": source_url,
        "storage_path": storage_path,
        "public_url": public_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    client.table("clips").insert(row).execute()

    return public_url


def main() -> None:
    youtube_url = require_env("YOUTUBE_URL")
    start_seconds = int(os.getenv("CLIP_START_SECONDS", "0"))
    duration_seconds = int(os.getenv("CLIP_DURATION_SECONDS", "60"))

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    raw_clip = output_dir / "raw_clip.mp4"
    audio_file = output_dir / "audio.wav"
    captions_file = output_dir / "captions.srt"
    final_clip = output_dir / "clip_with_captions.mp4"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    storage_name = f"clips/{timestamp}.mp4"

    print("Downloading stream segment...")
    download_segment(youtube_url, start_seconds, duration_seconds, raw_clip)

    print("Extracting audio for transcription...")
    extract_audio(raw_clip, audio_file)

    print("Generating captions...")
    transcribe_audio(audio_file, captions_file)

    print("Burning subtitles into the video...")
    add_captions(raw_clip, captions_file, final_clip)

    print("Uploading video to Supabase Storage...")
    public_url = upload_to_supabase(final_clip, storage_name, f"clip_{timestamp}", youtube_url)
    print(f"Done. Uploaded clip URL: {public_url}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
