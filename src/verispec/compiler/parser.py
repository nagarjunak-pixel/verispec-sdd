"""Structured `SPEC.md` compiler into `SpecModel` IR."""

from __future__ import annotations

import re

from verispec.compiler.expr import validate_expression
from verispec.types import InvariantSpec, SpecModel, StateSpec, TransitionSpec


class SpecCompileError(ValueError):
    """Raised when a `SPEC.md` document is structurally invalid."""


_HEADER_RE = re.compile(r"^#\s+(?:Spec:\s*)?(.+?)\s*$", re.IGNORECASE)
_META_RE = re.compile(r"^[-*]?\s*\*\*?(Initial|Terminal|Version)\*\*?:\s*(.+?)\s*$", re.IGNORECASE)
_STATE_BULLET_RE = re.compile(r"^[-*]\s+`?([A-Za-z_][A-Za-z0-9_]*)`?(?:\s*[:—-]\s*(.*))?$")
_INV_BULLET_RE = re.compile(
    r"^[-*]\s+`?([A-Za-z_][A-Za-z0-9_-]*)`?(?:\s*\[([A-Za-z_][A-Za-z0-9_]*)\])?\s*:\s*`?(.+?)`?\s*$"
)


def _clean_cell(raw: str) -> str | None:
    val = raw.strip()
    if val.startswith("`") and val.endswith("`") and len(val) >= 2:
        val = val[1:-1].strip()
    if not val or val in {"-", "—", "none", "None", "true", "True", "always"}:
        return None
    return val


def compile_spec_markdown(markdown_text: str) -> SpecModel:
    """Compile a structured `SPEC.md` specification into a validated `SpecModel`."""
    lines = markdown_text.splitlines()
    spec_name = "UnnamedSpec"
    version = "1.0.0"
    initial_state: str | None = None
    terminal_states: list[str] = []

    states_map: dict[str, StateSpec] = {}
    transitions: list[TransitionSpec] = []
    invariants: list[InvariantSpec] = []

    section: str = "header"
    table_headers: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        h1 = _HEADER_RE.match(line)
        if h1 and not line.startswith("##"):
            spec_name = h1.group(1).strip()
            continue

        if line.startswith("##"):
            sec_title = line.lstrip("#").strip().lower()
            table_headers = []
            if "state" in sec_title:
                section = "states"
            elif "transition" in sec_title:
                section = "transitions"
            elif "invariant" in sec_title:
                section = "invariants"
            else:
                section = "other"
            continue

        meta = _META_RE.match(line.replace("**", ""))
        if meta or _META_RE.match(line):
            m = _META_RE.match(line) or meta
            assert m is not None
            key = m.group(1).lower()
            val = m.group(2).strip().strip("`")
            if key == "initial":
                initial_state = val.split(",")[0].strip().strip("`")
            elif key == "terminal":
                terminal_states = [
                    s.strip().strip("`")
                    for s in val.split(",")
                    if s.strip() and s.strip().lower() not in {"none", "-"}
                ]
            elif key == "version":
                version = val
            continue

        if section == "states":
            sb = _STATE_BULLET_RE.match(line)
            if sb:
                sname = sb.group(1).strip()
                rest = (sb.group(2) or "").strip()
                state_inv: str | None = None
                if "[invariant:" in rest:
                    before, after = rest.split("[invariant:", 1)
                    rest = before.strip()
                    state_inv = after.rsplit("]", 1)[0].strip().strip("`")
                    validate_expression(state_inv)
                states_map[sname] = StateSpec(name=sname, description=rest, invariant=state_inv)

        elif section == "transitions" and line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells if c):
                continue
            lower_cells = [c.lower() for c in cells]
            if not table_headers and "from" in lower_cells and "to" in lower_cells:
                table_headers = lower_cells
                continue
            if len(cells) < 3:
                raise SpecCompileError(
                    f"Transition row requires at least From | Event | To: {line}"
                )

            from_st = cells[0].strip("` ").strip()
            evt = cells[1].strip("` ").strip()
            to_st = cells[2].strip("` ").strip()
            guard = _clean_cell(cells[3]) if len(cells) >= 4 else None
            post = _clean_cell(cells[4]) if len(cells) >= 5 else None

            if not from_st or not evt or not to_st:
                raise SpecCompileError(f"Incomplete transition row: {line}")
            if guard is not None:
                validate_expression(guard)
            if post is not None:
                validate_expression(post)

            for st_name in (from_st, to_st):
                if st_name not in states_map:
                    states_map[st_name] = StateSpec(name=st_name)

            transitions.append(
                TransitionSpec(
                    index=len(transitions),
                    from_state=from_st,
                    event=evt,
                    to_state=to_st,
                    guard=guard,
                    postcondition=post,
                )
            )

        elif section == "invariants":
            ib = _INV_BULLET_RE.match(line)
            if ib:
                inv_name = ib.group(1).strip()
                scope = ib.group(2).strip() if ib.group(2) else None
                expr = ib.group(3).strip().strip("`")
                validate_expression(expr)
                invariants.append(InvariantSpec(name=inv_name, expression=expr, scope_state=scope))

    if not states_map:
        raise SpecCompileError("SPEC.md defines zero states or transitions")

    if initial_state is None:
        initial_state = next(iter(states_map.keys()))

    if initial_state not in states_map:
        states_map[initial_state] = StateSpec(name=initial_state)

    for ts in terminal_states:
        if ts not in states_map:
            states_map[ts] = StateSpec(name=ts)

    for sname, s_obj in states_map.items():
        s_obj.is_initial = sname == initial_state
        s_obj.is_terminal = sname in terminal_states

    for inv in invariants:
        if inv.scope_state is not None and inv.scope_state not in states_map:
            raise SpecCompileError(
                f"Invariant {inv.name!r} references unknown state {inv.scope_state!r}"
            )

    ordered_events: list[str] = []
    seen_evts: set[str] = set()
    for tr in transitions:
        if tr.event not in seen_evts:
            seen_evts.add(tr.event)
            ordered_events.append(tr.event)

    return SpecModel(
        name=spec_name,
        version=version,
        initial_state=initial_state,
        terminal_states=terminal_states,
        states=list(states_map.values()),
        transitions=transitions,
        invariants=invariants,
        events=ordered_events,
    )
