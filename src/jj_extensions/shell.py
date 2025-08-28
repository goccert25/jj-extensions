import json
import subprocess
from typing import List, Optional, Any


def run(
    args: List[str], cwd: Optional[str] = None, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check)


def run_ok(args: List[str], cwd: Optional[str] = None) -> str:
    cp = run(args, cwd=cwd)
    return cp.stdout.strip()


def capture_json(args: List[str], cwd: Optional[str] = None) -> Any:
    out = run_ok(args, cwd=cwd)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse JSON from: {' '.join(args)}\nOutput: {out}"
        ) from exc
