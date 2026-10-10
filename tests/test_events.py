"""Unit tests for ares/events.py (pure, no network)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ares.models import BASELINE_POOL, CrisisEvent, Policy
from ares.events import apply_event, parse_event
from ares.optimizer import enumerate_candidates

ROOT = Path(__file__).resolve().parents[1]
POOL = dict(BASELINE_POOL)


def official() -> dict:
    return json.loads((ROOT / "starter_kit" / "event_injection_format.json").read_text(encoding="utf-8"))


def test_official_starter_format():
    data = official()
    e = parse_event(data)
    assert e.event_id == "CRISIS_002" and e.name == "Solar Aftershock"
    assert e.percent_changes == {"power": -30.0} and e.deltas == {} and e.set_values == {}
    assert e.requires_replan is True and e.duration_hours == 6.0
    assert e.message_to_commander.startswith("ALERT")
    assert any("power_reduction_percent=30 -> -30% of Power" in n for n in e.interpretation_notes)
    assert any("affected_systems" in n for n in e.interpretation_notes)
    new, changes = apply_event(POOL, e)
    assert new["power"] == 55          # ceil(79 * 0.30) = 24 removed
    assert changes == ["Power 79 -> 55 (-24, -30%)"]
    assert parse_event(json.dumps(data)).percent_changes == {"power": -30.0}


@pytest.mark.parametrize("text, resource, delta, expected", [
    ("Available Power decreases by 4", "power", -4, 3),
    ("Available Water decreases by 3", "water", -3, 5),
    ("Available Oxygen decreases by 3", "oxygen", -3, 2),
    ("Available Robot time decreases by 4", "robot", -4, 1),
    ("Available Bandwidth decreases by 2", "bandwidth", -2, 3),
])
def test_practice_events_end_to_end(text, resource, delta, expected):
    e = parse_event(text)
    assert e.deltas == {resource: delta}
    new, _ = apply_event(POOL, e)
    assert enumerate_candidates(new, Policy.baseline()) == []
    assert len(enumerate_candidates(new, Policy.override())) == expected


@pytest.mark.parametrize("data, deltas", [
    ({"name": "Leak", "changes": {"power": -4}}, {"power": -4}),
    ({"name": "Leak", "resource_changes": {"O2": -3, "comms": -1}}, {"oxygen": -3, "bandwidth": -1}),
    ({"deltas": {"Robot time": -4}}, {"robot": -4}),
    ({"water_reduction": 3, "impact": {"oxygen_decrease": 2, "bandwidth_delta": -2}},
     {"water": -3, "oxygen": -2, "bandwidth": -2}),
    ({"impact": {"robot_time_reduction": 4, "power_increase": 5, "water_reduction_units": 1}},
     {"robot": -4, "power": 5, "water": -1}),
    ({"impacts": [{"resource": "energy", "change": -6}]}, {"power": -6}),
    ({"name": "Single", "resource": "comms", "change": -2}, {"bandwidth": -2}),
])
def test_absolute_forms(data, deltas):
    e = parse_event(data)
    assert e.deltas == deltas and e.percent_changes == {} and e.set_values == {}
    assert e.interpretation_notes


def test_set_and_percent_keys():
    e = parse_event({"power_set": 60, "water_increase_percent": 10, "oxygen_reduction_percent": "25%"})
    assert e.set_values == {"power": 60}
    assert e.percent_changes == {"water": 10.0, "oxygen": -25.0}
    new, changes = apply_event(POOL, e)
    assert new["power"] == 60
    assert new["water"] == 57            # floor(52 * 0.10) = 5 added
    assert new["oxygen"] == 44           # ceil(59 * 0.25) = 15 removed
    assert "Water 52 -> 57 (+5, +10%)" in changes


@pytest.mark.parametrize("text, field, expected", [
    ("power -4", "deltas", {"power": -4}),
    ("Oxygen drops by 30%", "percent_changes", {"oxygen": -30.0}),
    ("bandwidth reduced by 2 units", "deltas", {"bandwidth": -2}),
    ("+5 water", "deltas", {"water": 5}),
    ("robot time increases by 3", "deltas", {"robot": 3}),
    ("Dust storm: power and water each drop by 2", "deltas", {"power": -2, "water": -2}),
    ("Lose 4 power and 2 water", "deltas", {"power": -4, "water": -2}),
    ("Solar aftershock: power generation drops by 30% for the next 6 hours.", "percent_changes", {"power": -30.0}),
    ("Power falls to 60", "set_values", {"power": 60}),
    ("Rover actuator failure: robots down by 4", "deltas", {"robot": -4}),
    ("Meteor strike on the panels, reducing power by 10 units", "deltas", {"power": -10}),
    ("Supply drop at Hour 20 with 6 extra water and 3 more robot time", "deltas", {"water": 6, "robot": 3}),
    ("Oxygen generator damaged. Oxygen falls from 59 to 50.", "set_values", {"oxygen": 50}),
    ("Power reduced by 10%, oxygen reduced by 5%", "percent_changes", {"power": -10.0, "oxygen": -5.0}),
])
def test_free_text(text, field, expected):
    e = parse_event(text)
    assert getattr(e, field) == expected
    assert e.interpretation_notes


def test_free_text_name_duration_and_forbidden_mode():
    e = parse_event("Dust Storm: power drops by 30% for 6 hours; E1 is offline")
    assert e.name == "Dust Storm" and e.duration_hours == 6.0
    assert e.percent_changes == {"power": -30.0} and e.forbidden_modes == ["E1"]
    assert e.event_id.startswith("EVT-")


def test_json_inside_string():
    e = parse_event('Judge input: {"event_name": "Relay storm", "changes": {"bandwidth": -2}} end')
    assert e.name == "Relay storm" and e.deltas == {"bandwidth": -2}
    assert "Parsed JSON object from text input" in e.interpretation_notes


def test_description_only_dict_parsed_as_text():
    e = parse_event({"event_name": "Water contamination",
                     "description": "Available Water decreases by 3"})
    assert e.deltas == {"water": -3}
    assert any("parsed the description text" in n for n in e.interpretation_notes)


def test_event_without_resource_change_is_valid():
    e = parse_event({"event_name": "Morale drop", "description": "Crew is tired", "requires_replan": "false"})
    assert e.deltas == {} and e.percent_changes == {} and e.set_values == {}
    assert e.requires_replan is False
    assert any("No recognised resource change" in n for n in e.interpretation_notes)
    assert apply_event(POOL, e) == (POOL, [])


def test_forbidden_modes_validated():
    e = parse_event({"name": "Rover lost", "changes": {"robot": -2}, "forbidden_modes": ["E1", "x9", "e2"]})
    assert e.forbidden_modes == ["E1", "E2"]
    assert any("x9" in n for n in e.interpretation_notes)


def test_default_id_and_name_deterministic():
    a, b = parse_event({"changes": {"power": -1}}), parse_event({"changes": {"power": -1}})
    assert a.event_id == b.event_id and a.event_id.startswith("EVT-")
    assert a.name == "Unnamed event"


def test_round_trip_from_event_dict():
    e = parse_event({"name": "Mixed", "power_set": 70, "water_reduction_percent": 10, "oxygen_change": -2})
    again = parse_event(e.to_dict())
    assert (again.set_values, again.percent_changes, again.deltas) == (e.set_values, e.percent_changes, e.deltas)
    assert again.event_id == e.event_id and again.name == "Mixed"


@pytest.mark.parametrize("bad", ["", "   ", {}, 42, None, [1, 2]])
def test_unparseable_input_raises(bad):
    with pytest.raises(ValueError):
        parse_event(bad)


def test_apply_event_clamps_and_does_not_mutate():
    pool = dict(POOL)
    snapshot = copy.deepcopy(pool)
    e = CrisisEvent(event_id="T", name="t", description="", deltas={"bandwidth": -50, "water": 2})
    new, changes = apply_event(pool, e)
    assert pool == snapshot
    assert new["bandwidth"] == 0 and new["water"] == 54
    assert "Bandwidth 17 -> 0 (-50, clamped at 0)" in changes


def test_apply_event_order_set_percent_delta():
    e = CrisisEvent(event_id="T", name="t", description="", set_values={"power": 60},
                    percent_changes={"power": -10.0}, deltas={"power": -1})
    new, changes = apply_event(POOL, e)
    assert new["power"] == 53            # 60 -> 54 -> 53
    assert changes == ["Power 79 -> 53 (set to 60, -6, -10%, -1)"]
