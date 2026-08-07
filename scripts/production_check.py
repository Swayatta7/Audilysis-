import importlib
import json
import platform
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from services.production_diagnostics import (
    connectivity_targets,
    diagnose_connectivity,
    get_environment_audit,
    get_runtime_path_diagnostics,
    package_diagnostics,
)
from services.diarization import get_speaker_detection_diagnostics
from services.youtube_transcript_service import get_proxy_diagnostics


def wsgi_import_status() -> dict:
    try:
        module = importlib.import_module("wsgi")
        return {"status": "ok", "detail": hasattr(module, "app")}
    except Exception as exc:
        return {"status": "error", "detail": f"{exc.__class__.__name__}: {exc}"}


def connectivity_report() -> dict:
    report = {}
    for name, target in connectivity_targets().items():
        host, port = target
        report[name] = diagnose_connectivity(host, port)
    return report


def main() -> int:
    output = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
        "base_dir": str(BASE_DIR),
        "packages": package_diagnostics(),
        "environment": get_environment_audit(),
        "paths": get_runtime_path_diagnostics(),
        "youtube_proxy": get_proxy_diagnostics(),
        "speaker_detection": get_speaker_detection_diagnostics(),
        "wsgi": wsgi_import_status(),
        "connectivity": connectivity_report(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
