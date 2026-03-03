"""Test setup: keep every test's projects/recipes in a throwaway folder.

Without this, tests that reset state would delete the real .loom/ directory.
"""
import os
import tempfile

os.environ["LOOM_HOME"] = tempfile.mkdtemp(prefix="loom-test-")
