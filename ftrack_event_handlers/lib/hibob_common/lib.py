from ftrack_common.event_handlers import ServerAction


class HiBobServerAction(ServerAction):
    """Overriden ftrack ServerAction loading settings from addon."""

    settings_frack_subkey = "ftrack_event_handlers"
    addon_name = "hibob"

    def get_addon_settings(self, session, event, entities):
        project_name = self.get_project_name_from_event_with_entities(
            session, event, entities
        )
        project_settings = self.get_project_settings_from_event(
            event, project_name
        )
        return project_settings[self.addon_name]

    def get_my_settings(self, session, event, entities):
        ftrack_settings = self.get_addon_settings(session, event, entities)
        return (
            ftrack_settings
            [self.settings_frack_subkey]
            [self.settings_key]
        )

    def valid_roles(self, session, entities, event):
        """Validate user roles by settings.

        Method requires to have set `settings_key` attribute.
        """

        settings = self.get_my_settings(session, event, entities)
        if self.settings_enabled_key:
            if not settings.get(self.settings_enabled_key, True):
                return False

        user_role_list = self.get_user_roles_from_event(session, event)
        if not self.roles_check(settings.get("role_list"), user_role_list):
            return False
        return True
