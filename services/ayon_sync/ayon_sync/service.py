from __future__ import annotations

import atexit
import base64
import collections
import datetime
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any

import arrow
import ayon_api
import requests

from .planner_api import (
    get_resources,
    get_booking_events,
    create_booking_event,
    update_booking_event,
    delete_booking_event,
)

# 10 minutes
SYNC_DELTA = 60 * 10
HIBOB_ID_KEY = "hibob_id"


class MissingCredentialsError(Exception):
    pass


class _GlobalContext:
    stop_event = threading.Event()
    process_cleaned_up = False


class HolidayItem:
    def __init__(
        self,
        start: str | arrow.arrow,
        end: str | arrow.arrow,
        name: str,
        hibob_id: str | None,
        ayon_id: str | None,
    ) -> None:
        if isinstance(start, str):
            start = arrow.get(start)

        if isinstance(end, str):
            end = arrow.get(end)
        self.start = start
        self.end = end
        self.name = name
        self.hibob_id = hibob_id
        self.ayon_id = ayon_id
        self.processed = False

    def set_processed(self):
        self.processed = True

    @classmethod
    def from_hibob(
        cls, start: str, end: str, name: str, hibob_id: int
    ) -> "HolidayItem":
        end = arrow.get(end).shift(days=1, microseconds=-1)
        return cls(start, end, name, hibob_id=str(hibob_id), ayon_id=None)

    @classmethod
    def from_ayon(
        cls,
        start: str,
        end: str,
        name: str,
        ayon_id: str,
        hibob_id: str | None,
    ) -> "HolidayItem":
        return cls(start, end, name, hibob_id=hibob_id, ayon_id=ayon_id)

    def __repr__(self) -> str:
        s = self.start.format("YYYY-MM-DD")
        e = self.end.format("YYYY-MM-DD")
        return f"{self.__class__.__name__} {s}/{e}"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, HolidayItem):
            return False

        return (
            self.start == other.start
            and self.end == other.end
        )

    def intersects(self, other: Any) -> bool:
        if not isinstance(other, HolidayItem):
            return False

        if (
            self.start > other.end
            or self.end < other.start
        ):
            return False
        return True


def get_addon_version() -> str:
    """Find currently defined addon version in AYON server."""
    addon_name: str = ayon_api.get_service_addon_name()
    addon_version: str = ayon_api.get_service_addon_version()
    variant: str = ayon_api.get_default_settings_variant()

    bundles = ayon_api.get_bundles()
    if variant == "production":
        bundle_name = bundles.get("productionBundle")
        if not bundle_name:
            return addon_version
    elif variant == "staging":
        bundle_name = bundles.get("stagingBundle")
        if not bundle_name:
            return addon_version
    else:
        bundle_name = variant

    bundle = next(
        (
            bundle
            for bundle in bundles["bundles"]
            if bundle["name"] == bundle_name
        ),
        None
    )
    if bundle is None:
        return addon_version

    version = bundle["addons"].get(addon_name)
    if version:
        return version
    return addon_version


def get_addon_settings() -> dict[str, Any]:
    addon_name: str = ayon_api.get_service_addon_name()
    variant: str = ayon_api.get_default_settings_variant()
    addon_version: str = get_addon_version()
    return ayon_api.get_addon_settings(
        addon_name,
        addon_version,
        variant=variant,
    )


def get_hibob_credentials(
    addon_settings: dict[str, Any]
) -> tuple[str | None, str | None]:
    service_settings = addon_settings["service_credentials"]
    hibob_user = service_settings["hibob_user"]
    hibob_api = service_settings["hibob_api"]
    if not hibob_user or not hibob_api:
        return None, None
    secrets_by_name = {
        secret["name"]: secret["value"]
        for secret in ayon_api.get_secrets()
    }
    return secrets_by_name.get(hibob_user), secrets_by_name.get(hibob_api)


def get_hibob_holidays(
    hibob_user: str,
    hibob_api: str,
    ignored_policy_types: list[str],
) -> dict[str, list[HolidayItem]]:
    cred_str = f"{hibob_user}:{hibob_api}".encode("ascii")
    base64_auth = base64.b64encode(cred_str).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {base64_auth}"
    }

    hibob_url = "https://api.hibob.com/v1"

    people_response = requests.post(
        f"{hibob_url}/people/search",
        headers=headers,
        json={
            "fields": [
                "root.id",
                "root.email"
            ]
        }
    )
    people_response.raise_for_status()

    people_data = people_response.json()
    empoyees_data = people_data.pop("employees", [])
    employee_email_by_id = {
        employee["id"]: employee["email"].lower()
        for employee in empoyees_data
        if employee["email"]
    }

    now_date = datetime.datetime.now()
    start_date = now_date - datetime.timedelta(days=7)
    end_date = now_date + datetime.timedelta(days=365)
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    url = (
        f"{hibob_url}/timeoff/whosout"
        f"?from={start_date_str}&to={end_date_str}&includeHourly=false"
    )

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = json.loads(response.text)
    outs = data.pop("outs", [])
    holiday_by_employee_email = collections.defaultdict(list)
    for item in outs:
        policy_type = item.get("policyType")
        if policy_type and policy_type.lower() in ignored_policy_types:
            continue
        employee_id = item["employeeId"]
        email = employee_email_by_id[employee_id]
        display_name = item.get("policyTypeDisplayName") or "Holiday"
        holiday_by_employee_email[email].append(
            HolidayItem.from_hibob(
                item["startDate"],
                item["endDate"],
                display_name,
                item["requestId"],
            ),
        )
    return holiday_by_employee_email


