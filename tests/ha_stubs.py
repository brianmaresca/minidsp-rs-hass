"""Minimal Home Assistant interfaces for isolated integration tests.

These stubs exercise the integration's behavior, not Home Assistant's loader or
entity registry. They are installed only in the test process.
"""
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def module(name, **members):
    value = ModuleType(name)
    value.__dict__.update(members)
    sys.modules[name] = value
    return value


def callback(func):
    return func


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    @staticmethod
    def async_get_options_flow(entry):
        raise NotImplementedError


class OptionsFlow:
    def async_create_entry(self, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(self, step_id, data_schema):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema}


class DataUpdateCoordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name, update_interval):
        self.name = name
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self.listeners = []

    def _notify(self):
        for listener in list(self.listeners):
            listener()

    def async_set_updated_data(self, data):
        self.data = data
        self.last_update_success = True
        self.last_exception = None
        self._notify()

    def async_set_update_error(self, error):
        self.last_update_success = False
        self.last_exception = error
        self._notify()

    async def async_request_refresh(self):
        try:
            data = await self._async_update_data()
        except Exception as error:
            self.async_set_update_error(error)
        else:
            self.async_set_updated_data(data)


class CoordinatorEntity:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.state_writes = 0

    def async_write_ha_state(self):
        self.state_writes += 1

    def _handle_coordinator_update(self):
        self.async_write_ha_state()


def install():
    module("homeassistant")
    module("homeassistant.config_entries", ConfigFlow=ConfigFlow,
           ConfigFlowResult=dict, ConfigEntry=SimpleNamespace, OptionsFlow=OptionsFlow)
    module("homeassistant.const", CONF_URL="url", CONF_NAME="name")
    module("homeassistant.core", HomeAssistant=SimpleNamespace, callback=callback)
    module("homeassistant.helpers")
    module("homeassistant.helpers.update_coordinator",
           DataUpdateCoordinator=DataUpdateCoordinator,
           CoordinatorEntity=CoordinatorEntity, UpdateFailed=RuntimeError)
    module("homeassistant.components")
    module("homeassistant.components.number", NumberEntity=type("NumberEntity", (), {}))
    # Import the real integration modules without executing entry setup, which
    # requires a live Home Assistant runtime.
    component = module("custom_components.minidsp")
    component.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/minidsp")]
