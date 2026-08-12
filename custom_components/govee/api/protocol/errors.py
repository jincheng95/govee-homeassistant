"""Exceptions raised by the raw-LAN codec.

Deliberately independent of ``..exceptions`` so this package stays a plain
library with zero Home Assistant *and* zero intra-integration coupling. A
consumer that wants integration-flavoured errors should catch
:class:`GoveeProtocolError` at the boundary and re-raise.
"""

from __future__ import annotations


class GoveeProtocolError(Exception):
    """Base class for every raw-LAN codec failure."""


class FrameError(GoveeProtocolError):
    """A frame could not be built (body too long, byte out of range, ...)."""


class UnsupportedCapabilityError(GoveeProtocolError):
    """The profile does not declare this capability for this device/zone.

    This is the "we know it is not there" case — e.g. asking the H60B0's
    white-only downlight for an RGB colour.
    """


class UnknownEncodingError(GoveeProtocolError):
    """The profile declares the capability but a required constant is UNKNOWN.

    This is the "we do not know the bytes yet" case: the table is allowed to
    say so, and the codec refuses to guess, because a wrong byte is accepted
    and then silently ignored by the firmware — which looks exactly like a
    dead network.
    """


class DiyEffectError(GoveeProtocolError):
    """A DIY effect payload could not be encoded from the given parameters.

    Raised for out-of-range mode/speed/flow values, an empty or oversized
    palette, or direction/flow parameters aimed at a zone whose record has no
    such field — sending those anyway would shift every following byte and be
    silently misread by the firmware.
    """


class SegmentMaskError(GoveeProtocolError):
    """The requested segment mask is empty or out of range.

    An all-zero mask is accepted by the firmware and silently does nothing,
    so we refuse to send one.
    """
