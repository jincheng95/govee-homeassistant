"""Child-entity power sync to the whole-device light (fork feature).

Zones and segments are *children* of one lamp: neither has any power of its
own, and lighting one while the lamp is reported off produces a child entity
that says "on" above a device that says "off" — the exact state the zone
lights were fixed to avoid.

The zone lights have always resolved this by powering the lamp on the **normal
transport** before their own (raw-LAN) write goes out: the whole-device power
command is the only one with real state behind it, so it goes through
``coordinator.async_control_device`` (BLE > LAN > MQTT > REST, with upstream's
optimistic update) rather than as a raw frame. The segment entities did not,
so painting a segment left the master off. This module is that one rule,
extracted so both platforms call the same code.

Deliberately one-directional: a child turning **off** must never power the lamp
off, because the other children may still be lit and nothing reports per-child
power back (see roadmap 1.5).

Send, not confirm
-----------------
The child's own (raw-LAN) frames only need the power datagram to have LEFT the
host before they follow — the lamp processes them in arrival order, and the
power write takes ~30 ms to hit the wire. What they do **not** need is the
answer to "did the lamp confirm it?", which on an H60B0 costs the SKU's ~1.5 s
echo settle plus the confirm read (see :mod:`.lan_confirm`). Awaiting the whole
pipeline pushed the first zone frame from ~0.5 s after the trigger to ~1.6 s —
a delay the user sees, spent waiting for a fact nothing here consumes.

So the power-on is dispatched with ``defer_lan_confirm=True``: the send is
inline, and the settle + confirm — including its MQTT/REST re-send when the
confirm genuinely fails, and the issue-#57 miss counting that arms the write
cooldown — run in a coordinator-owned background task. Nothing about the
confirm's *semantics* changes; it simply stops being on the caller's critical
path.

The latch
---------
The "is the lamp off?" question is answered from ``coordinator.get_state``, and
that snapshot is subject to the same echo lag the LAN confirm read is (see
:mod:`.lan_confirm`): on the H60B0 the lamp keeps reporting ``onOff: 0`` for
~1.5 s after it has been told to switch on. A scene that lights two zones back
to back therefore asks the question twice and gets "off" both times, and the
second call sends a whole-device power-on the lamp is already executing.

So a send is remembered for :data:`POWER_LATCH_SECONDS` per device and any
further call inside that window stands down *regardless of the state snapshot*.
The latch is deliberately the shorter half of the rule: it never sends a power
command that was not going to be sent anyway, it only skips a duplicate. If the
first command genuinely failed, the latch expires in two seconds and the next
child turn_on re-sends it.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final

from .models import PowerCommand

if TYPE_CHECKING:  # pragma: no cover
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# How long a whole-device power-on suppresses the next one. Comfortably longer
# than the worst echo lag in the profile table (the H60B0's 1.5 s), which is the
# window in which the state snapshot can still say "off" about a lamp that has
# already been told to switch on; short enough that a genuinely failed power
# command is retried by the next child turn_on rather than being swallowed.
POWER_LATCH_SECONDS: Final = 2.0

# Where the per-coordinator latch store hangs. Set on the coordinator rather
# than built in its __init__ on purpose: no upstream line to conflict — the
# same discipline :mod:`.zone_state` uses for its registry.
_LATCH_ATTR: Final = "_govee_child_power_latch"


def _latches(coordinator: GoveeCoordinator) -> dict[str, float]:
    """The coordinator's ``device_id -> monotonic deadline`` latch store.

    Type-checked rather than None-checked: this reads an attribute off an
    object the module does not own, and a test double (or anything else that
    answers every ``getattr``) must yield a real store instead of something
    that compares nonsensically against a clock.
    """
    existing = getattr(coordinator, _LATCH_ATTR, None)
    if not isinstance(existing, dict):
        existing = {}
        setattr(coordinator, _LATCH_ATTR, existing)
    return existing


def _latched(coordinator: GoveeCoordinator, device_id: str) -> bool:
    """Whether a whole-device power-on for ``device_id`` is still in flight.

    Monotonic throughout: a wall-clock jump (NTP step, DST, a suspend/resume)
    must never make a 100 ms-old command look two seconds old or vice versa.
    """
    deadline = _latches(coordinator).get(device_id)
    return deadline is not None and time.monotonic() < deadline


def _latch(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Remember that a whole-device power-on has just been sent."""
    _latches(coordinator)[device_id] = time.monotonic() + POWER_LATCH_SECONDS


async def async_ensure_device_powered(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Power the lamp on over the normal path when it is reported off.

    A no-op when the device is already on, when nothing is known about it — an
    unknown state is not evidence that the lamp is off, and sending a power
    command on a guess would light a lamp the user did not ask to light — or
    when one was already sent within the last :data:`POWER_LATCH_SECONDS`.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id of the parent lamp.
    """
    state = coordinator.get_state(device_id)
    if state is None or state.power_state:
        return
    if _latched(coordinator, device_id):
        # The snapshot says "off" because the lamp is still echoing its
        # pre-command state, not because the last power-on did not happen.
        _LOGGER.debug(
            "Govee child power sync: a power-on for %s is already in flight — not sending another",
            device_id,
        )
        return
    # Latch BEFORE the await: two zone turn_ons a hundred milliseconds apart are
    # two coroutines on one loop, and a latch set after the command would let
    # the second one through while the first is suspended.
    _latch(coordinator, device_id)
    _LOGGER.debug("Govee child power sync: powering %s on for a child turn_on", device_id)
    # Send-blocking, not confirm-blocking: see the module docstring.
    await coordinator.async_control_device(
        device_id, PowerCommand(power_on=True), defer_lan_confirm=True
    )
