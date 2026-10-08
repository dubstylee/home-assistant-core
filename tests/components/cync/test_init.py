"""Tests for the Cync integration setup."""

from unittest.mock import DEFAULT, MagicMock, patch

from pycync import CyncPlug
from pycync.devices.device_types import DeviceType
from pycync.exceptions import CyncError
import pytest

from homeassistant.components.cync.const import DOMAIN
from homeassistant.components.cync.coordinator import CyncCoordinator
from homeassistant.components.cync.entity import CyncBaseEntity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from tests.common import MockConfigEntry


@pytest.mark.parametrize(
    ("unique_id", "mesh_id"),
    [
        pytest.param("1000-1101", "1000-1", id="online-light"),
        pytest.param("1000-1112", "1000-3", id="offline-light"),
    ],
)
async def test_preserve_registry_identifiers(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
    unique_id: str,
    mesh_id: str,
) -> None:
    """Test existing light and device customizations survive setup and reload."""
    mock_config_entry.add_to_hass(hass)
    area = area_registry.async_get_or_create("Porch")
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, unique_id)},
    )
    device_registry.async_update_device(
        device.id,
        area_id=area.id,
        labels={"outside"},
        name_by_user="Porch light",
    )
    entity = entity_registry.async_get_or_create(
        Platform.LIGHT,
        DOMAIN,
        unique_id,
        config_entry=mock_config_entry,
        device_id=device.id,
    )
    entity_registry.async_update_entity(
        entity.entity_id,
        new_entity_id="light.porch",
        name="Porch lamp",
        icon="mdi:lamp",
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    current_device = device_registry.async_get(device.id)
    assert current_device is not None
    assert current_device.identifiers == {(DOMAIN, f"mesh:{mesh_id}")}
    assert current_device.area_id == area.id
    assert current_device.labels == {"outside"}
    assert current_device.name_by_user == "Porch light"
    entity_id = entity_registry.async_get_entity_id(Platform.LIGHT, DOMAIN, unique_id)
    assert entity_id is not None
    current_entity = entity_registry.async_get(entity_id)
    assert current_entity is not None
    assert current_entity.device_id == device.id
    assert current_entity.previous_unique_id is None
    assert entity_id == "light.porch"
    assert current_entity.name == "Porch lamp"
    assert current_entity.icon == "mdi:lamp"
    assert (
        len(
            er.async_entries_for_config_entry(
                entity_registry, mock_config_entry.entry_id
            )
        )
        == 3
    )
    assert (
        len(
            dr.async_entries_for_config_entry(
                device_registry, mock_config_entry.entry_id
            )
        )
        == 3
    )


async def test_preserve_device_without_entity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """Test an offline light reuses its device after its entity was removed."""
    mock_config_entry.add_to_hass(hass)
    area = area_registry.async_get_or_create("Porch")
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1112")},
    )
    device_registry.async_update_device(
        device.id, area_id=area.id, name_by_user="Porch light", labels={"outside"}
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = entity_registry.async_get_entity_id(Platform.LIGHT, DOMAIN, "1000-1112")
    assert entity_id is not None
    entity = entity_registry.async_get(entity_id)
    assert entity is not None
    assert entity.device_id == device.id
    current_device = device_registry.async_get(device.id)
    assert current_device is not None
    assert current_device.identifiers == {(DOMAIN, "mesh:1000-3")}
    assert current_device.area_id == area.id
    assert current_device.name_by_user == "Porch light"
    assert current_device.labels == {"outside"}
    assert (
        len(
            dr.async_entries_for_config_entry(
                device_registry, mock_config_entry.entry_id
            )
        )
        == 3
    )


async def test_outlet_registry_identifiers(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    cync_client: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test the base entity registers outlets sharing a cloud ID independently."""
    mock_config_entry.add_to_hass(hass)
    coordinator = CyncCoordinator(hass, mock_config_entry, cync_client)
    outlets = [
        CyncPlug(
            is_online=True,
            wifi_connected=True,
            device_id=1234,
            mesh_device_id=mesh_id,
            home_id=10000,
            name="Outdoor outlet",
            device_type_id=67,
            device_type=DeviceType.PLUG,
            mac_address="ABCDEF123456",
            product_id="product123",
            authorize_code="abcd_code",
        )
        for mesh_id in (1006, 2006)
    ]
    cync_client.get_devices.return_value = outlets
    await coordinator.async_refresh()

    for outlet in outlets:
        entity = CyncBaseEntity(outlet, coordinator)
        device = device_registry.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id, **entity.device_info
        )
        entity_registry.async_get_or_create(
            Platform.SWITCH,
            DOMAIN,
            entity.unique_id,
            config_entry=mock_config_entry,
            device_id=device.id,
        )

    entries = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert {entry.unique_id for entry in entries} == {"10000-1006", "10000-2006"}
    assert len({entry.device_id for entry in entries}) == 2
    assert set(coordinator.data) == {"10000-1006", "10000-2006"}


async def test_retry_device_identifier_migration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    cync_client: MagicMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test partial migration retries preserve devices and other identifiers."""
    mock_config_entry.add_to_hass(hass)
    first = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1101"), ("other", "light")},
    )
    second = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1112")},
    )
    with patch.object(
        device_registry,
        "async_update_device",
        wraps=device_registry.async_update_device,
        side_effect=[DEFAULT, RuntimeError("Interrupted migration")],
    ):
        assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert device_registry.async_get(first.id).identifiers == {
        (DOMAIN, "mesh:1000-1"),
        ("other", "light"),
    }
    assert device_registry.async_get(second.id).identifiers == {(DOMAIN, "1000-1112")}
    cync_client.shut_down.assert_awaited_once()

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert device_registry.async_get(first.id).identifiers == {
        (DOMAIN, "mesh:1000-1"),
        ("other", "light"),
    }
    assert device_registry.async_get(second.id).identifiers == {(DOMAIN, "mesh:1000-3")}
    assert (
        len(
            dr.async_entries_for_config_entry(
                device_registry, mock_config_entry.entry_id
            )
        )
        == 3
    )


