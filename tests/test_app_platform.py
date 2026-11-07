"""Which Qt platform the window starts on (no Qt needed)."""

import sys

import pytest

from datapack_emulator.app import choose_platform, parse_arguments

WAYLAND = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sessions")


@linux_only
def test_a_wayland_session_with_xwayland_uses_xcb():
    assert choose_platform([], WAYLAND) == "xcb"


@linux_only
def test_the_users_choice_wins():
    assert choose_platform([], {**WAYLAND, "QT_QPA_PLATFORM": "wayland"}) is None
    assert choose_platform(["-platform", "wayland"], WAYLAND) is None
    assert choose_platform(["-platform=wayland"], WAYLAND) is None
    assert choose_platform([], WAYLAND, native_wayland=True) is None


def test_no_wayland_or_no_x_display_leaves_qt_alone():
    assert choose_platform([], {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}) is None
    assert choose_platform([], {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w"}) is None
    assert choose_platform([], {}) is None


def test_wayland_flag_parses():
    arguments, for_qt = parse_arguments(["--wayland", "-platform", "offscreen"])
    assert arguments.wayland and for_qt == ["-platform", "offscreen"]
