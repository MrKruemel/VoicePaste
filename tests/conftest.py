"""Shared pytest fixtures for the Voice-to-Summary Paste Tool test suite."""

import sys
import os

# Add src directory to path so tests can import modules
_src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
