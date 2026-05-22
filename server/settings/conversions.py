from typing import Any


def _convert_ignored_policy_types_1_1_0(overrides):
    """Moved ignored_policy_types from event handler to sync_config."""

    try:
        ftrack_action = (
            overrides["ftrack_event_handlers"]["SyncHiBobHolidaysAction"]
        )
    except (KeyError, TypeError):
        return

    if "ignored_policy_types" not in ftrack_action:
        return

    ignored_policy_types = ftrack_action.pop("ignored_policy_types")
    overrides["sync_config"]["ignored_policy_types"] = ignored_policy_types


async def convert_settings_overrides(
    source_version: str,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    _convert_ignored_policy_types_1_1_0(overrides)
    return overrides
