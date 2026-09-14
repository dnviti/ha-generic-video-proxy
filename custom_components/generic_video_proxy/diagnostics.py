"""Diagnostics support for Generic Video Proxy.

Everything that could carry a credential is redacted: upstream URLs often embed
API keys or camera passwords, and custom headers routinely carry tokens.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HEADERS,
    CONF_PASSWORD,
    CONF_POSTER_URL,
    CONF_SNAPSHOT_URL,
    CONF_URL,
    CONF_USERNAME,
)
from .runtime import ProxyRuntime

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_HEADERS,
    CONF_URL,
    CONF_SNAPSHOT_URL,
    CONF_POSTER_URL,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for one proxy."""
    runtime: ProxyRuntime = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "config": async_redact_data(runtime.config.as_dict(), TO_REDACT),
        "upstream": runtime.client.safe_url,
        "stream": asdict(runtime.hub.stats),
    }
