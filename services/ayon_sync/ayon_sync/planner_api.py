from __future__ import annotations

import ayon_api
from typing import Any, Iterable, Literal

EventType = Literal["capacity", "allocation", "absence", "milestone"]
SearchMode = Literal["simple", "advanced"]
BookingAttributeDataType = Literal[
    "string",
    "integer",
    "float",
    "boolean",
    "datetime",
    "list_of_strings",
    "list_of_integers",
    "list_of_any",
    "list_of_submodels",
    "dict",
]


class PlannerAPI:
    def __init__(self, version: str) -> None:
        self.version = version

    def get_resources(
        self,
        resource_ids: Iterable[str] | None = None,
        resource_filter: dict | None = None,
        search: str | None = None,
        search_mode: SearchMode | None = None,
        page: int = 1,
        page_limit: int = 500,
    ) -> list[dict]:
        body = {
            key: value
            for key, value in (
                ("ids", resource_ids),
                ("filter", resource_filter),
                ("search", search),
                ("searchMode", search_mode),
                ("page", page),
                ("limit", page_limit),
            )
            if value is not None
        }
        response = ayon_api.post(
            self._get_endpoint("booking/resources/find"),
            **body
        )
        response.raise_for_status()
        return response.data

    def get_booking_events(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        event_ids: Iterable[str] | None = None,
        event_types: Iterable[EventType] | None = None,
        scenario_id: str | None = None,
        project_names: Iterable[str] | None = None,
        track_ids: Iterable[str] | None = None,
        resource_ids: Iterable[str] | None = None,
        event_filter: dict | None = None,
        resource_filter: dict | None = None,
        search: str | None = None,
        search_mode: SearchMode | None = None,
        page: int = 1,
        page_limit: int = 500,
    ) -> list[dict]:
        """

            start_time: ISO format string, e.g. "2024-06-01T00:00:00Z"
            end_time: ISO format string, e.g. "2024-06-01T00:00:00Z"

        """
        if project_names is not None:
            project_names = list(project_names)

        if track_ids is not None:
            track_ids = list(track_ids)

        if resource_ids is not None:
            resource_ids = list(resource_ids)

        if event_ids is not None:
            event_ids = list(event_ids)

        if event_types is not None:
            event_types = list(event_types)

        body = {
            key: value
            for key, value in (
                ("startTime", start_time),
                ("endTime", end_time),
                ("ids", event_ids),
                ("eventTypes", event_types),
                ("scenarioId", scenario_id),
                ("projectNames", project_names),
                ("trackIds", track_ids),
                ("resourceIds", resource_ids),
                ("filter", event_filter),
                ("resourceFilter", resource_filter),
                ("search", search),
                ("searchMode", search_mode),
                ("page", page),
                ("limit", page_limit),
            )
            if value is not None
        }
        response = ayon_api.post(
            self._get_endpoint("booking/events/find"),
            **body
        )
        response.raise_for_status()
        return response.data["events"]

    def create_booking_event(
        self,
        start_time: str,
        end_time: str,
        *,
        label: str | None = None,
        event_type: EventType | None = None,
        attrib: dict | None = None,
        net_duration: int | None = None,
        schedule_id: str | None = None,
        scenario_id: str | None = None,
        track_id: str | None = None,
        project_name: str | None = None,
        status: str | None = None,
        resources: list[dict] | None = None,
        resource_types: list[str] | None = None,
        depends_on: list[dict] | None = None,
        dependents: list[dict] | None = None,
        data: dict[str, Any] | None = None,
        redacted: bool | None = None,
        event_id: str | None = None,
    ) -> str:
        if event_id is None:
            event_id = ayon_api.utils.create_entity_id()
        event_data = {
            "startTime": start_time,
            "endTime": end_time,
            "id": event_id,
        }
        for key, value in (
            ("label", label),
            ("eventType", event_type),
            ("attrib", attrib),
            ("netDuration", net_duration),
            ("scheduleId", schedule_id),
            ("scenarioId", scenario_id),
            ("trackId", track_id),
            ("projectName", project_name),
            ("status", status),
            ("resources", resources),
            ("resourceTypes", resource_types),
            ("dependsOn", depends_on),
            ("dependents", dependents),
            ("data", data),
            ("redacted", redacted),
        ):
            if value is not None:
                event_data[key] = value

        response = ayon_api.post(
            self._get_endpoint("booking/events"),
            **event_data
        )
        response.raise_for_status()
        return event_id

    def update_booking_event(
        self,
        event_id: str,
        *,
        label: str | None = None,
        event_type: EventType | None = None,
        attrib: dict | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        net_duration: int | None = None,
        scenario_id: str | None = None,
        track_id: str | None = None,
        project_name: str | None = None,
        status: str | None = None,
        resources: list[dict] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        update_data = {
            key: value
            for key, value in (
                ("label", label),
                ("eventType", event_type),
                ("attrib", attrib),
                ("startTime", start_time),
                ("endTime", end_time),
                ("netDuration", net_duration),
                ("scenarioId", scenario_id),
                ("trackId", track_id),
                ("projectName", project_name),
                ("status", status),
                ("resources", resources),
                ("data", data),
            )
            if value is not None
        }
        if not update_data:
            return

        response = ayon_api.patch(
            self._get_endpoint(f"booking/events/{event_id}"),
            **update_data
        )
        response.raise_for_status()

    def delete_booking_event(self, event_id: str) -> None:
        response = ayon_api.delete(
            self._get_endpoint(f"booking/events/{event_id}"),
        )
        response.raise_for_status()

    def get_booking_attributes(self) -> list[dict]:
        response = ayon_api.get(
            self._get_endpoint("booking/attributes"),
        )
        response.raise_for_status()
        return response.data["attributes"]

    def save_booking_attribute(
        self,
        name: str,
        data_type: BookingAttributeDataType,
        scopes: dict[str, list[str]],
        *,
        position: int | None = None,
        label: str | None = None,
        enum_resolver: dict[str, str] | None = None,
        enum: list[dict] | None = None,
    ) -> None:
        body = {
            "name": name,
            "dataType": data_type,
            "scopes": scopes,
        }
        for key, value in (
            ("position", position),
            ("label", label),
            ("enumResolver", enum_resolver),
            ("enum", enum),
        ):
            if value is not None:
                body[key] = value

        response = ayon_api.post(
            self._get_endpoint("booking/attributes"),
            **body
        )
        response.raise_for_status()

    def _get_endpoint(self, path: str | None = None) -> str:
        if not path:
            return f"addons/planner/{self.version}"
        return f"addons/planner/{self.version}/{path}"
