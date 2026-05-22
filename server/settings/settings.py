from ayon_server.settings import (
    BaseSettingsModel,
    SettingsField,
)
from ayon_server.settings.enum import secrets_enum


class ServiceCredentialsModel(BaseSettingsModel):
    hibob_user: str = SettingsField(
        "",
        enum_resolver=secrets_enum,
        title="HiBob Service User"
    )
    hibob_api: str = SettingsField(
        "",
        enum_resolver=secrets_enum,
        title="HiBob Service API key"
    )


class SyncHiBobHolidaysActionModel(BaseSettingsModel):
    enabled: bool = SettingsField(True, title="Enabled")
    role_list: list[str] = SettingsField(
        default_factory=list,
        title="Roles",
    )


class SyncConfigModel(BaseSettingsModel):
    ignored_policy_types: list[str] = SettingsField(
        default_factory=list,
        title="Ignored Policy Types",
    )



class FtrackEventHandlersModel(BaseSettingsModel):
    SyncHiBobHolidaysAction: SyncHiBobHolidaysActionModel = SettingsField(
        title="Sync HiBob Holidays",
        default_factory=SyncHiBobHolidaysActionModel,
    )


class HiBobSettings(BaseSettingsModel):
    """HiBob addon settings."""
    service_credentials: ServiceCredentialsModel = SettingsField(
        default_factory=ServiceCredentialsModel,
        title="Service Credentials",
        scope=["studio"],
    )
    sync_config: SyncConfigModel = SettingsField(
        default_factory=SyncConfigModel,
        title="Sync Configuration",
    )
    ftrack_event_handlers: FtrackEventHandlersModel = SettingsField(
        title="Ftrack actions",
        default_factory=FtrackEventHandlersModel,
    )


DEFAULT_VALUES = {
    "sync_config": {
        "ignored_policy_types": [],
    },
    "ftrack_event_handlers": {
        "SyncHiBobHolidaysAction": {
            "enabled": True,
            "role_list": [
                "Administrator",
                "Project Manager"
            ],
        },
    },
}
