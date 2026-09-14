"""Config and options flow for Generic Video Proxy."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import (
    AUTH_TYPES,
    CONF_AUTH_TYPE,
    CONF_FRAME_BUFFER,
    CONF_HEADERS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_POSTER_URL,
    CONF_RECONNECT_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SNAPSHOT_URL,
    CONF_SOURCE_TYPE,
    CONF_STALL_TIMEOUT,
    CONF_UPSTREAM_TIMEOUT,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_AUTH_TYPE,
    DEFAULT_FRAME_BUFFER,
    DEFAULT_RECONNECT_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOURCE_TYPE,
    DEFAULT_STALL_TIMEOUT,
    DEFAULT_UPSTREAM_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_FRAME_BUFFER,
    MAX_RECONNECT_INTERVAL,
    MAX_SCAN_INTERVAL,
    MAX_UPSTREAM_TIMEOUT,
    MIN_FRAME_BUFFER,
    MIN_RECONNECT_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_UPSTREAM_TIMEOUT,
    SOURCE_TYPE_AUTO,
    SOURCE_TYPE_RTSP,
    SOURCE_TYPES,
)
from .models import ConfigError, parse_config
from .upstream import (
    UpstreamClient,
    UpstreamError,
    async_close_session,
    async_detect_source_type,
    create_session,
)

_LOGGER = logging.getLogger(__name__)

#: Transient field: probe the upstream before saving the entry.
CONF_VALIDATE = "validate"


def _url_selector() -> selector.TextSelector:
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))


def _number_selector(minimum: int, maximum: int, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _choice_selector(options: tuple[str, ...]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _format_headers(headers: Any) -> str:
    """Render stored headers as an editable ``Key: Value`` block."""
    if not isinstance(headers, Mapping):
        return ""
    return "\n".join(f"{key}: {value}" for key, value in headers.items())


def build_schema(current: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the schema shared by the user and options flows."""
    values: Mapping[str, Any] = current or {}
    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=values.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Required(CONF_URL, default=values.get(CONF_URL, "")): _url_selector(),
        vol.Required(
            CONF_SOURCE_TYPE, default=values.get(CONF_SOURCE_TYPE, DEFAULT_SOURCE_TYPE)
        ): _choice_selector(SOURCE_TYPES),
        vol.Required(
            CONF_AUTH_TYPE, default=values.get(CONF_AUTH_TYPE, DEFAULT_AUTH_TYPE)
        ): _choice_selector(AUTH_TYPES),
        vol.Optional(
            CONF_USERNAME, default=values.get(CONF_USERNAME) or ""
        ): selector.TextSelector(),
        vol.Optional(CONF_PASSWORD, default=""): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(
            CONF_VERIFY_SSL, default=values.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_SNAPSHOT_URL, default=values.get(CONF_SNAPSHOT_URL) or ""
        ): _url_selector(),
        vol.Optional(CONF_POSTER_URL, default=values.get(CONF_POSTER_URL) or ""): _url_selector(),
        vol.Optional(
            CONF_HEADERS, default=_format_headers(values.get(CONF_HEADERS))
        ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        vol.Optional(
            CONF_SCAN_INTERVAL, default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ): _number_selector(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, "s"),
        vol.Optional(
            CONF_RECONNECT_INTERVAL,
            default=values.get(CONF_RECONNECT_INTERVAL, DEFAULT_RECONNECT_INTERVAL),
        ): _number_selector(MIN_RECONNECT_INTERVAL, MAX_RECONNECT_INTERVAL, "s"),
        vol.Optional(
            CONF_UPSTREAM_TIMEOUT,
            default=values.get(CONF_UPSTREAM_TIMEOUT, DEFAULT_UPSTREAM_TIMEOUT),
        ): _number_selector(MIN_UPSTREAM_TIMEOUT, MAX_UPSTREAM_TIMEOUT, "s"),
        vol.Optional(
            CONF_STALL_TIMEOUT,
            default=values.get(CONF_STALL_TIMEOUT, DEFAULT_STALL_TIMEOUT),
        ): _number_selector(MIN_UPSTREAM_TIMEOUT, MAX_UPSTREAM_TIMEOUT, "s"),
        vol.Optional(
            CONF_FRAME_BUFFER, default=values.get(CONF_FRAME_BUFFER, DEFAULT_FRAME_BUFFER)
        ): _number_selector(MIN_FRAME_BUFFER, MAX_FRAME_BUFFER, "frames"),
        vol.Optional(CONF_VALIDATE, default=True): selector.BooleanSelector(),
    }
    return vol.Schema(schema)


async def async_probe_source(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """Probe the configured URL and return the detected source type.

    Raises :class:`UpstreamError` when the upstream cannot be reached or its
    payload cannot be identified.
    """
    config = parse_config(data)
    session = create_session(config.verify_ssl)
    try:
        client = UpstreamClient(config, session)
        detected, content_type = await async_detect_source_type(client)
    finally:
        await async_close_session(session)
    _LOGGER.debug(
        "%s: probe detected %s (%s)", config.name, detected, content_type or "no content type"
    )
    return detected


class GenericVideoProxyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a proxied stream."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure a new proxied stream."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            should_validate = bool(data.pop(CONF_VALIDATE, True))
            try:
                config = parse_config(data)
            except ConfigError as err:
                _LOGGER.debug("invalid proxy configuration: %s", err)
                errors["base"] = "invalid_config"
            else:
                await self.async_set_unique_id(f"{config.name}:{config.url}")
                self._abort_if_unique_id_configured()
                if should_validate and config.source_type != SOURCE_TYPE_RTSP:
                    try:
                        detected = await async_probe_source(self.hass, data)
                    except UpstreamError as err:
                        _LOGGER.warning("could not read %s: %s", config.name, err)
                        errors["base"] = "cannot_connect"
                    else:
                        if detected and config.source_type == SOURCE_TYPE_AUTO:
                            data[CONF_SOURCE_TYPE] = detected
                if not errors:
                    return self.async_create_entry(title=config.name, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=build_schema(user_input),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow of this integration."""
        return GenericVideoProxyOptionsFlow()


class GenericVideoProxyOptionsFlow(OptionsFlow):
    """Edit an existing proxied stream."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and process the options form."""
        entry = self.config_entry
        current: dict[str, Any] = {**entry.data, **entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            should_validate = bool(data.pop(CONF_VALIDATE, True))
            if not data.get(CONF_PASSWORD):
                # An empty password field means "keep the stored secret".
                data[CONF_PASSWORD] = current.get(CONF_PASSWORD)
            try:
                config = parse_config(data)
            except ConfigError as err:
                _LOGGER.debug("invalid proxy configuration: %s", err)
                errors["base"] = "invalid_config"
            else:
                if should_validate and config.source_type != SOURCE_TYPE_RTSP:
                    try:
                        detected = await async_probe_source(self.hass, data)
                    except UpstreamError as err:
                        _LOGGER.warning("could not read %s: %s", config.name, err)
                        errors["base"] = "cannot_connect"
                    else:
                        if detected and config.source_type == SOURCE_TYPE_AUTO:
                            data[CONF_SOURCE_TYPE] = detected
                if not errors:
                    return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=build_schema(current),
            errors=errors,
        )
