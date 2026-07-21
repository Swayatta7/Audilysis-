#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.youtube_transcript_service import (  # noqa: E402
    build_proxy_config,
    extract_video_id,
    get_proxy_mode,
    get_youtube_transcript_api_version,
    safe_exception_diagnostic,
)
from youtube_transcript_api import YouTubeTranscriptApi  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose YouTube transcript retrieval with the installed library.")
    parser.add_argument("video", help="YouTube URL or 11-character video ID")
    args = parser.parse_args()

    video_id = extract_video_id(args.video)
    print(f"youtube_transcript_api_version={get_youtube_transcript_api_version()}")
    print(f"video_id={video_id}")
    print(f"proxy_mode={get_proxy_mode()}")

    try:
        api = YouTubeTranscriptApi(proxy_config=build_proxy_config())
        transcript_list = api.list(video_id)
        transcripts = list(transcript_list)
        print(f"available_transcripts={len(transcripts)}")
        for index, transcript in enumerate(transcripts, start=1):
            print(
                "transcript="
                f"{index},language_code={transcript.language_code},"
                f"is_generated={transcript.is_generated}"
            )
        if not transcripts:
            print("result=no_transcripts_listed")
            return 2

        selected = transcripts[0]
        snippets = selected.fetch()
        print(f"fetch_result=success")
        print(f"selected_language_code={selected.language_code}")
        print(f"selected_is_generated={selected.is_generated}")
        print(f"segment_count={len(snippets)}")
        return 0
    except Exception as exc:
        print("fetch_result=error")
        print(f"exception_class={exc.__class__.__name__}")
        print(f"diagnostic={safe_exception_diagnostic(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
