"""Frozen-app entry point (PyInstaller can't use `python -m`)."""

import sys

from downloader.app import main

if __name__ == "__main__":
    sys.exit(main())
