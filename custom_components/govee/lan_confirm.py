"""Echo-lag awareness for the LAN verify-by-read confirm (fork feature).

The problem this solves
-----------------------
Upstream's LAN control tier is *verified*: it fires the UDP write, then reads
``devStatus`` back within :data:`~.const.LAN_WRITE_CONFIRM_TIMEOUT` (0.5 s) and
requires the reported value to match what it sent. A readback that does not
match — or does not arrive — is counted toward issue #57's write-suppression
streak, and three of them put the device's LAN writes into a 300 s cooldown.

That rule assumes the device answers with post-command state. Some SKUs do not.
The H60B0 keeps echoing its **pre-command** state for roughly 1.5 s after a
write; the protocol reference states the rule outright (§2.1: *"Do not read back
sooner than ~1.5 s after a write, or the poll returns the pre-write state and an
optimistic client flip-flops"*, write→visible latency 0.3–1.1 s, 1.09 s worst
observed on an H60B0 raw power-on).

Read a device like that 28 ms after the write and the confirm can only fail. So
on this SKU every master power/brightness write was a guaranteed "miss":

* each one paid the 0.5 s confirm wait and then re-sent over MQTT/REST, landing
  ~1.9 s late as a second, visible whole-lamp brightness step (the flicker that
  started this investigation), and
* two scene applies were enough to reach the threshold of 3 and arm the #57
  cooldown, which then routed the lamp's writes to the cloud for five minutes.

Neither the write nor the transport was ever at fault. The confirm was simply
being taken before the answer existed.

The fix, in two parts
---------------------
1. **Settle before reading.** :func:`async_settle` waits out the SKU's declared
   echo lag before the confirm read is issued, so the read lands on state the
   device has actually updated. The confirm then means what it says: a match is
   real confirmation (the write is done, no cloud duplicate goes out, no
   flicker), and a mismatch is real evidence.
2. **Never count an unconfirmable read.** :func:`counts_as_miss` refuses to
   arm the #57 streak on a readback taken *before* the echo settled. Part 1
   normally prevents that from happening at all; this is the backstop for a
   device that echoes longer than the table says, and it is what keeps a
   guaranteed-stale answer out of the miss counter.

Where the knowledge lives
-------------------------
In the profile table, as ``DeviceProfile.echo_lag_seconds`` — this is per-SKU
firmware behaviour, exactly the kind of fact
:mod:`.api.protocol.profiles` exists to hold, and it is measured, not guessed.
A SKU with no profile, or a profile that has not declared a lag, gets ``0.0``:
no settle wait, and every unconfirmed read counts, i.e. **upstream's semantics
byte for byte**. Only a SKU the fork has measured opts in.

Latency note
------------
The settle wait is not free — a confirmed LAN write on an H60B0 now takes
~1.5 s instead of ~0.03 s. It is still an improvement on what it replaces: the
old path spent 0.5 s failing to confirm and then a full cloud round trip
(~1.9 s) re-sending, for ~2.4 s and a visible double-step. The entity's
optimistic state is applied before the confirm either way, so the UI still
responds in ~0 ms; only the service call's own duration changes.
"""

from __future__ import annotations

import asyncio
import logging

from .zone_state import profile_for

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
    profile = profile_for(sku)
    if profile is None:
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


async def async_settle(sku: str) -> float:
    """Wait out ``sku``'s echo lag so the confirm read lands on real state.

    Args:
        sku: The device model being written to.

    Returns:
        The number of seconds actually waited — ``0.0`` for every SKU without a
        declared lag, which is the no-op case the whole module defaults to.
    """
    delay = confirm_read_delay(sku)
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
            confirm read completing (or timing out).

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