def get_ayon_holidays(
    resource_id_by_email: dict[str, str]
) -> dict[str, list[HolidayItem]]:
    email_by_resource_id = {
        resource_id: email
        for email, resource_id in resource_id_by_email.items()
    }
    resource_ids = set(email_by_resource_id)
    if not resource_ids:
        return {}

    holidays_by_email = {
        email: []
        for email in resource_id_by_email
    }
    page = 1
    limit = 1000
    event_filter = {
        "conditions": [
            {
                "key": f"data.{HIBOB_ID_KEY}",
                "operator": "ne",
                "value": "",
            }
        ]
    }
    now_date = datetime.datetime.now()
    start_date = now_date - datetime.timedelta(days=7)
    end_date = now_date + datetime.timedelta(days=365)
    while True:
        events = get_booking_events(
            start_time=start_date.isoformat(),
            end_time=end_date.isoformat(),
            event_types={"absence"},
            resource_ids=resource_ids,
            event_filter=event_filter,
            page=page,
            page_limit=limit,
        )
        page += 1

        if not events:
            break

        for event in events:
            for resource in event["resources"]:
                resource_id = resource["id"]
                email = email_by_resource_id.get(resource_id)
                if not email:
                    continue
                hibob_id = event["data"][HIBOB_ID_KEY]
                holidays_by_email[email].append(
                    HolidayItem.from_ayon(
                        start=event["startTime"],
                        end=event["endTime"],
                        name=event["label"],
                        ayon_id=event["id"],
                        hibob_id=hibob_id,
                    )
                )

        if len(events) < limit:
            break
    return holidays_by_email


def create_new_holiday(
    hibob_holiday: HolidayItem,
    resource_id: str,
) -> HolidayItem:
    event_id = ayon_api.utils.create_entity_id()
    create_booking_event(
        start_time=hibob_holiday.start.isoformat(),
        end_time=hibob_holiday.end.isoformat(),
        label=hibob_holiday.name,
        event_type="absence",
        data={
            HIBOB_ID_KEY: hibob_holiday.hibob_id
        },
        resources=[{"id": resource_id}],
        event_id=event_id,
    )
    ayon_holiday = HolidayItem(
        start=hibob_holiday.start,
        end=hibob_holiday.end,
        name=hibob_holiday.name,
        hibob_id=hibob_holiday.hibob_id,
        ayon_id=event_id,
    )
    return ayon_holiday


def update_holiday(
    hibob_holiday: HolidayItem,
    ayon_holiday: HolidayItem,
) -> None:
    ayon_holiday.name = hibob_holiday.name

    update_booking_event(
        ayon_holiday.ayon_id,
        label=hibob_holiday.name,
        start_time=hibob_holiday.start.isoformat(),
        end_time=hibob_holiday.end.isoformat(),
        data={
            HIBOB_ID_KEY: hibob_holiday.hibob_id
        },
    )


def remove_holiday(
    ayon_holiday: HolidayItem,
) -> None:
    delete_booking_event(ayon_holiday.ayon_id)


