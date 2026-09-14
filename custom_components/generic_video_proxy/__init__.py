"""Generic Video Proxy - proxy HTTP(S) video and image streams through Home Assistant.

The integration fetches an upstream stream *through Home Assistant* and re-serves
it from Home Assistant's own HTTP server. Because the browser always talks to the
Home Assistant origin, a stream plays identically on plain HTTP, behind an HTTPS
reverse proxy, and from outside the home - no mixed content, no CORS, no exposed
camera credentials.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DATA_VIEWS_REGISTERED, PLATFORMS
from .frontend import async_register_card
from .hub import StreamHub
from .models import ConfigError, ProxyConfig, parse_config
from .runtime import ProxyRuntime, remove_runtime, set_runtime
from .security import TokenManager
from .upstream import UpstreamClient, create_session
from .views import async_register_views
from .websocket import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

type GenericVideoProxyConfigEntry = ConfigEntry[ProxyRuntime]


def config_from_entry(entry: ConfigEntry) -> ProxyConfig:
    """Build the validated configuration of a config entry."""
    return parse_config({**entry.data, **entry.options})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one proxied stream."""
    if hass.http is None:
        raise ConfigEntryNotReady("the http integration is not set up")

    try:
        config = config_from_entry(entry)
    except ConfigError as err:
        _LOGGER.error("invalid configuration for %s: %s", entry.title, err)
        return False

    session = create_session(config.verify_ssl)
    client = UpstreamClient(config, session)
    runtime = ProxyRuntime(
        hass=hass,
        entry=entry,
        config=config,
        client=client,
        hub=StreamHub(hass, entry.entry_id, config, client),
        tokens=await TokenManager.async_create(hass),
        session=session,
    )

    set_runtime(hass, runtime)
    entry.runtime_data = runtime

    if not hass.data.get(DATA_VIEWS_REGISTERED):
        async_register_views(hass)
        async_register_websocket_api(hass)
        hass.data[DATA_VIEWS_REGISTERED] = True
    await async_register_card(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _LOGGER.debug("%s: proxy for %s is ready", config.name, client.safe_url)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one proxied stream."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    runtime: ProxyRuntime = entry.runtime_data
    await runtime.async_stop()
    remove_runtime(hass, entry.entry_id)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
