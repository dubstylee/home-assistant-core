"""The Cync integration."""

from pycync import Auth, Cync, CyncLight, User
from pycync.exceptions import AuthFailedError, CyncError

from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .const import (
    CONF_AUTHORIZE_STRING,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from .coordinator import CyncConfigEntry, CyncCoordinator

_PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: CyncConfigEntry) -> bool:
    """Set up Cync from a config entry."""
    user_info = User(
        entry.data[CONF_ACCESS_TOKEN],
        entry.data[CONF_REFRESH_TOKEN],
        entry.data[CONF_AUTHORIZE_STRING],
        entry.data[CONF_USER_ID],
        expires_at=entry.data[CONF_EXPIRES_AT],
    )
    cync_auth = Auth(async_get_clientsession(hass), user=user_info)
    ssl_context = get_default_context()

    try:
        cync = await Cync.create(
            auth=cync_auth,
            ssl_context=ssl_context,
        )
    except AuthFailedError as ex:
        raise ConfigEntryAuthFailed("User token invalid") from ex
    except CyncError as ex:
        raise ConfigEntryNotReady("Unable to connect to Cync") from ex

    entry.async_on_unload(cync.shut_down)

    devices_coordinator = CyncCoordinator(hass, entry, cync)

    cync.set_update_callback(devices_coordinator.on_data_update)

    await devices_coordinator.async_config_entry_first_refresh()
    entry.runtime_data = devices_coordinator

    _async_migrate_device_identifiers(hass, entry, cync)

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CyncConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


@callback
def _async_migrate_device_identifiers(
    hass: HomeAssistant, entry: CyncConfigEntry, cync: Cync
) -> None:
    """Migrate light device identifiers using the complete home device lists."""
    identifiers = {
        (DOMAIN, f"{device.parent_home_id}-{device.device_id}"): (
            DOMAIN,
            f"mesh:{device.unique_id}",
        )
        for home in cync.get_homes()
        for device in home.get_flattened_device_list()
        if isinstance(device, CyncLight)
    }
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        new_identifiers = {
            identifiers.get(identifier, identifier) for identifier in device.identifiers
        }
        if new_identifiers != device.identifiers:
            registry.async_update_device(device.id, new_identifiers=new_identifiers)
