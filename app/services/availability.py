"""Availability and 4-Directional Buffer Calculation Engine."""

from datetime import date, datetime
import logging
from app.argentura.models import (
    Appointment,
    DaySchedule,
    DirectionalBuffers,
    LadyProfile,
    WorkSchedule,
)

log = logging.getLogger("argentura.availability")

ACTIVE_STATUSES = {
    "angefragt",
    "bestaetigt",
    "wartet_mitarbeiterin",
    "geblockt",
    "verlaengert",
}


def time_to_minutes(t_str: str) -> int:
    """Convert 'HH:MM' string to integer minutes from midnight."""
    parts = t_str.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def minutes_to_time(mins: int) -> str:
    """Convert integer minutes to 'HH:MM' string."""
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def get_directional_buffer(
    loc_from: str, loc_to: str, buffers: DirectionalBuffers
) -> int:
    """Apply the specific directional transition buffer between two bookings."""
    from_loc = loc_from.lower()
    to_loc = loc_to.lower()

    if from_loc == "incall" and to_loc == "incall":
        return buffers.incallToIncall
    elif from_loc == "incall" and to_loc == "outcall":
        return buffers.incallToOutcall
    elif from_loc == "outcall" and to_loc == "outcall":
        return buffers.outcallToOutcall
    elif from_loc == "outcall" and to_loc == "incall":
        return buffers.outcallToIncall
    return buffers.incallToIncall


class AvailabilityEngine:
    @staticmethod
    def is_slot_available(
        lady: LadyProfile,
        appointments: list[Appointment],
        check_date: str,
        start_time: str,
        duration_minutes: int,
        location_type: str = "incall",
        find_alternatives: bool = True,
    ) -> tuple[bool, str, list[str]]:
        """Check availability for a proposed slot.

        Returns: (is_available, reason, list_of_alternative_slots)
        """
        try:
            d = date.fromisoformat(check_date)
        except ValueError:
            return False, "Invalid date format (must be YYYY-MM-DD)", []

        schedule = lady.workSchedule or WorkSchedule()
        day_sched: DaySchedule = schedule.get_day(d.isoweekday())

        if not day_sched.on:
            return False, f"{lady.name} is not on duty on this day", []

        work_start = time_to_minutes(day_sched.start)
        work_end = time_to_minutes(day_sched.end)
        req_start = time_to_minutes(start_time)
        req_end = req_start + duration_minutes

        if req_start < work_start or req_end > work_end:
            alternatives = (
                AvailabilityEngine.find_available_slots(
                    lady, appointments, check_date, duration_minutes, location_type
                )
                if find_alternatives
                else []
            )
            return (
                False,
                f"Outside working hours ({day_sched.start} - {day_sched.end})",
                alternatives,
            )

        # 1. Check breaks
        for brk in day_sched.breaks:
            b_start = time_to_minutes(brk["start"])
            b_end = time_to_minutes(brk["end"])
            if max(req_start, b_start) < min(req_end, b_end):
                alternatives = (
                    AvailabilityEngine.find_available_slots(
                        lady, appointments, check_date, duration_minutes, location_type
                    )
                    if find_alternatives
                    else []
                )
                return (
                    False,
                    f"Overlaps with scheduled break ({brk['start']} - {brk['end']})",
                    alternatives,
                )

        # 2. Check existing bookings & directional buffers
        buffers = schedule.scheduleTransitionBuffers
        day_appts = [
            a
            for a in appointments
            if a.date == check_date and a.status in ACTIVE_STATUSES
        ]

        for apt in day_appts:
            apt_start = time_to_minutes(apt.startTime)
            apt_end = time_to_minutes(apt.endTime)
            apt_loc = apt.locationType

            # Direct time overlap
            if max(req_start, apt_start) < min(req_end, apt_end):
                alternatives = (
                    AvailabilityEngine.find_available_slots(
                        lady, appointments, check_date, duration_minutes, location_type
                    )
                    if find_alternatives
                    else []
                )
                return False, "Conflict with an existing appointment", alternatives

            # Buffer after previous appointment
            if apt_end <= req_start:
                needed_buffer = get_directional_buffer(apt_loc, location_type, buffers)
                if req_start < apt_end + needed_buffer:
                    alternatives = (
                        AvailabilityEngine.find_available_slots(
                            lady, appointments, check_date, duration_minutes, location_type
                        )
                        if find_alternatives
                        else []
                    )
                    return (
                        False,
                        f"Requires {needed_buffer} min transition buffer after prior booking",
                        alternatives,
                    )

            # Buffer before subsequent appointment
            if req_end <= apt_start:
                needed_buffer = get_directional_buffer(location_type, apt_loc, buffers)
                if apt_start < req_end + needed_buffer:
                    alternatives = (
                        AvailabilityEngine.find_available_slots(
                            lady, appointments, check_date, duration_minutes, location_type
                        )
                        if find_alternatives
                        else []
                    )
                    return (
                        False,
                        f"Requires {needed_buffer} min transition buffer before next booking",
                        alternatives,
                    )

        return True, "Available", []

    @staticmethod
    def find_available_slots(
        lady: LadyProfile,
        appointments: list[Appointment],
        check_date: str,
        duration_minutes: int,
        location_type: str = "incall",
        max_suggestions: int = 3,
    ) -> list[str]:
        """Scan candidate start times in 30-min steps to find available slots."""
        schedule = lady.workSchedule or WorkSchedule()
        try:
            d = date.fromisoformat(check_date)
        except ValueError:
            return []

        day_sched = schedule.get_day(d.isoweekday())
        if not day_sched.on:
            return []

        work_start = time_to_minutes(day_sched.start)
        work_end = time_to_minutes(day_sched.end)

        available_times = []
        current = work_start
        step = 30

        while current + duration_minutes <= work_end:
            time_str = minutes_to_time(current)
            free, _, _ = AvailabilityEngine.is_slot_available(
                lady,
                appointments,
                check_date,
                time_str,
                duration_minutes,
                location_type,
                find_alternatives=False,
            )
            if free:
                available_times.append(time_str)
                if len(available_times) >= max_suggestions:
                    break
            current += step

        return available_times
