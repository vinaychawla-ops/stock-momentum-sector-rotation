"""Smoke tests launcher — use pytest for full suite."""
import subprocess, sys
if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "tests/", "-v"]))
