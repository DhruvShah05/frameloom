"""Test setup: keep every test's projects/recipes in a throwaway folder.

Without this, tests that reset state would delete your real FrameLoom data.
"""
import os
import tempfile

os.environ["FRAMELOOM_HOME"] = tempfile.mkdtemp(prefix="frameloom-test-")
