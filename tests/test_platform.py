from pathlib import Path

from mutator.cli import platform_warning


def test_native_windows_points_to_wsl():
    assert "WSL2" in platform_warning("win32", Path("C:/proj"))


def test_windows_drive_under_wsl_suggests_the_linux_filesystem():
    assert "under ~/" in platform_warning("linux", Path("/mnt/c/proj"))


def test_linux_and_macos_paths_are_quiet():
    assert platform_warning("linux", Path("/home/me/proj")) is None
    assert platform_warning("darwin", Path("/Users/me/proj")) is None
