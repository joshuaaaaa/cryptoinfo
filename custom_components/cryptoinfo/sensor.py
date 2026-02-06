#!/usr/bin/env python3
"""
Sensor component for Cryptoinfo
Author: Johnny Visser
"""

import asyncio
import urllib.error
from datetime import datetime, timedelta

from homeassistant import config_entries
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const.const import (
    _LOGGER,
    API_ENDPOINT,
    ATTR_1H_CHANGE,
    ATTR_7D_CHANGE,
    ATTR_14D_CHANGE,
    ATTR_24H_CHANGE,
    ATTR_24H_VOLUME,
    ATTR_30D_CHANGE,
    ATTR_1Y_CHANGE,
    ATTR_BASE_PRICE,
    ATTR_CIRCULATING_SUPPLY,
    ATTR_LAST_UPDATE,
    ATTR_CRYPTOCURRENCY_ID,
    ATTR_CRYPTOCURRENCY_NAME,
    ATTR_CRYPTOCURRENCY_SYMBOL,
    ATTR_CURRENCY_NAME,
    ATTR_MARKET_CAP,
    ATTR_MULTIPLIER,
    ATTR_TOTAL_SUPPLY,
    ATTR_ATH,
    ATTR_ATH_DATE,
    ATTR_ATH_CHANGE,
    ATTR_RANK,
    ATTR_IMAGE,
    CONF_CRYPTOCURRENCY_IDS,
    CONF_CURRENCY_NAME,
    CONF_ID,
    CONF_MULTIPLIERS,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_UPDATE_FREQUENCY,
    DOMAIN,
    SENSOR_PREFIX,
)

# Maximum number of API requests per minute (CoinGecko free tier limit)
MAX_REQUESTS_PER_MINUTE = 15


