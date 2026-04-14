"""DataUpdateCoordinator for Govee integration.

Manages device discovery, state polling, and MQTT integration.
Implements IStateProvider protocol for clean architecture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GoveeApiClient,
    GoveeApiError,
    GoveeAuthError,
    GoveeAwsIotClient,
    GoveeDeviceNotFoundError,
    GoveeIotCredentials,
    GoveeRateLimitError,
)
from .api.auth import GoveeAuthClient
from .api.ble_packet import DIY_STYLE_NAMES
from .ble_passthrough import BlePassthroughManager

# BLE direct support — conditionally imported to avoid hard Bluetooth dependency
try:
    from homeassistant.components import bluetooth
    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothScanningMode,
    )
    from .api.ble import (
        BLE_COMMAND_SUPPORTED_MODELS,
        GoveeBLEDevice,
        SEGMENTED_MODELS,
    )

    HAS_BLUETOOTH = True
except ImportError:  # pragma: no cover — HA installs without Bluetooth
    HAS_BLUETOOTH = False
from .const import (
    DOMAIN,
    GOVEE_BLE_MANUFACTURER_IDS,
    OPTIMISTIC_GRACE_CAP_SECONDS,
)
from .models import (
    GoveeDevice,
    GoveeDeviceState,
    RGBColor,
    TRANSPORT_KINDS,
    TransportHealth,
    TransportKind,
)
from .models.commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DeviceCommand,
    DIYSceneCommand,
    ModeCommand,
    MusicModeCommand,
    PowerCommand,
    SceneCommand,
    SegmentColorCommand,
    TemperatureSettingCommand,
    ToggleCommand,
    WorkModeCommand,
    create_dreamview_command,
)
from .models.device import (
    INSTANCE_DREAMVIEW,
    INSTANCE_HDMI_SOURCE,
    INSTANCE_THERMOSTAT_TOGGLE,
)
from .protocols import IStateObserver
from .scene_cache import SceneCacheManager
from .repairs import (
    async_create_auth_issue,
    async_create_mqtt_issue,
    async_create_rate_limit_issue,
    async_delete_auth_issue,
    async_delete_mqtt_issue,
    async_delete_rate_limit_issue,
)

_LOGGER = logging.getLogger(__name__)

# State fetch timeout per device
STATE_FETCH_TIMEOUT = 30

# Segment command pacing — Govee silently rate-limits bursts of segment
# updates on RGBIC strips (H80A1 has 14 segments). Serialize per-device
# with a small gap so a "scene" that hits every segment doesn't drop
# commands with empty JSON responses (issue #53).
SEGMENT_COMMAND_PACING_SECONDS = 0.12

# BLE advertisement staleness threshold. When the last advertisement
# seen for a BLE device is older than this, mark the BLE transport
# unavailable. Govee devices typically re-advertise every few seconds.
BLE_STALE_SECONDS = 120

# BLE advertising name prefixes used by Govee devices
_BLE_NAME_PREFIXES = ("Govee_*", "ihoment_*", "GBK_*")


def _sku_from_ble_name(name: str | None) -> str | None:
    """Extract SKU from BLE advertising name like ``Govee_H6072_754B``.

    Govee BLE lights advertise with names following the pattern
    ``<Prefix>_<SKU>_<Suffix>`` where the SKU starts with ``H`` followed
    by 3-4 alphanumeric characters. Returns ``None`` if no SKU can be
    parsed (device will be skipped for BLE correlation).
    """
    if not name:
        return None
    for part in name.split("_"):
        if part.startswith("H") and len(part) >= 4 and part[1:].isalnum():
            return part
    return None


class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device state management.

    Features:
    - Parallel state fetching for all devices
    - MQTT integration for real-time updates
    - Scene caching
    - Optimistic state updates
    - Group device handling

    Implements IStateProvider protocol for entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api_client: GoveeApiClient,
        iot_credentials: GoveeIotCredentials | None,
        poll_interval: int,
        enable_groups: bool = False,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance.
            config_entry: Config entry for this integration.
            api_client: Govee REST API client.
            iot_credentials: Optional IoT credentials for MQTT.
            poll_interval: Polling interval in seconds.
            enable_groups: Whether to include group devices.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
            always_update=False,
        )

        self._config_entry = config_entry
        self._api_client = api_client
        self._iot_credentials = iot_credentials
        self._enable_groups = enable_groups

        # Device registry
        self._devices: dict[str, GoveeDevice] = {}

        # State cache
        self._states: dict[str, GoveeDeviceState] = {}

        # Per-device transport health: device_id -> kind -> TransportHealth.
        # Drives the optional per-device connectivity binary_sensors and
        # feeds the existing transport diagnostic attributes.
        self._transport_health: dict[str, dict[TransportKind, TransportHealth]] = {}

        # Per-device asyncio.Lock for serializing segment commands. Govee
        # rate-limits parallel segment dispatch with empty-body 200s; the
        # lock keeps one in flight at a time, paced by
        # SEGMENT_COMMAND_PACING_SECONDS to stay under the 100/min cap.
        self._segment_locks: dict[str, asyncio.Lock] = {}

        # Scene cache manager
        self._scene_cache = SceneCacheManager(api_client)

        # Observers for state changes
        self._observers: list[IStateObserver] = []

        # MQTT client for real-time updates
        self._mqtt_client: GoveeAwsIotClient | None = None

        # Device-specific MQTT topics from undocumented API
        # Maps device_id -> MQTT topic for publishing commands
        self._device_topics: dict[str, str] = {}

        # BLE passthrough manager for MQTT-based commands
        self._ble_manager = BlePassthroughManager(
            get_mqtt_client=lambda: self._mqtt_client,
            device_topics=self._device_topics,
            ensure_device_topic=self._ensure_device_topic,
        )

        # BLE direct transport — per-device GoveeBLEDevice instances
        # populated dynamically from Bluetooth advertisements.
        self._ble_devices: dict[str, GoveeBLEDevice] = {} if HAS_BLUETOOTH else {}

        # SKUs for which we've already logged "advertised but not on the
        # BLE command allowlist" — avoid spamming the log on every advert.
        self._ble_ignored_skus_logged: set[str] = set()

        # Track in-flight power-off commands so segment entities can
        # avoid racing with a concurrent device power-off (issue #16).
        self._pending_power_off: set[str] = set()

        # Track rate limit state to avoid spamming repair issues
        self._rate_limited: bool = False

        # Store original poll interval for restoring after rate limit backoff
        self._original_update_interval = timedelta(seconds=poll_interval)

    @property
    def devices(self) -> dict[str, GoveeDevice]:
        """Get all discovered devices."""
        return self._devices

    @property
    def api_rate_limit_remaining(self) -> int:
        """Return API rate limit remaining."""
        return self._api_client.rate_limit_remaining

    @property
    def api_rate_limit_total(self) -> int:
        """Return API rate limit total."""
        return self._api_client.rate_limit_total

    @property
    def api_rate_limit_reset(self) -> int:
        """Return API rate limit reset time."""
        return self._api_client.rate_limit_reset

    @property
    def mqtt_client(self) -> GoveeAwsIotClient | None:
        """Return MQTT client instance."""
        return self._mqtt_client

    @property
    def scene_cache_count(self) -> int:
        """Return number of devices with cached scenes."""
        return self._scene_cache.scene_cache_count

    @property
    def diy_scene_cache_count(self) -> int:
        """Return number of devices with cached DIY scenes."""
        return self._scene_cache.diy_scene_cache_count

    @property
    def mqtt_connected(self) -> bool:
        """Return True if MQTT client is connected."""
        return self._mqtt_client is not None and self._mqtt_client.connected

    def is_ble_available(self, device_id: str) -> bool:
        """Return True if a BLE transport is active for this device."""
        return device_id in self._ble_devices

    # ------------------------------------------------------------------ #
    # Transport health (per-device connectivity tracking)
    # ------------------------------------------------------------------ #

    def _ensure_transport_health(self, device_id: str) -> None:
        """Initialize transport-health entries for a device if missing."""
        if device_id in self._transport_health:
            return
        self._transport_health[device_id] = {
            kind: TransportHealth(transport=kind) for kind in TRANSPORT_KINDS
        }

    def get_transport_health(
        self,
        device_id: str,
        transport: TransportKind,
    ) -> TransportHealth | None:
        """Return health for (device, transport), or None if untracked."""
        per_device = self._transport_health.get(device_id)
        if per_device is None:
            return None
        return per_device.get(transport)

    def _record_transport_success(
        self,
        device_id: str,
        transport: TransportKind,
    ) -> None:
        """Stamp a successful transport use."""
        self._ensure_transport_health(device_id)
        self._transport_health[device_id][transport].mark_success(
            datetime.now(timezone.utc)
        )

    def _record_transport_failure(
        self,
        device_id: str,
        transport: TransportKind,
        reason: str,
    ) -> None:
        """Stamp a failed transport use."""
        self._ensure_transport_health(device_id)
        self._transport_health[device_id][transport].mark_failure(
            datetime.now(timezone.utc), reason
        )

    def _refresh_mqtt_health(self) -> None:
        """Propagate MQTT client connection state to all device health entries.

        MVP uses polling each coordinator cycle; a push callback from the
        MQTT client is a follow-up enhancement.
        """
        connected = self.mqtt_connected
        for device_id in self._devices:
            self._ensure_transport_health(device_id)
            mqtt = self._transport_health[device_id]["mqtt"]
            if connected:
                # Don't backdate last_success_ts here — only real pushes do that.
                mqtt.is_available = True
                mqtt.last_failure_reason = None
            else:
                mqtt.is_available = False
                if self._mqtt_client is None:
                    mqtt.last_failure_reason = "not_configured"
                else:
                    mqtt.last_failure_reason = "disconnected"

    def _refresh_ble_staleness(self) -> None:
        """Mark BLE unavailable for devices whose last advertisement is stale."""
        now = datetime.now(timezone.utc)
        for device_id in self._devices:
            self._ensure_transport_health(device_id)
            ble = self._transport_health[device_id]["ble"]
            # Not connected at all
            if device_id not in self._ble_devices:
                ble.is_available = False
                continue
            last = ble.last_success_ts
            if last is None:
                continue
            if (now - last).total_seconds() > BLE_STALE_SECONDS:
                ble.is_available = False
                ble.last_failure_reason = "stale_advertisement"

    @property
    def states(self) -> dict[str, GoveeDeviceState]:
        """Get current states for all devices."""
        return self._states

    def get_device(self, device_id: str) -> GoveeDevice | None:
        """Get device by ID."""
        return self._devices.get(device_id)

    def get_state(self, device_id: str) -> GoveeDeviceState | None:
        """Get current state for a device."""
        return self._states.get(device_id)

    def is_power_off_pending(self, device_id: str) -> bool:
        """Return True if a power-off command is in flight for this device.

        Segment entities use this to avoid racing with a concurrent power-off.
        """
        return device_id in self._pending_power_off

    def register_observer(self, observer: IStateObserver) -> None:
        """Register a state change observer."""
        if observer not in self._observers:
            self._observers.append(observer)

    def unregister_observer(self, observer: IStateObserver) -> None:
        """Unregister a state change observer."""
        if observer in self._observers:
            self._observers.remove(observer)

    # ------------------------------------------------------------------ #
    # BLE direct transport
    # ------------------------------------------------------------------ #

    def setup_ble_subscriptions(self) -> list[Any]:
        """Subscribe to BLE advertisements for nearby Govee devices.

        Called from ``async_setup_entry`` after the coordinator is created.
        Returns a list of unsubscribe callables to pass to
        ``entry.async_on_unload``.

        This is a no-op when ``HAS_BLUETOOTH`` is False (HA installs
        without Bluetooth hardware or the ``bluetooth`` component).
        """
        if not HAS_BLUETOOTH:
            return []

        unsubs: list[Any] = []

        @callback
        def _on_ble_advertisement(service_info: Any, change: Any) -> None:
            self._handle_ble_advertisement(service_info)

        for prefix in _BLE_NAME_PREFIXES:
            unsub = bluetooth.async_register_callback(
                self.hass,
                _on_ble_advertisement,
                BluetoothCallbackMatcher(local_name=prefix, connectable=True),
                BluetoothScanningMode.ACTIVE,
            )
            unsubs.append(unsub)

        # Some Govee SKUs (H6053 / H6076 / H6126, issue #58) don't deliver
        # reliably via name-prefix matchers in all HA Bluetooth setups.
        # Register a complementary manufacturer-ID callback so adverts
        # with the Govee company ID are always captured.
        for mfg_id in GOVEE_BLE_MANUFACTURER_IDS:
            unsub = bluetooth.async_register_callback(
                self.hass,
                _on_ble_advertisement,
                BluetoothCallbackMatcher(manufacturer_id=mfg_id, connectable=True),
                BluetoothScanningMode.ACTIVE,
            )
            unsubs.append(unsub)

        _LOGGER.debug(
            "BLE advertisement subscription active for names=%s manufacturer_ids=%s",
            _BLE_NAME_PREFIXES,
            GOVEE_BLE_MANUFACTURER_IDS,
        )
        return unsubs

    @callback
    def _handle_ble_advertisement(self, service_info: Any) -> None:
        """Correlate a BLE advertisement with a known cloud device.

        Matching strategy (see ``docs/_research/2026-04-09_multi-transport-single-entity.md``):

        1. Extract SKU from the advertising name (``Govee_H6072_754B`` → ``H6072``).
        2. Find cloud devices with that SKU (ignoring group devices).
        3. If exactly one match → unambiguous correlation.
        4. If multiple same-SKU → try MAC-prefix heuristic as tiebreaker.
        5. If no match or ambiguous → skip (device remains cloud-only).
        """
        ble_sku = _sku_from_ble_name(service_info.name)
        if not ble_sku:
            return

        candidates = [
            (did, dev)
            for did, dev in self._devices.items()
            if dev.sku == ble_sku and not dev.is_group
        ]

        matched_id: str | None = None
        if len(candidates) == 1:
            matched_id = candidates[0][0]
        elif len(candidates) > 1:
            # Multiple same-SKU — MAC-prefix tiebreaker (unproven but plausible)
            ble_mac = service_info.address.upper()
            for did, _dev in candidates:
                if did.upper().startswith(ble_mac):
                    matched_id = did
                    break

        if matched_id is None:
            return

        # BLE advertisement visibility is not the same as BLE command
        # capability (issue #59). Some SKUs advertise BLE but silently
        # drop command frames — enrolling them in the dispatch path
        # makes every control command disappear. Only enroll SKUs whose
        # BLE command set is verified.
        if ble_sku not in BLE_COMMAND_SUPPORTED_MODELS:
            if ble_sku not in self._ble_ignored_skus_logged:
                self._ble_ignored_skus_logged.add(ble_sku)
                _LOGGER.info(
                    "%s (SKU=%s) is advertising BLE but is not on the BLE "
                    "command allowlist. Staying cloud-only. If BLE commands "
                    "are known to work for this SKU, please open a GitHub "
                    "issue referencing #59 so it can be added.",
                    self._devices[matched_id].name,
                    ble_sku,
                )
            return

        if matched_id not in self._ble_devices:
            self._ble_devices[matched_id] = GoveeBLEDevice(
                service_info.device,
                segmented=ble_sku in SEGMENTED_MODELS,
            )
            _LOGGER.info(
                "BLE transport available for %s (SKU=%s, BLE=%s)",
                self._devices[matched_id].name,
                ble_sku,
                service_info.address,
            )
        else:
            self._ble_devices[matched_id].set_ble_device_and_advertisement_data(
                service_info.device, service_info.advertisement,
            )

        # Stamp last-seen for the BLE connectivity sensor and notify entities.
        self._record_transport_success(matched_id, "ble")
        # async_set_updated_data requires super().__init__ to have run — guard
        # for tests that instantiate the coordinator via object.__new__().
        try:
            if self.data is not None:
                self.async_set_updated_data(self._states)
        except AttributeError:
            pass

    def _notify_observers(self, device_id: str, state: GoveeDeviceState) -> None:
        """Notify all observers of state change."""
        for observer in self._observers:
            try:
                observer.on_state_changed(device_id, state)
            except Exception as err:
                _LOGGER.warning("Observer notification failed: %s", err)

    async def _async_setup(self) -> None:
        """Set up the coordinator - discover devices and start MQTT.

        Called automatically by async_config_entry_first_refresh().
        """
        # Discover devices
        await self._discover_devices()

        # Start MQTT client if credentials available
        if self._iot_credentials:
            await self._start_mqtt()
            # Fetch device-specific MQTT topics for publishing commands
            await self._fetch_device_topics()

    async def _discover_devices(self) -> None:
        """Discover all devices from Govee API."""
        try:
            devices = await self._api_client.get_devices()

            _LOGGER.info(
                "API returned %d devices (enable_groups=%s)",
                len(devices),
                self._enable_groups,
            )

            for device in devices:
                _LOGGER.debug(
                    "Device: %s (%s) type=%s is_group=%s",
                    device.name,
                    device.device_id,
                    device.device_type,
                    device.is_group,
                )
                # Log capabilities for debugging segment issues
                for cap in device.capabilities:
                    _LOGGER.debug(
                        "  Capability: type=%s instance=%s params=%s",
                        cap.type,
                        cap.instance,
                        cap.parameters,
                    )

                # Filter group devices unless enabled
                if device.is_group and not self._enable_groups:
                    _LOGGER.info(
                        "Skipping group device: %s (device_id=%s) because enable_groups=False",
                        device.name,
                        device.device_id,
                    )
                    continue

                _LOGGER.debug("Adding device to coordinator: %s", device.device_id)
                self._devices[device.device_id] = device
                # Create empty state for each device
                self._states[device.device_id] = GoveeDeviceState.create_empty(
                    device.device_id
                )
                self._ensure_transport_health(device.device_id)

            _LOGGER.info(
                "Discovered %d Govee devices (enable_groups=%s)",
                len(self._devices),
                self._enable_groups,
            )

            # Clean up scene caches for devices no longer discovered
            self._scene_cache.cleanup_stale(set(self._devices))

            # Scene cache is populated lazily via async_get_scenes() / async_get_diy_scenes()
            # during entity setup, avoiding rate limit pressure at startup

            # Clear any auth issues on success
            await async_delete_auth_issue(self.hass, self._config_entry)

        except GoveeAuthError as err:
            # Create repair issue for auth failure
            await async_create_auth_issue(self.hass, self._config_entry)
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            raise UpdateFailed(f"Failed to discover devices: {err}") from err

    async def _start_mqtt(self) -> None:
        """Start MQTT client for real-time updates."""
        if not self._iot_credentials:
            return

        self._mqtt_client = GoveeAwsIotClient(
            credentials=self._iot_credentials,
            on_state_update=self._on_mqtt_state_update,
        )

        if self._mqtt_client.available:
            try:
                await self._mqtt_client.async_start()
                _LOGGER.info("MQTT client started for real-time updates")
                # Clear any MQTT issues on success
                await async_delete_mqtt_issue(self.hass, self._config_entry)
            except Exception as err:
                _LOGGER.warning("MQTT client failed to start: %s", err)
                await async_create_mqtt_issue(
                    self.hass,
                    self._config_entry,
                    str(err),
                )
        else:
            _LOGGER.warning("MQTT library not available")

    async def _fetch_device_topics(self) -> None:
        """Fetch device-specific MQTT topics from undocumented Govee API.

        These topics are required for publishing commands (ptReal, etc).
        Device targeting via payload alone doesn't work - AWS IoT requires
        publishing to the device's specific topic.
        """
        if not self._iot_credentials:
            return

        try:
            async with GoveeAuthClient() as auth_client:
                self._device_topics = await auth_client.fetch_device_topics(
                    self._iot_credentials.token
                )
                _LOGGER.info(
                    "Fetched MQTT topics for %d devices",
                    len(self._device_topics),
                )
        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch device topics: %s", err)
            # Continue without device topics - ptReal commands won't work
            # but the integration can still function with polling
        except Exception as err:
            _LOGGER.warning("Unexpected error fetching device topics: %s", err)

    @callback
    def _on_mqtt_state_update(self, device_id: str, state_data: dict[str, Any]) -> None:
        """Handle state update from MQTT.

        Called from aiomqtt's async message loop, which runs on the HA event
        loop. Safe to call async_set_updated_data() directly. The @callback
        decorator documents this event-loop-only contract.
        """
        if device_id not in self._devices:
            _LOGGER.debug("MQTT update for unknown device: %s", device_id)
            return

        state = self._states.get(device_id)
        if state is None:
            state = GoveeDeviceState.create_empty(device_id)
            self._states[device_id] = state

        # Update state from MQTT data
        state.update_from_mqtt(state_data)

        # Confirmed push — record transport health and end the optimistic
        # grace window for this device (state.update_from_mqtt also calls
        # clear_optimistic_window, but recording MQTT health is our job).
        self._record_transport_success(device_id, "mqtt")

        # Update coordinator data and notify HA
        self.async_set_updated_data(self._states)

        # Notify observers
        self._notify_observers(device_id, state)

        _LOGGER.debug(
            "MQTT state applied for %s: power=%s",
            device_id,
            state.power_state,
        )

    async def _async_update_data(self) -> dict[str, GoveeDeviceState]:
        """Fetch state for all devices (parallel).

        Called by DataUpdateCoordinator on poll interval.
        """
        if not self._devices:
            return self._states

        # Create tasks for parallel fetching
        tasks = [
            self._fetch_device_state(device_id, device)
            for device_id, device in self._devices.items()
        ]

        # Scale timeout based on device count (2s per device, min 30s, max 120s)
        timeout = min(max(STATE_FETCH_TIMEOUT, len(self._devices) * 2), 120)

        # Wait for all with timeout
        try:
            async with asyncio.timeout(timeout):
                results = await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            _LOGGER.warning("State fetch timed out after %ds", timeout)
            return self._states

        # Process results
        successful_updates = 0
        for device_id, result in zip(self._devices.keys(), results):
            if isinstance(result, GoveeDeviceState):
                self._states[device_id] = result
                successful_updates += 1
            elif isinstance(result, GoveeAuthError):
                await async_create_auth_issue(self.hass, self._config_entry)
                raise ConfigEntryAuthFailed("Invalid API key") from result
            elif isinstance(result, Exception):
                _LOGGER.debug(
                    "Failed to fetch state for %s: %s",
                    device_id,
                    result,
                )
                # Keep previous state on error

        # Clear rate limit issue and restore poll interval if we got successful updates
        if successful_updates > 0 and self._rate_limited:
            self._rate_limited = False
            self.update_interval = self._original_update_interval
            _LOGGER.info(
                "Rate limit cleared, restoring poll interval to %s",
                self._original_update_interval,
            )
            await async_delete_rate_limit_issue(self.hass, self._config_entry)

        # Refresh transport-health snapshots tied to coordinator cadence.
        self._refresh_mqtt_health()
        self._refresh_ble_staleness()

        return self._states

    async def _fetch_device_state(
        self,
        device_id: str,
        device: GoveeDevice,
    ) -> GoveeDeviceState | Exception:
        """Fetch state for a single device.

        Args:
            device_id: Device identifier.
            device: Device instance.

        Returns:
            GoveeDeviceState or Exception on error.
        """
        # Skip API call for group devices - state fetch always fails with 400
        if device.is_group:
            existing = self._states.get(device_id)
            if existing:
                existing.online = True  # Group devices are always "available"
                return existing
            return GoveeDeviceState.create_empty(device_id)

        try:
            state = await self._api_client.get_device_state(device_id, device.sku)
            self._record_transport_success(device_id, "cloud_api")

            # Preserve optimistic state fields that API doesn't reliably return.
            # Clear them when device is turned off (no longer active).
            existing_state = self._states.get(device_id)
            if existing_state:
                # Optimistic grace period (issue #60): if a control command
                # just fired, the device may not yet be visible to the cloud
                # (e.g. BLE-out-of-range or slow AWS propagation). Preserve
                # the optimistic power/brightness for a short window instead
                # of flipflopping the UI. MQTT pushes clear the window early.
                grace_cap = OPTIMISTIC_GRACE_CAP_SECONDS
                poll_seconds = (
                    self.update_interval.total_seconds()
                    if self.update_interval is not None
                    else 60.0
                )
                grace_window = min(2 * poll_seconds, grace_cap)
                optimistic_ts = existing_state.last_optimistic_update
                in_grace = (
                    existing_state.source == "optimistic"
                    and optimistic_ts is not None
                    and (time.monotonic() - optimistic_ts) < grace_window
                )
                if in_grace and existing_state.power_state != state.power_state:
                    _LOGGER.debug(
                        "Preserving optimistic power for %s during %ds grace "
                        "(API=%s optimistic=%s)",
                        device_id,
                        int(grace_window),
                        state.power_state,
                        existing_state.power_state,
                    )
                    state.power_state = existing_state.power_state
                    state.brightness = existing_state.brightness
                    state.source = "optimistic"
                    state.last_optimistic_update = optimistic_ts

                # Log state transitions from API for debugging stale-state issues
                if existing_state.power_state != state.power_state:
                    _LOGGER.debug(
                        "API state change for %s: power %s -> %s (was source=%s)",
                        device_id,
                        existing_state.power_state,
                        state.power_state,
                        existing_state.source,
                    )
                if existing_state.brightness != state.brightness:
                    _LOGGER.debug(
                        "API state change for %s: brightness %s -> %s",
                        device_id,
                        existing_state.brightness,
                        state.brightness,
                    )
                # Scenes persist on device across power cycles — always preserve
                if existing_state.active_scene:
                    state.active_scene = existing_state.active_scene
                if existing_state.active_scene_name:
                    state.active_scene_name = existing_state.active_scene_name
                # DIY scenes also persist across power cycles
                if existing_state.active_diy_scene:
                    state.active_diy_scene = existing_state.active_diy_scene

                # Govee API returns colorRgb=0 when the device is in certain
                # modes (scenes, color_temp, music, etc.).  This sentinel value
                # is not meaningful — preserve the last known real color so the
                # HA UI doesn't flash black after every poll cycle.
                if (
                    state.color is not None
                    and state.color.as_packed_int == 0
                    and existing_state.color is not None
                    and existing_state.color.as_packed_int != 0
                ):
                    state.color = existing_state.color
                # Preserve restore-target fields across API polls.
                # These are "memory" fields — always preserved regardless of power state.
                if existing_state.last_color is not None:
                    state.last_color = existing_state.last_color
                if existing_state.last_color_temp_kelvin is not None:
                    state.last_color_temp_kelvin = existing_state.last_color_temp_kelvin
                if existing_state.last_scene_id is not None:
                    state.last_scene_id = existing_state.last_scene_id
                if existing_state.last_scene_name is not None:
                    state.last_scene_name = existing_state.last_scene_name

                # Heater state: preserve across polls (API doesn't reliably return these)
                if existing_state.heater_temperature is not None:
                    state.heater_temperature = existing_state.heater_temperature
                if existing_state.heater_auto_stop is not None:
                    state.heater_auto_stop = existing_state.heater_auto_stop

                self._preserve_optimistic_field(
                    existing_state, state, device_id, "dreamview_enabled", "DreamView"
                )
                # Music mode has extra fields to preserve alongside the flag
                if existing_state.music_mode_enabled:
                    if state.power_state:
                        state.music_mode_enabled = existing_state.music_mode_enabled
                        state.music_mode_value = existing_state.music_mode_value
                        state.music_mode_name = existing_state.music_mode_name
                        state.music_sensitivity = existing_state.music_sensitivity
                    else:
                        _LOGGER.debug(
                            "Clearing music mode for %s (device turned off)",
                            device_id,
                        )

            return state

        except GoveeDeviceNotFoundError:
            # Expected for group devices - use existing/optimistic state
            _LOGGER.debug(
                "State query failed for group device %s [expected]", device_id
            )
            existing = self._states.get(device_id)
            if existing:
                existing.online = True  # Group devices are always "available"
                return existing
            return GoveeDeviceState.create_empty(device_id)

        except GoveeRateLimitError as err:
            _LOGGER.warning("Rate limit hit, keeping previous state")
            # Create rate limit repair issue and back off (only once)
            if not self._rate_limited:
                self._rate_limited = True
                reset_time = "unknown"
                # Back off: increase poll interval to retry_after or 120s
                backoff_seconds = int(err.retry_after) if err.retry_after else 120
                self.update_interval = timedelta(seconds=backoff_seconds)
                _LOGGER.warning(
                    "Rate limited, increasing poll interval to %ds",
                    backoff_seconds,
                )
                if err.retry_after:
                    reset_time = f"{int(err.retry_after)} seconds"
                self.hass.async_create_task(
                    async_create_rate_limit_issue(
                        self.hass,
                        self._config_entry,
                        reset_time,
                    )
                )
            existing = self._states.get(device_id)
            return existing if existing else GoveeDeviceState.create_empty(device_id)

        except Exception as err:
            self._record_transport_failure(device_id, "cloud_api", str(err))
            return err

    async def async_control_device(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> bool:
        """Send control command to device with optimistic update.

        Args:
            device_id: Device identifier.
            command: Command to execute.

        Returns:
            True if command succeeded.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device: %s", device_id)
            return False

        # Track power-off commands so segment entities can detect them
        # before the first await, ensuring concurrent coroutines see the flag.
        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            self._pending_power_off.add(device_id)

        try:
            # BLE-first dispatch: if a BLE transport is available for this
            # device, try it before the cloud REST API. BLE is ~10x faster
            # (~50ms local vs ~500ms cloud) and works when internet is down.
            if HAS_BLUETOOTH and device_id in self._ble_devices:
                if await self._try_ble_command(device_id, command):
                    self._apply_optimistic_update(device_id, command)
                    self.async_set_updated_data(self._states)
                    return True
                # BLE failed — fall through to REST

            # Serialize segment commands per device. Govee silently drops
            # parallel segment requests (issue #53); sequential dispatch
            # with a small gap respects the 100/min rate limit. Optimistic
            # state is applied before entering the lock so UI feedback is
            # immediate; the actual REST write trails by <~1.5s for a
            # full 14-segment burst on RGBIC strips.
            if isinstance(command, SegmentColorCommand):
                self._apply_optimistic_update(device_id, command)
                self.async_set_updated_data(self._states)
                success = await self._dispatch_segment_command(
                    device_id, device.sku, command
                )
                return success

            success = await self._api_client.control_device(
                device_id,
                device.sku,
                command,
            )

            if success:
                self._record_transport_success(device_id, "cloud_api")
                # Apply optimistic update
                self._apply_optimistic_update(device_id, command)
                self.async_set_updated_data(self._states)
            else:
                self._record_transport_failure(
                    device_id, "cloud_api", "control_returned_false"
                )

            return success

        except GoveeAuthError as err:
            self._record_transport_failure(device_id, "cloud_api", "auth_failed")
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            _LOGGER.error("Control command failed: %s", err)
            self._record_transport_failure(device_id, "cloud_api", str(err))
            return False
        finally:
            if is_power_off:
                self._pending_power_off.discard(device_id)

    async def _dispatch_segment_command(
        self,
        device_id: str,
        sku: str,
        command: SegmentColorCommand,
    ) -> bool:
        """Send a single segment command under a per-device lock.

        Pacing: after each REST call we hold the lock for a short delay so
        the next segment command has to wait — this bounds the effective
        dispatch rate for a multi-segment burst to stay within Govee's
        rate limit without adding a separate queue.
        """
        lock = self._segment_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            try:
                success = await self._api_client.control_device(
                    device_id, sku, command
                )
            except GoveeAuthError:
                self._record_transport_failure(device_id, "cloud_api", "auth_failed")
                raise
            except GoveeApiError as err:
                _LOGGER.error(
                    "Segment command failed for %s: %s", device_id, err
                )
                self._record_transport_failure(device_id, "cloud_api", str(err))
                return False

            if success:
                self._record_transport_success(device_id, "cloud_api")
            else:
                self._record_transport_failure(
                    device_id, "cloud_api", "segment_returned_false"
                )

            # Pace the next acquire so bursts don't trip silent rate limiting.
            await asyncio.sleep(SEGMENT_COMMAND_PACING_SECONDS)
            return success

    async def _try_ble_command(self, device_id: str, command: DeviceCommand) -> bool:
        """Attempt to send a command via BLE. Returns True on success.

        Only power, brightness, and RGB color commands are BLE-capable.
        Scenes, color_temp, work modes, etc. fall through to REST.
        """
        ble_device = self._ble_devices.get(device_id)
        if ble_device is None:
            return False

        try:
            if isinstance(command, PowerCommand):
                if command.power_on:
                    await ble_device.turn_on()
                else:
                    await ble_device.turn_off()
            elif isinstance(command, BrightnessCommand):
                await ble_device.set_brightness(command.brightness)
            elif isinstance(command, ColorCommand):
                await ble_device.set_rgb(
                    command.color.r, command.color.g, command.color.b,
                )
            else:
                # Not BLE-capable (scenes, color_temp, work modes, etc.)
                return False
        except Exception as err:
            _LOGGER.debug(
                "BLE command failed for %s, falling back to REST",
                device_id,
                exc_info=True,
            )
            self._record_transport_failure(device_id, "ble", str(err))
            return False
        else:
            _LOGGER.debug("BLE command succeeded for %s: %s", device_id, type(command).__name__)
            self._record_transport_success(device_id, "ble")
            return True

    async def _ensure_device_topic(self, device_id: str) -> str | None:
        """Get device MQTT topic, refreshing if needed.

        If the topic is missing for this device but we have credentials,
        attempt a single refresh from the API.
        """
        topic = self._device_topics.get(device_id)
        if topic is not None:
            return topic

        # Topic missing - try one refresh
        if self._iot_credentials:
            _LOGGER.debug("Device topic missing for %s, refreshing from API", device_id)
            await self._fetch_device_topics()
            topic = self._device_topics.get(device_id)
            if topic:
                _LOGGER.debug("Got device topic for %s after refresh", device_id)

        return topic

    async def async_send_music_mode(
        self,
        device_id: str,
        enabled: bool,
        sensitivity: int = 50,
        music_mode: int = 1,
        last_scene_id: str | None = None,
        last_scene_name: str | None = None,
    ) -> bool:
        """Send music mode command via REST API first, with BLE fallback.

        Tries REST API for devices with STRUCT music mode capability,
        then falls back to BLE passthrough via MQTT.

        Args:
            device_id: Device identifier.
            enabled: True to enable music mode, False to disable.
            sensitivity: Microphone sensitivity 0-100 (default 50).
            music_mode: Music mode value (default 1 = Rhythm).
            last_scene_id: Last active scene ID (for restoring on disable).
            last_scene_name: Last active scene name (for restoring on disable).

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for music mode: %s", device_id)
            return False

        # Try REST API first for devices with STRUCT music mode capability
        if device.has_struct_music_mode:
            if enabled:
                try:
                    command = MusicModeCommand(
                        music_mode=music_mode,
                        sensitivity=sensitivity,
                        auto_color=1,
                    )
                    success = await self.async_control_device(device_id, command)
                    if success:
                        _LOGGER.debug(
                            "Sent music mode ON to %s via REST API", device.name
                        )
                        return True
                except ConfigEntryAuthFailed:
                    raise
                except Exception as err:
                    _LOGGER.debug(
                        "REST music mode ON failed for %s: %s, trying BLE",
                        device.name,
                        err,
                    )
            else:
                # Disable music mode via REST: restore last scene or send brightness
                success = await self._rest_disable_music_mode(
                    device_id, last_scene_id, last_scene_name
                )
                if success:
                    return True

        # Fall back to BLE passthrough via MQTT
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send music mode for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_music_mode(
            device_id, device.sku, enabled, sensitivity
        )

        if success:
            # Apply optimistic update to state
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_music_mode(enabled)
            _LOGGER.debug(
                "Sent music mode %s (sensitivity=%d) to %s via BLE",
                "ON" if enabled else "OFF",
                sensitivity,
                device.name,
            )

        return success

    async def _rest_disable_music_mode(
        self,
        device_id: str,
        last_scene_id: str | None = None,
        last_scene_name: str | None = None,
    ) -> bool:
        """Disable music mode via REST API.

        Tries to restore the last active scene, then falls back to a
        brightness command to cleanly exit music mode.

        Args:
            device_id: Device identifier.
            last_scene_id: Scene ID to restore.
            last_scene_name: Scene name to restore.

        Returns:
            True if successfully disabled via REST.
        """
        device = self._devices.get(device_id)
        success = False

        # Try restoring last active scene
        if last_scene_id and last_scene_name:
            command = SceneCommand(
                scene_id=int(last_scene_id),
                scene_name=last_scene_name,
            )
            success = await self.async_control_device(device_id, command)
            if success:
                _LOGGER.debug(
                    "Restored scene '%s' on %s after music mode off",
                    last_scene_name,
                    device.name if device else device_id,
                )

        if not success:
            # No last scene or scene restore failed - send brightness command
            # to cleanly exit music mode via REST (avoids visible power cycle)
            state = self._states.get(device_id)
            brightness = state.brightness if state and state.brightness else 100
            success = await self.async_control_device(
                device_id, BrightnessCommand(brightness=brightness)
            )
            _LOGGER.debug(
                "Sent brightness command to %s to exit music mode (brightness=%d)",
                device.name if device else device_id,
                brightness,
            )

        if success:
            self.clear_music_mode(device_id)

        return success

    async def async_send_dreamview(self, device_id: str, enabled: bool) -> bool:
        """Send DreamView command via REST API, with BLE fallback.

        Args:
            device_id: Device identifier.
            enabled: True to enable DreamView, False to disable.

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DreamView: %s", device_id)
            return False

        # Try REST API first (works for HTTP-capable devices like H6097)
        try:
            success = await self.async_control_device(
                device_id, create_dreamview_command(enabled)
            )
            if success:
                _LOGGER.debug(
                    "Sent DreamView %s to %s via REST API",
                    "ON" if enabled else "OFF",
                    device.name,
                )
                return True
        except ConfigEntryAuthFailed:
            # Let authentication errors propagate so Home Assistant can handle reauth
            raise
        except Exception as err:
            _LOGGER.debug("REST DreamView failed for %s: %s", device.name, err)

        # Fall back to BLE passthrough for devices that need it
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send DreamView for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_dreamview(
            device_id, device.sku, enabled
        )

        if success:
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_dreamview(enabled)
            _LOGGER.debug(
                "Sent DreamView %s to %s via BLE passthrough",
                "ON" if enabled else "OFF",
                device.name,
            )

        return success

    async def async_send_diy_scene(
        self,
        device_id: str,
        scene_id: int,
        scene_name: str = "",
    ) -> bool:
        """Send DIY scene command via REST API, with BLE fallback.

        Args:
            device_id: Device identifier.
            scene_id: DIY scene ID from the API.
            scene_name: DIY scene name for logging/state.

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY scene: %s", device_id)
            return False

        # Try REST API first
        try:
            command = DIYSceneCommand(scene_id=scene_id, scene_name=scene_name)
            success = await self.async_control_device(device_id, command)
            if success:
                _LOGGER.debug(
                    "Activated DIY scene '%s' on %s via REST API",
                    scene_name,
                    device.name,
                )
                return True
            _LOGGER.debug(
                "REST DIY scene returned failure for %s, trying BLE passthrough",
                device.name,
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.debug("REST DIY scene failed for %s: %s", device.name, err)

        # Fall back to BLE passthrough
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send DIY scene for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_diy_scene(
            device_id, device.sku, scene_id
        )

        if success:
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_diy_scene(str(scene_id))
            _LOGGER.debug(
                "Activated DIY scene '%s' on %s via BLE passthrough",
                scene_name,
                device.name,
            )

        return success

    async def async_send_diy_style(
        self, device_id: str, style: str, speed: int = 50
    ) -> bool:
        """Send DIY style command via BLE passthrough.

        Note: DIY style changes require complex multi-packet BLE sequences.
        This is a placeholder that applies optimistic state only.
        Full BLE packet implementation is not yet available.

        Args:
            device_id: Device identifier.
            style: DIY style name (Fade, Jumping, Flicker, Marquee, Music).
            speed: Animation speed 0-100 (default 50).

        Returns:
            True if optimistic state was applied.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY style: %s", device_id)
            return False

        style_value = DIY_STYLE_NAMES.get(style)
        if style_value is None:
            _LOGGER.warning("Unknown DIY style: %s", style)
            return False

        _LOGGER.debug(
            "DIY style command for %s is optimistic only - no device command sent. "
            "Full BLE packet implementation is not yet available",
            device.name,
        )

        # Apply optimistic state update
        state = self._states.get(device_id)
        if state:
            state.apply_optimistic_diy_style(style, style_value)

        return False

    @staticmethod
    def _preserve_optimistic_field(
        existing: GoveeDeviceState,
        new: GoveeDeviceState,
        device_id: str,
        field: str,
        label: str,
    ) -> None:
        """Preserve an optimistic state field across API polls.

        If the existing state has a truthy value for the field, preserve it
        on the new state when the device is on. Clear it when the device is off.
        """
        if getattr(existing, field):
            if new.power_state:
                setattr(new, field, getattr(existing, field))
            else:
                _LOGGER.debug(
                    "Clearing %s for %s (device turned off)", label, device_id
                )

    def _apply_optimistic_update(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> None:
        """Apply optimistic state update based on command."""
        state = self._states.get(device_id)
        if not state:
            return

        if isinstance(command, PowerCommand):
            state.apply_optimistic_power(command.power_on)
        elif isinstance(command, BrightnessCommand):
            state.apply_optimistic_brightness(command.brightness)
        elif isinstance(command, ColorCommand):
            state.apply_optimistic_color(command.color)
        elif isinstance(command, ColorTempCommand):
            state.apply_optimistic_color_temp(command.kelvin)
        elif isinstance(command, SceneCommand):
            state.apply_optimistic_scene(str(command.scene_id), command.scene_name)
        elif isinstance(command, DIYSceneCommand):
            state.apply_optimistic_diy_scene(str(command.scene_id))
        elif isinstance(command, ModeCommand):
            if command.mode_instance == INSTANCE_HDMI_SOURCE:
                state.apply_optimistic_hdmi_source(command.value)
        elif isinstance(command, TemperatureSettingCommand):
            state.heater_temperature = command.temperature
            state.heater_auto_stop = command.auto_stop
        elif isinstance(command, WorkModeCommand):
            state.apply_optimistic_work_mode(command.work_mode, command.mode_value)
        elif isinstance(command, MusicModeCommand):
            # Look up mode name from device capabilities for display
            device = self._devices.get(device_id)
            mode_name = None
            if device:
                for opt in device.get_music_mode_options():
                    if opt.get("value") == command.music_mode:
                        mode_name = opt.get("name")
                        break
            state.apply_optimistic_music_mode_struct(
                command.music_mode,
                command.sensitivity,
                mode_name,
            )
        elif isinstance(command, ToggleCommand):
            # Handle toggle commands (DreamView, night light, thermostat, etc)
            if command.toggle_instance == INSTANCE_DREAMVIEW:
                state.apply_optimistic_dreamview(command.enabled)
            elif command.toggle_instance == INSTANCE_THERMOSTAT_TOGGLE:
                state.heater_auto_stop = 1 if command.enabled else 0

    async def async_get_scenes(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available scenes for a device.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            List of scene definitions.
        """
        device = self._devices.get(device_id)
        return await self._scene_cache.async_get_scenes(device_id, device, refresh)

    async def async_get_diy_scenes(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available DIY scenes for a device.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            List of DIY scene definitions.
        """
        device = self._devices.get(device_id)
        return await self._scene_cache.async_get_diy_scenes(device_id, device, refresh)

    async def async_clear_scene(self, device_id: str) -> None:
        """Clear active scene by sending a color/color_temp command to exit it on the device.

        Brightness commands don't exit scenes, so we must send a color or color_temp
        command. Restores the last known color/color_temp when available.
        """
        state = self._states.get(device_id)
        device = self._devices.get(device_id)
        if not state or not device:
            return

        # Nothing to clear if no scene is active
        if not state.active_scene and not state.active_diy_scene:
            self.clear_scene(device_id)
            self.clear_diy_scene(device_id)
            return

        # Resolve the color to restore. Skip RGBColor(0,0,0) — the API returns
        # colorRgb=0 when a scene is running, which is not a meaningful restore target.
        color = state.color or state.last_color
        if color and color.as_packed_int == 0:
            color = state.last_color
        # Final guard: never restore black even if last_color is somehow (0,0,0)
        if color and color.as_packed_int == 0:
            color = None
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        success = False
        if color and device.supports_rgb:
            success = await self.async_control_device(
                device_id, ColorCommand(color=color)
            )
        elif color_temp and device.supports_color_temp:
            success = await self.async_control_device(
                device_id, ColorTempCommand(kelvin=color_temp)
            )
        elif device.supports_rgb:
            # Prefer RGB white — Govee API reflects this value reliably
            # (colorRgb=16777215), unlike color_temp which may return 0.
            success = await self.async_control_device(
                device_id, ColorCommand(color=RGBColor(255, 255, 255))
            )
        elif device.supports_color_temp:
            # Fallback for color-temp-only devices
            ct_range = device.color_temp_range
            if ct_range:
                midpoint = (ct_range.min_kelvin + ct_range.max_kelvin) // 2
            else:
                midpoint = 4000
            success = await self.async_control_device(
                device_id, ColorTempCommand(kelvin=midpoint)
            )

        if success:
            # ColorCommand/ColorTempCommand already clear active_scene via optimistic handlers,
            # but we also need to clear active_diy_scene explicitly.
            self.clear_scene(device_id)
            self.clear_diy_scene(device_id)

    def clear_scene(self, device_id: str) -> None:
        """Clear active scene for a device."""
        state = self._states.get(device_id)
        if state:
            state.active_scene = None
            state.active_scene_name = None
            state.source = "optimistic"

    def clear_diy_scene(self, device_id: str) -> None:
        """Clear active DIY scene for a device."""
        state = self._states.get(device_id)
        if state:
            state.active_diy_scene = None
            state.source = "optimistic"

    def clear_music_mode(self, device_id: str) -> None:
        """Clear music mode state for a device."""
        state = self._states.get(device_id)
        if state:
            state.music_mode_enabled = False
            state.source = "optimistic"

    def restore_group_state(
        self, device_id: str, power: bool, brightness: int | None = None
    ) -> None:
        """Restore state for a group device from HA state machine."""
        state = self._states.get(device_id)
        if state:
            state.power_state = power
            if brightness is not None:
                state.brightness = brightness
            state.source = "optimistic"

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources."""
        # Disconnect all BLE devices
        for ble_device in self._ble_devices.values():
            await ble_device.stop()
        self._ble_devices.clear()

        if self._mqtt_client:
            await self._mqtt_client.async_stop()
            self._mqtt_client = None

        await self._api_client.close()
