#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.youtube_transcript_service import (  # noqa: E402
    diagnose_transcript_fetch,
    extract_video_id,
    get_proxy_diagnostics,
    get_youtube_transcript_api_version,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose YouTube transcript retrieval with the installed library.")
    parser.add_argument("video", help="YouTube URL or 11-character video ID")
    args = parser.parse_args()

    video_id = extract_video_id(args.video)
    print(f"youtube_transcript_api_version={get_youtube_transcript_api_version()}")
    print(f"video_id={video_id}")
    diagnostics = get_proxy_diagnostics()
    print(f"proxy_mode={diagnostics['mode']}")
    print(f"fetch_strategies={','.join(diagnostics['fetch_strategies'])}")
    print(f"direct_fallback_enabled={diagnostics['direct_fallback_enabled']}")
    print(f"request_total_budget_seconds={diagnostics['request_total_budget_seconds']}")
    print(f"connect_timeout_seconds={diagnostics['connect_timeout_seconds']}")
    print(f"read_timeout_seconds={diagnostics['read_timeout_seconds']}")

    result = diagnose_transcript_fetch(video_id)
    for index, attempt in enumerate(result["results"], start=1):
        parts = [
            f"attempt={index}",
            f"strategy={attempt['strategy']}",
            f"proxy_mode={attempt['proxy_mode']}",
            f"uses_proxy={attempt['uses_proxy']}",
            f"status={attempt['status']}",
        ]
        if attempt["status"] == "success":
            parts.extend([
                f"language_code={attempt['language_code']}",
                f"is_generated={attempt['is_generated']}",
                f"segment_count={attempt['segment_count']}",
            ])
        else:
            parts.extend([
                f"exception_class={attempt['exception_class']}",
                f"error_code={attempt['error_code']}",
                f"diagnostic={attempt['diagnostic']}",
            ])
        print(",".join(str(part) for part in parts))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
