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
