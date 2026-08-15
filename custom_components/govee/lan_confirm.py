"""Echo-lag awareness for the LAN verify-by-read confirm (fork feature).

Some SKUs keep reporting their PRE-command state for a while after a write (the
H60B0 for ~1.5 s), so a confirm read taken immediately can only fail.
:func:`async_settle` waits that lag out before the read; :func:`counts_as_miss`
refuses to count a readback that still landed inside it. Both measure from the
SEND, which callers must pass in. A SKU whose profile declares no lag gets 0.0
and every function here becomes a no-op — upstream's semantics exactly.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .api.protocol import GoveeProtocolError, get_profile

_LOGGER = logging.getLogger(__name__)


def echo_lag_seconds(sku: str) -> float:
    """How long ``sku`` keeps reporting its pre-command state after a write.

    Args:
        sku: The device model (``"H60B0"``).

    Returns:
        The measured echo lag in seconds, or ``0.0`` when this SKU has no
        profile or its profile has not declared one — in which case every
        caller here becomes a no-op and upstream's behaviour is unchanged.
    """
    try:
        profile = get_profile(sku)
    except GoveeProtocolError:
        return 0.0
    return max(0.0, float(profile.echo_lag_seconds))


def confirm_read_delay(sku: str) -> float:
    """How long to wait after a LAN write before the confirm read is issued.

    Equal to the echo lag: reading any sooner returns the pre-write state, so
    the confirm could only fail. Kept as its own name because it is a *policy*
    (when to read) built on a *measurement* (how long the device lies), and the
    two are free to diverge later.
    """
    return echo_lag_seconds(sku)


async def async_settle(sku: str, *, since: float | None = None) -> float:
    """Wait out ``sku``'s echo lag so the confirm read lands on real state.

    Args:
        sku: The device model being written to.
        since: ``time.monotonic()`` at the send. Time already spent between the
            send and this call counts toward the lag rather than being added to
            it — which is what a deferred confirm, started well after its
            datagram, needs. None waits the whole lag from now.

    Returns:
        The number of seconds actually waited — ``0.0`` for every SKU without a
        declared lag, and for a caller whose window has already passed.
    """
    delay = confirm_read_delay(sku)
    if since is not None:
        delay -= time.monotonic() - since
    if delay <= 0:
        return 0.0
    _LOGGER.debug("Govee LAN confirm: waiting %.2fs for %s's echo to settle before the confirm read", delay, sku)
    await asyncio.sleep(delay)
    return delay


def counts_as_miss(sku: str, elapsed: float) -> bool:
    """Whether an unconfirmed LAN readback may arm the #57 suppression streak.

    A readback taken before the SKU's echo has settled is *unconfirmable*, not
    failed: the device is contractually still reporting its pre-command state,
    so neither a mismatch nor a timeout says anything about the write or the
    transport. Counting it guarantees the cooldown on hardware that behaves
    exactly as documented, which is the bug this exists to stop.

    Args:
        sku: The device model that was written to.
        elapsed: Seconds between the LAN write leaving the socket and the
            confirm read completing (or timing out). Measured from the send —
            timing it from the start of the confirm instead puts the settle
            wait inside the figure, and the check can then never refuse.

    Returns:
        ``True`` when the miss is real evidence and should be counted. Always
        ``True`` for a SKU with no declared echo lag, so upstream's accounting
        is untouched for every device the fork has not measured.
    """
    lag = echo_lag_seconds(sku)
    if lag <= 0:
        return True
    if elapsed >= lag:
        return True
    _LOGGER.debug(
        "Govee LAN confirm: %s answered %.3fs after the write, inside its %.2fs echo window — "
        "unconfirmable, not counted toward LAN write suppression",
        sku,
        elapsed,
        lag,
    )
    return False
