import json
import collections
import datetime
import base64

import requests
import arrow
import ayon_api

from hibob_common import HiBobServerAction, get_hibob_icon_url


FTRACK_EVENT_NAME_SUFFIX = "(HiBob)"


class HolidayItem:
    def __init__(self, start, end, name):
        if isinstance(start, str):
            start = arrow.get(start)

        if isinstance(end, str):
            end = arrow.get(end)
        self.start = start
        self.end = end
        self.name = name

    def __repr__(self):
        return "{} {}/{}".format(
            self.__class__.__name__,
            self.start.format("YYYY-MM-DD"),
            self.end.format("YYYY-MM-DD")
        )

    def __eq__(self, other):
        return (
            self.start == other.start
            and self.end == other.end
        )

    def intersects(self, other):
        if (
            self.start > other.end
            or self.end < other.start
        ):
            return False
        return True


class HiBobHolidayItem(HolidayItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ftrack_name = "{} {}".format(self.name, FTRACK_EVENT_NAME_SUFFIX)

    @property
    def ftrack_event_start(self):
        return self.format_ftrack_date(self.start)

    @property
    def ftrack_event_end(self):
        return self.format_ftrack_date(self.end.shift(days=1))

    @staticmethod
    def format_ftrack_date(date):
        return date.format("YYYY-MM-DD")


class FtrackHolidayItem(HolidayItem):
    def __init__(self, calendar_event, *args, **kwargs):
        self._calendar_event = calendar_event
        self._processed = False
        super().__init__(*args, **kwargs)

    @property
    def calendar_event(self):
        return self._calendar_event

    @property
    def processed(self):
        return self._processed

    def set_processed(self):
        self._processed = True

    def __eq__(self, other):
        if isinstance(other, FtrackHolidayItem):
            return self._calendar_event["id"] == other._calendar_event["id"]

        return (
            self.start == other.start
            and self.end == other.end
        )


class SyncHiBobHolidaysAction(HiBobServerAction):
    identifier = "sync.hibob.holidays.server"
    label = "Sync HiBob Holidays"
    description = "Synchronize HiBob holidays with Ftrack"
    icon = get_hibob_icon_url()

    settings_key = "SyncHiBobHolidaysAction"

    def discover(self, session, entities, event):
        return self.valid_roles(session, entities, event)

    def launch(self, session, entities, event):
        hibob_user, hibob_api = self.get_hibob_credentials(
            session, event, entities
        )
        missing_keys = []
        if not hibob_user:
            missing_keys.append("Service user")
        if not hibob_api:
            missing_keys.append("API key")

        if missing_keys:
            verb = "is" if len(missing_keys) == 1 else "are"
            joined_missing = " and ".join(missing_keys)
            return {
                "message": (
                    f"HiBob {joined_missing} {verb} not set in settings"
                ),
                "success": False
            }

        users = session.query("select id, email from User").all()
        user_email_by_id = {
            user["id"]: user["email"].lower()
            for user in users
            if user["email"]
        }

        user_id_by_email = {
            user["email"].lower(): user["id"]
            for user in users
            if user["email"]
        }
        all_ftrack_holidays = self.get_ftrack_holidays(
            session, user_email_by_id
        )
        addon_settings = self.get_addon_settings(session, event, entities)
        ignored_policy_types = {
            policy_type.lower()
            for policy_type in (
                addon_settings["sync_config"]["ignored_policy_types"]
            )
        }
        all_hibob_holidays = self.get_hibob_holidays(
            hibob_user, hibob_api, ignored_policy_types
        )

        # Go through HiBob data (source of truth)
        for hibob_email, hibob_holidays in all_hibob_holidays.items():
            low_hibob_email = hibob_email.lower()
            user_id = user_id_by_email.get(low_hibob_email)
            if not user_id:
                self.log.debug("Did not find ftrack user with email {}".format(
                    hibob_email
                ))
                continue

            self.log.debug("* Processing holidays of email {}".format(
                hibob_email
            ))
            ftrack_holidays = all_ftrack_holidays.setdefault(
                low_hibob_email, []
            )

            # Handle all changes in one loop
            # - First create new holidays and holidays that are intersecting
            #   only one ftrack holiday
            # - Keep holidays intersecting with moret then one in queue
            #   and continuosly remove ftrack holidays that are processed from
            #   their
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
                hibob_holiday = hibob_holidays_queue.popleft()
                # Find same or intersecting item
                same_ftrack_holiday = None
                intersecting = []
                for ftrack_holiday in ftrack_holidays:
                    if ftrack_holiday.processed:
                        continue

                    if hibob_holiday == ftrack_holiday:
                        same_ftrack_holiday = ftrack_holiday
                        break

                    if hibob_holiday.intersects(ftrack_holiday):
                        intersecting.append(ftrack_holiday)

                # Mark matching ftrack holiday item as processed
                if same_ftrack_holiday is not None:
                    changed = True
                    self.log.debug("Holiday {} is already in ftrack".format(
                        str(same_ftrack_holiday)
                    ))
                    # Update name if is different
                    if hibob_holiday.ftrack_name != same_ftrack_holiday.name:
                        self.log.debug(
                            "Updating event name {} -> {}".format(
                                hibob_holiday.ftrack_name,
                                same_ftrack_holiday.name
                            ))
                        self.update_holiday(
                            session,
                            user_id,
                            hibob_holiday,
                            same_ftrack_holiday
                        )
                    same_ftrack_holiday.set_processed()

                # Create new holiday item if none is intersecting
                elif not intersecting:
                    changed = True
                    self.log.debug("Creating new holiday in ftrack {}".format(
                        str(hibob_holiday)
                    ))
                    ftrack_holiday = self.create_new_holiday(
                        session, user_id, hibob_holiday
                    )
                    ftrack_holidays.append(ftrack_holiday)
                    ftrack_holiday.set_processed()

                # Update ftrack holiday if there is only one intersecting
                #   and use first of intersecting holidays if there are more
                #   if only multiintersection holidays are remaining
                elif len(intersecting) == 1 or only_intersecting_with_multiple:
                    changed = True
                    ftrack_holiday = intersecting[0]
                    self.log.debug("Updating holiday in ftrack {}->{}".format(
                        str(ftrack_holiday), str(hibob_holiday)
                    ))
                    self.update_holiday(
                        session, user_id, hibob_holiday, ftrack_holiday
                    )
                    ftrack_holiday.set_processed()

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

            # Remove all not processed ftrack events
            #   - they were not found in HiBob
            for ftrack_holiday in ftrack_holidays:
                if not ftrack_holiday.processed:
                    self.log.debug("Removing deprecated holiday {}".format(
                        str(ftrack_holiday)
                    ))
                    self.remove_holiday(session, ftrack_holiday)

        return {
            "message": "Sync with HiBob finished",
            "success": True
        }

    def get_hibob_credentials(self, session, event, entities):
        addon_settings = self.get_addon_settings(session, event, entities)
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

    def get_ftrack_holidays(self, session, user_email_by_id):
        now_date = datetime.datetime.now()
        start_date = now_date - datetime.timedelta(days=7)
        start_date_str = start_date.strftime("%Y-%m-%d")
        calendar_events = session.query(
            "select id, name, start, end from CalendarEvent"
            f" where leave is True and end > {start_date_str}"
        ).all()
        calendar_events_by_id = {
            calendar_event["id"]: calendar_event
            for calendar_event in calendar_events
            if calendar_event["name"].endswith(FTRACK_EVENT_NAME_SUFFIX)
        }

        calendar_event_resources = []
        if calendar_events_by_id:
            joined_calendar_event_ids = ",".join([
                f'"{calendar_event_id}"'
                for calendar_event_id in calendar_events_by_id.keys()
            ])
            calendar_event_resources = session.query((
                "select id, resource_id from CalendarEventResource"
                f" where calendar_event_id in ({joined_calendar_event_ids})"
            )).all()
        holidays_by_email = collections.defaultdict(list)
        for event_resource in calendar_event_resources:
            resource_id = event_resource["resource_id"]
            calendar_event_id = event_resource["calendar_event_id"]
            calendar_event = calendar_events_by_id[calendar_event_id]

            email = user_email_by_id.get(resource_id)
            end = calendar_event["end"]
            holidays_by_email[email].append(
                FtrackHolidayItem(
                    calendar_event,
                    calendar_event["start"],
                    end.shift(days=-1),
                    calendar_event["name"],
                )
            )
        return holidays_by_email

    def get_hibob_holidays(self, hibob_user, hibob_api, ignored_policy_types):
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

        people_data = people_response.json()
        empoyees_data = people_data.pop("employees", [])
        employee_email_by_id = {
            employee["id"]: employee["email"]
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
        data = json.loads(response.text)
        outs = data.pop("outs", [])
        holiday_by_employee_email = collections.defaultdict(list)
        for item in outs:
            policy_type = item.get("polictType")
            if policy_type and policy_type.lower() in ignored_policy_types:
                continue
            employee_id = item["employeeId"]
            email = employee_email_by_id[employee_id]
            display_name = item.get("policyTypeDisplayName") or "Holiday"
            holiday_by_employee_email[email].append(
                HiBobHolidayItem(
                    item["startDate"],
                    item["endDate"],
                    display_name
                ),
            )
        return holiday_by_employee_email

    def create_new_holiday(self, session, user_id, hibob_holiday):
        calendar_event = session.create(
            "CalendarEvent",
            {
                "leave": True,
                "everyone": False,
                "forecast": True,
                "start": hibob_holiday.ftrack_event_start,
                "end": hibob_holiday.ftrack_event_end,
                "name": hibob_holiday.ftrack_name,
            }
        )
        session.create(
            "CalendarEventResource",
            {
                "calendar_event_id": calendar_event["id"],
                "resource_id": user_id
            }
        )
        session.commit()
        return FtrackHolidayItem(
            calendar_event,
            hibob_holiday.start,
            hibob_holiday.end,
            hibob_holiday.ftrack_name
        )

    def update_holiday(self, session, user_id, hibob_holiday, ftrack_holiday):
        calendar_event = ftrack_holiday.calendar_event
        calendar_event["start"] = hibob_holiday.ftrack_event_start
        calendar_event["end"] = hibob_holiday.ftrack_event_end
        calendar_event["name"] = hibob_holiday.ftrack_name
        ftrack_holiday.start = hibob_holiday.start
        ftrack_holiday.end = hibob_holiday.end
        ftrack_holiday.name = hibob_holiday.ftrack_name
        session.commit()

    def remove_holiday(self, session, ftrack_holiday):
        session.delete(ftrack_holiday.calendar_event)
        session.commit()


def register(session):
    SyncHiBobHolidaysAction(session).register()
