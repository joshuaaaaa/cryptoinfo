from .const.const import _LOGGER, DOMAIN

from homeassistant.const import Platform

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass, entry) -> bool:
    """Set up the CryptoInfo platform."""
    hass.data.setdefault(DOMAIN, {})

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("__init__ set up")
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""
    # Unload the sensor platform
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    return unload_ok


async def async_migrate_entry(hass, config_entry) -> bool:
    """Migrate old config entry to new version."""
    _LOGGER.debug(
        "Migrating config entry from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version == 1:
        # Version 1 -> 2: Remove min_time_between_requests (now automatic)
        new_data = {**config_entry.data}
        new_data.pop("min_time_between_requests", None)

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            version=2,
        )

        _LOGGER.info(
            "Migration to version 2 successful: removed min_time_between_requests"
        )

    return True
