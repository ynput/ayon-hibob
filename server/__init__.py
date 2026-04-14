from typing import Type

from ayon_server.addons import BaseServerAddon

from .settings import HiBobSettings, DEFAULT_VALUES


class HiBobAddon(BaseServerAddon):
    settings_model: Type[HiBobSettings] = HiBobSettings

    async def get_default_settings(self):
        settings_model_cls = self.get_settings_model()
        return settings_model_cls(**DEFAULT_VALUES)

    def get_custom_ftrack_handlers_endpoint(self) -> str:
        return (
            f"addons/{self.name}/{self.version}/private/"
            f"hibob_event_handlers.tar.gz"
        )
