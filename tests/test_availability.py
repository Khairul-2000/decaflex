"""Tests for 4-Directional Buffer and Availability Calculation Engine."""

from app.argentura.models import (
    Appointment,
    DaySchedule,
    DirectionalBuffers,
    LadyProfile,
    WorkSchedule,
)
from app.services.availability import AvailabilityEngine


def make_test_lady() -> LadyProfile:
    buffers = DirectionalBuffers(
        incallToIncall=30,
        incallToOutcall=60,
        outcallToOutcall=45,
        outcallToIncall=60,
    )
    # Mon: 10:00 - 20:00, Break 13:00 - 14:00
    mon_sched = DaySchedule(
        on=True,
        start="10:00",
        end="20:00",
        breaks=[{"start": "13:00", "end": "14:00"}],
    )
    sched = WorkSchedule(
        mo=mon_sched,
        scheduleTransitionBuffers=buffers,
    )
    return LadyProfile(
        id="lady-1",
        name="Julia",
        age=24,
        workSchedule=sched,
    )


def test_slot_available_clean_day():
    lady = make_test_lady()
    # 2026-10-12 was a Monday
    free, reason, alts = AvailabilityEngine.is_slot_available(
        lady=lady,
        appointments=[],
        check_date="2026-10-12",
        start_time="11:00",
        duration_minutes=60,
        location_type="incall",
    )
    assert free is True
    assert reason == "Available"


def test_outside_working_hours():
    lady = make_test_lady()
    free, reason, alts = AvailabilityEngine.is_slot_available(
        lady=lady,
        appointments=[],
        check_date="2026-10-12",
        start_time="09:00",  # starts before 10:00
        duration_minutes=60,
        location_type="incall",
    )
    assert free is False
    assert "Outside working hours" in reason
    assert len(alts) > 0  # suggests available alternatives like 10:00


def test_break_overlap():
    lady = make_test_lady()
    # Break is 13:00 - 14:00. Proposed slot: 13:30 - 14:30
    free, reason, alts = AvailabilityEngine.is_slot_available(
        lady=lady,
        appointments=[],
        check_date="2026-10-12",
        start_time="13:30",
        duration_minutes=60,
        location_type="incall",
    )
    assert free is False
    assert "scheduled break" in reason


def test_directional_buffer_incall_to_incall():
    lady = make_test_lady()
    # Existing: 11:00 - 12:00 Incall
    prior = Appointment(
        ladyId="lady-1",
        date="2026-10-12",
        startTime="11:00",
        endTime="12:00",
        locationType="incall",
        customerName="Peter",
        customerPhone="+49170123456",
        serviceType="60 min INCALL",
        price=150.0,
        status="bestaetigt",
    )
    # Proposed Incall: 12:15 - 13:00 (only 15m gap, but incallToIncall buffer is 30m)
    free, reason, _ = AvailabilityEngine.is_slot_available(
        lady=lady,
        appointments=[prior],
        check_date="2026-10-12",
        start_time="12:15",
        duration_minutes=45,
        location_type="incall",
    )
    assert free is False
    assert "30 min transition buffer" in reason

    # Proposed Incall: 12:30 - 13:30 (exactly 30m gap) -> overlaps break at 13:00!
    # Proposed Incall: 14:00 - 15:00 (after break) -> should be free
    free2, _, _ = AvailabilityEngine.is_slot_available(
        lady=lady,
        appointments=[prior],
        check_date="2026-10-12",
        start_time="14:00",
        duration_minutes=60,
        location_type="incall",
    )
    assert free2 is True


def test_directional_buffer_incall_to_outcall():
    lady = make_test_lady()
    # Prior Incall ends at 16:00
    prior = Appointment(
        ladyId="lady-1",
        date="2026-10-12",
        startTime="15:00",
        endTime="16:00",
        locationType="incall",
        customerName="Peter",
        customerPhone="+49170123456",
        serviceType="60 min INCALL",
        price=150.0,
        status="bestaetigt",
    )
    # Proposed Outcall: starts at 16:30 (30 min gap, but incallToOutcall buffer is 60m)
    free, reason, _ = AvailabilityEngine.is_slot_available(
        lady=lady,
        appointments=[prior],
        check_date="2026-10-12",
        start_time="16:30",
        duration_minutes=60,
        location_type="outcall",
    )
    assert free is False
    assert "60 min transition buffer" in reason
