"""The helper must run on every macOS the app declares, whatever Mac or shell built it."""

import plistlib
import re
import subprocess

from conftest import REPO


def _build_versions(binary) -> list[str]:
    """minos of every LC_BUILD_VERSION load command in the binary."""
    out = subprocess.run(["otool", "-l", str(binary)], capture_output=True, text=True, check=True).stdout
    return re.findall(r"cmd LC_BUILD_VERSION.*?\n\s*minos (\S+)", out, re.S)


def test_helper_minimum_macos_matches_info_plist(helper_binary):
    declared = plistlib.loads((REPO / "helper" / "Info.plist").read_bytes())["LSMinimumSystemVersion"]
    assert _build_versions(helper_binary) == [declared]  # not the toolchain's default, which may be newer


def test_helper_is_built_for_this_macs_cpu(helper_binary):
    native = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True).stdout.strip()
    archs = subprocess.run(["lipo", "-archs", str(helper_binary)], capture_output=True, text=True, check=True)
    assert archs.stdout.split() == ["arm64" if native == "1" else "x86_64"]
