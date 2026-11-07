"""설치 없이도 실행: python demo_test/python_tap/run.py demo (NumPy 필요)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from pthoven_tap.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