class CryptoApiRateLimiter:
    """Global rate limiter for CoinGecko API requests.

    Ensures no more than MAX_REQUESTS_PER_MINUTE requests are made globally,
    and staggers coordinator updates to avoid bursts.
    """

    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._request_timestamps: list[datetime] = []
        self._coordinators: list["CryptoDataCoordinator"] = []

    @classmethod
    def get_instance(cls) -> "CryptoApiRateLimiter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_coordinator(self, coordinator: "CryptoDataCoordinator") -> int:
        """Register a coordinator and return its stagger index."""
        self._coordinators.append(coordinator)
        return len(self._coordinators) - 1

    def unregister_coordinator(self, coordinator: "CryptoDataCoordinator") -> None:
        """Unregister a coordinator."""
        if coordinator in self._coordinators:
            self._coordinators.remove(coordinator)

    def _cleanup_old_timestamps(self):
        """Remove timestamps older than 1 minute."""
        cutoff = datetime.now() - timedelta(minutes=1)
        self._request_timestamps = [
            ts for ts in self._request_timestamps if ts > cutoff
        ]

    async def acquire(self):
        """Wait until we can make a request within rate limits."""
        async with self._lock:
            self._cleanup_old_timestamps()

            if len(self._request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
                # Calculate how long to wait
                oldest = self._request_timestamps[0]
                wait_seconds = (
                    oldest + timedelta(minutes=1) - datetime.now()
                ).total_seconds()
                if wait_seconds > 0:
                    _LOGGER.debug(
                        "Rate limit reached, waiting %.1f seconds", wait_seconds
                    )
                    await asyncio.sleep(wait_seconds)
                    self._cleanup_old_timestamps()

            self._request_timestamps.append(datetime.now())

    def get_stagger_delay(self, coordinator: "CryptoDataCoordinator") -> float:
        """Calculate stagger delay in seconds for a coordinator.

        Distributes coordinators across the update interval so they don't
        all fire at the same time. Each coordinator gets spaced by at least
        5 seconds to avoid API bursts.
        """
        if coordinator not in self._coordinators:
            return 0
        index = self._coordinators.index(coordinator)
        # Stagger by 5 seconds per coordinator
        return index * 5.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.debug("Setup Cryptoinfo sensor")

    config = config_entry.data

    id_name = (config.get(CONF_ID) or "").strip()
    cryptocurrency_ids = config.get(CONF_CRYPTOCURRENCY_IDS).lower().strip()
    currency_name = config.get(CONF_CURRENCY_NAME).strip()
    unit_of_measurement = (config.get(CONF_UNIT_OF_MEASUREMENT) or "").strip()
    multipliers = config.get(CONF_MULTIPLIERS).strip()
    update_frequency = timedelta(minutes=(float(config.get(CONF_UPDATE_FREQUENCY))))

    # Get rate limiter instance
    rate_limiter = CryptoApiRateLimiter.get_instance()

    # Create coordinator for centralized data fetching
    coordinator = CryptoDataCoordinator(
        hass,
        cryptocurrency_ids,
        currency_name,
        update_frequency,
        id_name,
        rate_limiter,
    )

    # Store coordinator reference for cleanup
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("coordinators", [])
    hass.data[DOMAIN]["coordinators"].append(coordinator)

    # Apply stagger delay before first refresh to avoid all coordinators
    # fetching at the same instant
    stagger_delay = rate_limiter.get_stagger_delay(coordinator)
    if stagger_delay > 0:
        _LOGGER.debug(
            "Staggering coordinator %s by %.1f seconds", id_name, stagger_delay
        )
        await asyncio.sleep(stagger_delay)

    # Wait for coordinator to do first update
    await coordinator.async_config_entry_first_refresh()

    entities = []
    crypto_list = [crypto.strip() for crypto in cryptocurrency_ids.split(",")]
    multipliers_list = [multiplier.strip() for multiplier in multipliers.split(",")]

    multipliers_length = len(multipliers_list)
    crypto_list_length = len(crypto_list)

    if multipliers_length != crypto_list_length:
        _LOGGER.error(
            f"Length mismatch: multipliers ({multipliers_length}) and cryptocurrency id's ({crypto_list_length}) must have the same length"
        )
        return False

    for i, cryptocurrency_id in enumerate(crypto_list):
        try:
            entities.append(
                CryptoinfoSensor(
                    coordinator,
                    cryptocurrency_id,
                    currency_name,
                    unit_of_measurement,
                    multipliers_list[i],
                    id_name,
                )
            )
        except urllib.error.HTTPError as error:
            _LOGGER.error(error.reason)
            return False

    async_add_entities(entities)


class CryptoDataCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass: HomeAssistant,
        cryptocurrency_ids: str,
        currency_name: str,
        update_frequency: timedelta,
        id_name: str,
        rate_limiter: CryptoApiRateLimiter,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=f"Crypto Data {id_name}",
            update_interval=update_frequency,
        )
        self.cryptocurrency_ids = cryptocurrency_ids
        self.currency_name = currency_name
        self.id_name = id_name
        self.rate_limiter = rate_limiter
        self._stagger_index = rate_limiter.register_coordinator(self)

        _LOGGER.debug(
            "Coordinator %s registered with stagger index %d",
            id_name,
            self._stagger_index,
        )

    async def _async_update_data(self):
        """Fetch data from API endpoint with rate limiting."""
        # Acquire rate limiter slot (waits if rate limit would be exceeded)
        await self.rate_limiter.acquire()

        _LOGGER.debug(
            "Fetch data from API endpoint, sensor: %s cryptocurrency_ids: %s",
            self.id_name,
            self.cryptocurrency_ids,
        )

        url = (
            f"{API_ENDPOINT}coins/markets"
            f"?ids={self.cryptocurrency_ids}"
            f"&vs_currency={self.currency_name}"
            f"&price_change_percentage=1h%2C24h%2C7d%2C14d%2C30d%2C1y"
        )

        try:
            session = aiohttp_client.async_get_clientsession(self.hass)
            async with session.get(url) as response:
                if response.status == 429:
                    retry_after = int(
                        response.headers.get("Retry-After", "60")
                    )
                    _LOGGER.warning(
                        "CoinGecko rate limit hit (429), retrying after %d seconds",
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    # Retry once after waiting
                    async with session.get(url) as retry_response:
                        retry_response.raise_for_status()
                        data = await retry_response.json()
                        return {coin["id"]: coin for coin in data}

                response.raise_for_status()
                data = await response.json()
                return {coin["id"]: coin for coin in data}
        except Exception as err:
            _LOGGER.error(f"Error fetching data: {err}")
            return self.data if self.data else None

    async def async_will_remove_from_hass(self) -> None:
        """Handle removal from Home Assistant."""
        self.rate_limiter.unregister_coordinator(self)
        _LOGGER.debug("Coordinator %s unregistered from rate limiter", self.id_name)


class CryptoinfoSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:bitcoin"

    def __init__(
        self,
        coordinator: CryptoDataCoordinator,
        cryptocurrency_id: str,
        currency_name: str,
        unit_of_measurement: str,
        multiplier: str,
        id_name: str,
    ):
        super().__init__(coordinator)
        self.cryptocurrency_id = cryptocurrency_id
        self.currency_name = currency_name
        self.multiplier = multiplier
        self._attr_native_unit_of_measurement = unit_of_measurement
        self._attr_unique_id = (
            SENSOR_PREFIX
            + (id_name + " " if len(id_name) > 0 else "")
            + cryptocurrency_id
            + currency_name
        )
        self.entity_id = "sensor." + (
            (SENSOR_PREFIX + (id_name + " " if len(id_name) > 0 else ""))
            .lower()
            .replace(" ", "_")
            + cryptocurrency_id
            + "_"
            + currency_name
        )

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.coordinator.data and self.cryptocurrency_id in self.coordinator.data:
            return float(
                self.coordinator.data[self.cryptocurrency_id]["current_price"]
            ) * float(self.multiplier)
        return None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if (
            not self.coordinator.data
            or self.cryptocurrency_id not in self.coordinator.data
        ):
            return {
                ATTR_LAST_UPDATE: datetime.today().strftime("%d-%m-%Y %H:%M"),
                ATTR_CRYPTOCURRENCY_NAME: None,
                ATTR_CURRENCY_NAME: None,
                ATTR_BASE_PRICE: None,
                ATTR_MULTIPLIER: None,
                ATTR_24H_VOLUME: None,
                ATTR_1H_CHANGE: None,
                ATTR_24H_CHANGE: None,
                ATTR_7D_CHANGE: None,
                ATTR_14D_CHANGE: None,
                ATTR_30D_CHANGE: None,
                ATTR_1Y_CHANGE: None,
                ATTR_MARKET_CAP: None,
                ATTR_CIRCULATING_SUPPLY: None,
                ATTR_TOTAL_SUPPLY: None,
                ATTR_ATH: None,
                ATTR_ATH_DATE: None,
                ATTR_ATH_CHANGE: None,
                ATTR_RANK: None,
                ATTR_IMAGE: None,
            }

        data = self.coordinator.data[self.cryptocurrency_id]
        return {
            ATTR_LAST_UPDATE: datetime.today().strftime("%d-%m-%Y %H:%M"),
            ATTR_CRYPTOCURRENCY_ID: self.cryptocurrency_id,
            ATTR_CRYPTOCURRENCY_NAME: data["name"],
            ATTR_CRYPTOCURRENCY_SYMBOL: data["symbol"],
            ATTR_CURRENCY_NAME: self.currency_name,
            ATTR_BASE_PRICE: data["current_price"],
            ATTR_MULTIPLIER: self.multiplier,
            ATTR_24H_VOLUME: data["total_volume"],
            ATTR_1H_CHANGE: data["price_change_percentage_1h_in_currency"],
            ATTR_24H_CHANGE: data["price_change_percentage_24h_in_currency"],
            ATTR_7D_CHANGE: data["price_change_percentage_7d_in_currency"],
            ATTR_14D_CHANGE: data["price_change_percentage_14d_in_currency"],
            ATTR_30D_CHANGE: data["price_change_percentage_30d_in_currency"],
            ATTR_1Y_CHANGE: data["price_change_percentage_1y_in_currency"],
            ATTR_MARKET_CAP: data["market_cap"],
            ATTR_CIRCULATING_SUPPLY: data["circulating_supply"],
            ATTR_TOTAL_SUPPLY: data["total_supply"],
            ATTR_ATH: data.get("ath"),
            ATTR_ATH_DATE: data.get("ath_date"),
            ATTR_ATH_CHANGE: data.get("ath_change_percentage"),
            ATTR_RANK: data.get("market_cap_rank"),
            ATTR_IMAGE: data["image"],
        }

    async def async_will_remove_from_hass(self) -> None:
        """Handle removal from Home Assistant."""
        await self.coordinator.async_will_remove_from_hass()  # type: ignore
        await super().async_will_remove_from_hass()
