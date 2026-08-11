"""Exceptions raised by the raw-LAN codec.

Deliberately independent of ``..exceptions`` so this package stays a plain
library with zero Home Assistant *and* zero intra-integration coupling. A
consumer that wants integration-flavoured errors should catch
:class:`LanRawError` at the boundary and re-raise.
"""

from __future__ import annotations


class LanRawError(Exception):
    """Base class for every raw-LAN codec failure."""


class FrameError(LanRawError):
    """A frame could not be built (body too long, byte out of range, ...)."""


class UnsupportedCapabilityError(LanRawError):
    """The profile does not declare this capability for this device/zone.

    This is the "we know it is not there" case — e.g. asking the H60B0's
    white-only downlight for an RGB colour.
    """


class UnknownEncodingError(LanRawError):
    """The profile declares the capability but a required constant is UNKNOWN.

    This is the "we do not know the bytes yet" case. The table is allowed to
    say so, and the codec REFUSES to guess. The segment sub-mode byte differs
    by SKU (``0x2c`` on the H60B0, ``0x15`` on the H6046) and must never be
    assumed to carry across models: a wrong sub-mode byte is accepted and then
    silently ignored by the firmware, which looks exactly like a dead network.
    """


class SegmentMaskError(LanRawError):
    """The requested segment mask is empty or out of range.

    An all-zero mask is accepted by the firmware and silently does nothing —
    the original "stuck red" bug. We refuse to send one.
    """
