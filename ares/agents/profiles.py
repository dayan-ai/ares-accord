"""Per-agent profiles (PDF p3): role, goals, constraints, main concern. Colony of 42 people."""
from __future__ import annotations

from ares.models import COMMANDER, ENGINEERING, FOOD, LIFE_SUPPORT, MEDICAL

COLONY_SIZE = 42
INJURED = 6

PROFILES: dict[str, dict] = {
    LIFE_SUPPORT: {
        "name": "Life Support",
        "role": "Seal the damaged habitat and stabilize air for all 42 colonists.",
        "goals": ["Keep breathable air for 42 people", "Seal the habitat breach", "Keep the living zone open"],
        "constraints": ["Needs continuous power for scrubbers", "Oxygen is non-negotiable for survival"],
        "main_concern": "oxygen and continuous power",
        "key_resources": ["oxygen", "power"],
    },
    MEDICAL: {
        "name": "Medical",
        "role": f"Stabilize and treat the {INJURED} injured colonists.",
        "goals": [f"Stabilize {INJURED} injured", "Perform urgent surgery", "Keep telemedicine link to Earth"],
        "constraints": ["Surgery needs power and oxygen", "Telemedicine needs bandwidth"],
        "main_concern": "power, oxygen and bandwidth",
        "key_resources": ["power", "oxygen", "bandwidth"],
    },
    FOOD: {
        "name": "Food Production",
        "role": "Protect the crop cycle and the greenhouse cooling loop.",
        "goals": ["Protect the current crop cycle", "Keep the cooling loop running", "Preserve food reserves"],
        "constraints": ["Crops and cooling need water", "Grow lights and pumps need power"],
        "main_concern": "water and power",
        "key_resources": ["water", "power"],
    },
    ENGINEERING: {
        "name": "Engineering",
        "role": "Repair power generation and critical colony systems.",
        "goals": ["Restore power generation", "Repair critical systems", "Keep repair capacity (rover) intact"],
        "constraints": ["Repairs need robot time", "Remote diagnostics need bandwidth"],
        "main_concern": "robot time and bandwidth",
        "key_resources": ["robot", "bandwidth"],
    },
    COMMANDER: {
        "name": "Commander",
        "role": "Coordinate the departments and propose a colony-wide plan; final approval belongs to the "
                "deterministic validator and the four department votes.",
        "goals": [f"Colony-wide safety for {COLONY_SIZE} people", "Fair, feasible allocation",
                  "Honour return commitments to any department that sacrifices"],
        "constraints": ["Cannot approve or bypass the validator", "Plans must be chosen from feasible candidates",
                        "Crisis Override only after a crisis event"],
        "main_concern": "colony-wide safety",
        "key_resources": ["power", "water", "oxygen", "robot", "bandwidth"],
    },
}
