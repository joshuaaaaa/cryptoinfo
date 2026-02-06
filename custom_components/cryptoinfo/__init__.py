from .config_flow import CryptoInfoData
from .const.const import _LOGGER, DOMAIN

from homeassistant.const import Platform

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass, entry) -> bool:
    """Set up the CryptoInfo platform."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = CryptoInfoData(hass)
        await hass.data[DOMAIN].async_initialize()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("__init__ set up")
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""
    # Save data before unloading
    if DOMAIN in hass.data:
        await hass.data[DOMAIN].store.async_save()

    # Unload the sensor platform
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])

    return unload_ok


async def async_migrate_entry(hass, config_entry) -> bool:
    """Migrate old config entry to new version."""
    _LOGGER.debug(
        "Migrating config entry from version %s",
        config_entry.version,
    )

    if config_entry.version == 1:
        # Version 1 -> 2: just update version, keep all data as-is
        hass.config_entries.async_update_entry(
            config_entry,
            version=2,
        )
        _LOGGER.info("Migration to version 2 successful")

    return True
