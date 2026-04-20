"""Google Calendar integration for appointment management.

Handles event CRUD, availability queries, Spanish national holiday detection,
and patient-level event filtering via extendedProperties.
"""

from __future__ import print_function

import os
import json
from datetime import datetime, timedelta, date, time
from functools import lru_cache

import pytz
import requests
import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build


def _resolve_secret(name: str, default: str = "") -> str:
    """Read an env var; if its value starts with 'arn:', resolve it via Secrets Manager."""
    v = os.getenv(name, default)
    if v and v.startswith("arn:"):
        sm = boto3.client("secretsmanager")
        s = sm.get_secret_value(SecretId=v).get("SecretString", "")
        return s or v
    return v


class CalendarManager:
    """Manages Google Calendar operations for the appointment chatbot."""

    def __init__(self):
        self.tz = pytz.timezone("Europe/Madrid")

        # Calendar ID: supports direct ID or Secrets Manager ARN
        cal_id_env = _resolve_secret("GOOGLE_CALENDAR_ID", "")
        self.calendar_id = cal_id_env if cal_id_env else "primary"

        # Credentials: supports JSON in env/secret or fallback to file
        sa_json = _resolve_secret("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if sa_json and sa_json.strip().startswith("{"):
            info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/calendar"]
            )
        else:
            sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
            creds = service_account.Credentials.from_service_account_file(
                sa_file, scopes=["https://www.googleapis.com/auth/calendar"]
            )

        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # ── Event CRUD ──────────────────────────────────────────────

    def create_event(self, summary: str, description: str, start: str, end: str, patient_id: str):
        """Create a calendar event with patient metadata in extendedProperties."""
        event = {
            "summary": summary,
            "description": f"{description}\nPatient: {patient_id}",
            "start": {"dateTime": start},
            "end": {"dateTime": end},
            "extendedProperties": {"private": {"paciente_id": patient_id}},
        }
        return self.service.events().insert(calendarId=self.calendar_id, body=event).execute()

    def list_events(self, start: str, end: str, max_events: int = 100):
        """List all events in the given time range."""
        events = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start,
                timeMax=end,
                maxResults=max_events,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return events.get("items", [])

    def cancel_event(self, event_id: str):
        """Delete an event by ID."""
        self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()

    def update_event_time(self, event_id: str, new_start: str, new_end: str):
        """Reschedule an existing event to a new time slot."""
        ev = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        ev["start"] = {"dateTime": new_start}
        ev["end"] = {"dateTime": new_end}
        self.service.events().update(calendarId=self.calendar_id, eventId=event_id, body=ev).execute()

    # ── Availability ────────────────────────────────────────────

    def get_available_slots(self, date_str: str) -> list[str]:
        """Return available 1-hour slots (HH:00) between 09:00–18:00 for a given date.

        Checks existing events and excludes any overlapping hours.
        """
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_start = self.tz.localize(datetime.combine(dt, time(hour=9, minute=0)))
            day_end = self.tz.localize(datetime.combine(dt, time(hour=18, minute=0)))

            events = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
                .get("items", [])
            )

            occupied = set()
            for ev in events:
                ev_start = ev["start"].get("dateTime")
                ev_end = ev["end"].get("dateTime")
                if ev_start and ev_end:
                    dt_start = datetime.fromisoformat(ev_start)
                    dt_end = datetime.fromisoformat(ev_end)
                    for h in range(9, 18):
                        slot_start = dt_start.replace(hour=h, minute=0, second=0, microsecond=0)
                        slot_end = slot_start + timedelta(hours=1)
                        if slot_end > dt_start and slot_start < dt_end:
                            occupied.add(f"{h:02d}:00")

            return [f"{h:02d}:00" for h in range(9, 18) if f"{h:02d}:00" not in occupied]
        except Exception:
            return []

    def check_availability(self, start_iso: str, end_iso: str) -> bool:
        """Return True if no events overlap the given time range."""
        events = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start_iso,
                timeMax=end_iso,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
        return len(events) == 0

    def check_availability_excluding(self, event_id: str, start_iso: str, end_iso: str) -> bool:
        """Return True if no events (other than event_id) overlap the time range."""
        events = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start_iso,
                timeMax=end_iso,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
        return all(ev.get("id") == event_id for ev in events)

    # ── Holidays ────────────────────────────────────────────────

    @lru_cache(maxsize=4)
    def _spanish_national_holidays(self, year: int) -> set:
        """Fetch Spanish national holidays from the Nager.Date API (cached per year)."""
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/ES"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return {date.fromisoformat(item["date"]) for item in r.json() if item.get("global", False)}

    def is_holiday(self, d: date) -> bool:
        """Check if a date is a Spanish national holiday."""
        try:
            return d in self._spanish_national_holidays(d.year)
        except Exception:
            return False

    # ── Multi-day availability ──────────────────────────────────

    def get_availability_next_days(self, days: int = 5) -> dict[str, list[str]]:
        """Build a date→available_slots map for the next N business days.

        Skips weekends and Spanish national holidays. Slots are 1-hour blocks
        between 09:00 and 18:00 (Europe/Madrid).
        """
        today = datetime.now(self.tz).date()
        result = {}
        d = today + timedelta(days=1)
        while len(result) < days:
            if d.weekday() < 5 and not self.is_holiday(d):
                day_start = self.tz.localize(datetime.combine(d, time(9, 0))).isoformat()
                day_end = self.tz.localize(datetime.combine(d, time(18, 0))).isoformat()
                events = (
                    self.service.events()
                    .list(
                        calendarId=self.calendar_id,
                        timeMin=day_start,
                        timeMax=day_end,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                    .get("items", [])
                )

                occupied = set()
                for ev in events:
                    ev_start = ev["start"].get("dateTime")
                    ev_end = ev["end"].get("dateTime")
                    if ev_start and ev_end:
                        dt_start = datetime.fromisoformat(ev_start)
                        dt_end = datetime.fromisoformat(ev_end)
                        for h in range(9, 18):
                            slot_start = dt_start.replace(hour=h, minute=0, second=0, microsecond=0)
                            slot_end = slot_start + timedelta(hours=1)
                            if slot_end > dt_start and slot_start < dt_end:
                                occupied.add(f"{h:02d}:00")

                available = [f"{h:02d}:00" for h in range(9, 18) if f"{h:02d}:00" not in occupied]
                result[d.strftime("%Y-%m-%d")] = available
            d += timedelta(days=1)
        return result

    # ── Patient queries ─────────────────────────────────────────

    def list_patient_appointments(self, patient_id: str, days_ahead: int = 365) -> list:
        """List upcoming appointments for a patient.

        First tries filtering by extendedProperties.private.paciente_id.
        Falls back to text search in the event description if no results.
        """
        now = datetime.now(self.tz)
        until = now + timedelta(days=days_ahead)
        try:
            events = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=now.isoformat(),
                    timeMax=until.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    privateExtendedProperty=[f"paciente_id={patient_id}"],
                    maxResults=2500,
                )
                .execute()
                .get("items", [])
            )
        except Exception:
            events = []

        # Fallback: search by patient ID in description text
        if not events:
            all_events = self.list_events(now.isoformat(), until.isoformat(), max_events=2500)
            filtered = []
            for ev in all_events:
                desc = ev.get("description", "") or ""
                priv = ev.get("extendedProperties", {}).get("private", {})
                if (
                    priv.get("paciente_id") == patient_id
                    or f"DNI: {patient_id}" in desc
                    or f"Patient: {patient_id}" in desc
                ):
                    filtered.append(ev)
            events = filtered

        events.sort(key=lambda e: e.get("start", {}).get("dateTime", ""))
        return events