async def test_device_identifier_namespace(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    cync_client: MagicMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test a mesh ID matching another light's cloud ID cannot merge devices."""
    mock_config_entry.add_to_hass(hass)
    lights = cync_client.get_homes.return_value[0].get_flattened_device_list()
    lights[1].mesh_device_id = 1101
    first = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1101")},
    )
    second = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1111")},
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert device_registry.async_get(first.id).identifiers == {(DOMAIN, "mesh:1000-1")}
    assert device_registry.async_get(second.id).identifiers == {
        (DOMAIN, "mesh:1000-1101")
    }
    assert (
        len(
            dr.async_entries_for_config_entry(
                device_registry, mock_config_entry.entry_id
            )
        )
        == 3
    )


async def test_connection_failure_before_device_migration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    cync_client: MagicMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test connection failures leave legacy identifiers intact until setup retries."""
    mock_config_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1101")},
    )
    cync_client.create.side_effect = CyncError

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert device_registry.async_get(device.id).identifiers == {(DOMAIN, "1000-1101")}

    cync_client.create.side_effect = None
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert device_registry.async_get(device.id).identifiers == {(DOMAIN, "mesh:1000-1")}


async def test_device_missing_until_later_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    cync_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a device absent from the API migrates when it returns on reload."""
    mock_config_entry.add_to_hass(hass)
    home = cync_client.get_homes.return_value[0]
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "1000-1101")},
    )
    cync_client.get_homes.return_value = []
    cync_client.get_devices.return_value = []

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert device_registry.async_get(device.id).identifiers == {(DOMAIN, "1000-1101")}

    cync_client.get_homes.return_value = [home]
    cync_client.get_devices.return_value = home.get_flattened_device_list()
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert device_registry.async_get(device.id).identifiers == {(DOMAIN, "mesh:1000-1")}
    entity_id = entity_registry.async_get_entity_id(Platform.LIGHT, DOMAIN, "1000-1101")
    assert entity_id is not None
    assert entity_registry.async_get(entity_id).device_id == device.id
    assert (
        len(
            dr.async_entries_for_config_entry(
                device_registry, mock_config_entry.entry_id
            )
        )
        == 3
    )
