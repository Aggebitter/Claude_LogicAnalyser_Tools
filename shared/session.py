"""
Shared session state for logic analyser MCP servers.
Persists channel labels, protocol, assertions and capture paths across mode switches.
"""

import json
import os
from pathlib import Path
from typing import Any

SESSION_FILE = Path.home() / ".claude" / "logic_analyser_session.json"

_DEFAULT: dict[str, Any] = {
    "device": None,
    "backend": None,
    "channels": {},
    "protocol": None,
    "sample_rate": None,
    "assertions": [],
    "captures": {
        "baseline": None,
        "latest": None,
    },
}


def load() -> dict[str, Any]:
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE) as f:
                data = json.load(f)
            # Merge with defaults so new keys are always present
            merged = dict(_DEFAULT)
            merged.update(data)
            merged["captures"] = {**_DEFAULT["captures"], **data.get("captures", {})}
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT)


def save(state: dict[str, Any]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(state, f, indent=2)


def annotate_channel(channel: int | str, label: str) -> dict[str, Any]:
    state = load()
    state["channels"][str(channel)] = label
    save(state)
    return state


def set_protocol(protocol: str) -> dict[str, Any]:
    state = load()
    state["protocol"] = protocol
    save(state)
    return state


def set_device(device: str, backend: str, sample_rate: int | None = None) -> dict[str, Any]:
    state = load()
    state["device"] = device
    state["backend"] = backend
    if sample_rate is not None:
        state["sample_rate"] = sample_rate
    save(state)
    return state


def add_assertion(assertion_id: str, description: str, kind: str, params: dict) -> dict[str, Any]:
    """kind: 'timing' | 'logic' | 'protocol'"""
    state = load()
    # Replace if same id already exists
    state["assertions"] = [a for a in state["assertions"] if a["id"] != assertion_id]
    state["assertions"].append({
        "id": assertion_id,
        "description": description,
        "type": kind,
        "params": params,
    })
    save(state)
    return state


def remove_assertion(assertion_id: str) -> dict[str, Any]:
    state = load()
    state["assertions"] = [a for a in state["assertions"] if a["id"] != assertion_id]
    save(state)
    return state


def clear_assertions() -> dict[str, Any]:
    state = load()
    state["assertions"] = []
    save(state)
    return state


def set_capture(role: str, path: str) -> dict[str, Any]:
    """role: 'baseline' | 'latest'"""
    state = load()
    state["captures"][role] = path
    save(state)
    return state


def reset() -> dict[str, Any]:
    state = dict(_DEFAULT)
    save(state)
    return state


def summary() -> str:
    state = load()
    lines = [
        f"Device  : {state['device'] or 'not set'} ({state['backend'] or '?'})",
        f"Protocol: {state['protocol'] or 'unknown'}",
        f"Rate    : {state['sample_rate'] or 'not set'}",
        "Channels:",
    ]
    if state["channels"]:
        for ch, label in sorted(state["channels"].items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            lines.append(f"  CH{ch}: {label}")
    else:
        lines.append("  (none labelled)")
    lines.append(f"Assertions: {len(state['assertions'])}")
    lines.append(f"Baseline  : {state['captures']['baseline'] or 'none'}")
    lines.append(f"Latest    : {state['captures']['latest'] or 'none'}")
    return "\n".join(lines)
