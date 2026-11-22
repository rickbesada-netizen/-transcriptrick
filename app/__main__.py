"""Run from source: python -m app"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent.parent / "launcher.py"), run_name="__main__")
