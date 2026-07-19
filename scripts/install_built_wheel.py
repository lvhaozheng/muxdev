from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    wheel_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "wheelhouse").resolve()
    wheels = sorted(wheel_dir.glob("muxdev-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one muxdev wheel in {wheel_dir}, found {len(wheels)}")
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", str(wheels[0])],
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