def sync_holidays():
    log = logging.getLogger("HiBobSync")

    addon_settings = get_addon_settings()
    hibob_user, hibob_api_key = get_hibob_credentials(addon_settings)
    if not hibob_user or not hibob_api_key:
        raise MissingCredentialsError(
            "Missing HiBob credentials. Please check your service"
            " credentials settings."
        )

    # TODO use settings to load ignored policy types
    ignored_policy_types = []

    hibob_holidays_by_email = get_hibob_holidays(
        hibob_user, hibob_api_key, ignored_policy_types
    )
    emails = set(hibob_holidays_by_email)

    ayon_email_by_username: dict[str, str] = {}
    for user in ayon_api.get_users(
        emails=emails,
        fields={"name", "attrib.email"},
    ):
        email = user["attrib"]["email"]
        if email:
            ayon_email_by_username[user["name"]] = email.lower()

    resource_id_by_email: dict[str, str] = {}

    for resource in get_resources(
        resource_filter={
            "conditions": [
                {
                    "key": "resourceType",
                    "value": "person",
                    "operator": "eq",
                }
            ]
        }
    ):
        username = resource.get("ayonUserName")
        email = ayon_email_by_username.get(username)
        if email is not None:
            resource_id_by_email[email] = resource["id"]

    ayon_holidays: dict[str, list[HolidayItem]] = get_ayon_holidays(
        resource_id_by_email
    )

    for email, hibob_holidays in hibob_holidays_by_email.items():
        resource_id = resource_id_by_email.get(email)
        if not resource_id:
            continue

        ayon_user_holidays = ayon_holidays.get(email, [])

        hibob_holidays_queue = collections.deque(hibob_holidays)
        current_iter_len = len(hibob_holidays_queue)
        changed = False
        only_intersecting_with_multiple = False

        max_iter = 10000
        current_iter = 0
        while hibob_holidays_queue:
            # Making sure that the while loop does not stuck server
            # - developer can make mistakes and there is no reason to stuck
            #   server
            if current_iter > max_iter:
                raise Exception("BUG: Over iterated holidays")
            current_iter += 1

            # Get hibob holiday item
            hibob_holiday: HolidayItem = hibob_holidays_queue.popleft()
            # Find same or intersecting item
            same_ayon_holiday: HolidayItem | None = None
            intersecting: list[HolidayItem] = []
            for ayon_holiday in ayon_user_holidays:
                if ayon_holiday.processed:
                    continue

                if hibob_holiday == ayon_holiday:
                    same_ayon_holiday = ayon_holiday
                    break

                if hibob_holiday.intersects(ayon_holiday):
                    intersecting.append(ayon_holiday)

            # Mark matching AYON holiday item as processed
            if same_ayon_holiday is not None:
                changed = True
                log.debug(
                    f"Holiday {same_ayon_holiday} is already in AYON"
                )
                # Update name if is different
                if hibob_holiday.name != same_ayon_holiday.name:
                    log.debug(
                        f"Updating event name {hibob_holiday.name}"
                        f" -> {same_ayon_holiday.name}"
                    )
                    update_holiday(hibob_holiday, same_ayon_holiday)
                same_ayon_holiday.set_processed()

            # Create new holiday item if none is intersecting
            elif not intersecting:
                changed = True
                log.debug(
                    f"Creating new holiday in AYON {hibob_holiday}"
                )
                ayon_holiday = create_new_holiday(
                    hibob_holiday, resource_id
                )
                ayon_user_holidays.append(ayon_holiday)
                ayon_holiday.set_processed()

            # Update AYON holiday if there is only one intersecting
            #   and use first of intersecting holidays if there are more
            #   if only multiintersection holidays are remaining
            elif len(intersecting) == 1 or only_intersecting_with_multiple:
                changed = True
                ayon_holiday = intersecting[0]
                log.debug(
                    "Updating holiday in AYON"
                    f" {ayon_holiday}->{hibob_holiday}"
                )
                update_holiday(hibob_holiday, ayon_holiday)
                ayon_holiday.set_processed()

            # Just append multiintersection item to the end of queue
            elif not only_intersecting_with_multiple:
                hibob_holidays_queue.append(hibob_holiday)

            # Handle end of loop
            #   - enumeration of queue to know if already processed
            #       items are being processed
            #   - reset 'changed' nad length of current queue tp be
            #       able identify next end of loop
            current_iter_len -= 1
            if current_iter_len <= 0:
                if changed:
                    changed = False
                    current_iter_len = len(hibob_holidays_queue)
                else:
                    only_intersecting_with_multiple = True

        # Remove all not processed AYON events
        #   - they were not found in HiBob
        for ayon_holiday in ayon_user_holidays:
            if not ayon_holiday.processed:
                log.debug(f"Removing holiday {ayon_holiday}")
                remove_holiday(ayon_holiday)


def _cleanup_process():
    """Cleanup timer threads on exit."""
    if _GlobalContext.process_cleaned_up:
        return
    _GlobalContext.process_cleaned_up = True
    logging.info("Process stop requested. Terminating process.")
    if not _GlobalContext.stop_event.is_set():
        _GlobalContext.stop_event.set()


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    log = logging.getLogger("HiBobSync")

    try:
        ayon_api.init_service()
        connected = True
    except Exception:
        connected = False

    if not connected:
        log.warning("Failed to connect to AYON server.")
        # Sleep for 10 seconds, so it is possible to see the message in
        #   docker
        # NOTE: Becuase AYON connection failed, there's no way how to log it
        #   to AYON server (obviously)... So stdout is all we have.
        time.sleep(10)
        sys.exit(1)

    log.info("Connected to AYON server.")

    # Register interrupt signal
    def signal_handler(sig, frame):
        _cleanup_process()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(_cleanup_process)

    ayon_api.set_sender_type("hibob")
    try:
        last_sync = 0
        while not _GlobalContext.stop_event.is_set():
            delta = time.time() - last_sync
            if delta < SYNC_DELTA:
                time.sleep(5)
                continue

            try:
                sync_holidays()
            except MissingCredentialsError as exc:
                log.error(str(exc))

            last_sync = time.time()

    finally:
        _cleanup_process()


if __name__ == "__main__":
    main()
