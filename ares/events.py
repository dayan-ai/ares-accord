"""Crisis-event parsing and application (pure, deterministic, no I/O).

`parse_event` turns any reasonable judge input (official starter JSON, simple
absolute changes, flat `<resource>_<operation>` keys, or free text) into a
canonical `CrisisEvent`. Every interpretation decision is recorded in
`event.interpretation_notes` so the dashboard can show how input was read.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from fractions import Fraction
from typing import Any, Mapping

from ares.models import MODE_PACKAGES, RESOURCE_LABELS, RESOURCES, CrisisEvent

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
_SYNONYMS: dict[str, str] = {
    **dict.fromkeys(("power", "energy", "kwh", "solar", "electricity", "electric"), "power"),
    **dict.fromkeys(("water", "h2o", "liters", "litres", "liter", "litre"), "water"),
    **dict.fromkeys(("oxygen", "o2", "air"), "oxygen"),
    **dict.fromkeys(("robot", "robots", "robottime", "rover", "rovers", "robotics", "drone", "drones"), "robot"),
    **dict.fromkeys(("bandwidth", "comms", "comm", "relay", "communication", "communications", "data", "band"),
                    "bandwidth"),
}
# Key-token classes for flat keys such as "power_reduction_percent".
_K_DEC = {"reduction", "reduce", "reduced", "reduces", "decrease", "decreased", "decreases", "drop", "drops",
          "dropped", "loss", "lose", "lost", "loses", "cut", "cuts", "minus", "down", "decline", "deficit",
          "shortage", "lower", "lowered", "removed", "remove"}
_K_INC = {"increase", "increased", "increases", "gain", "gains", "gained", "boost", "boosted", "add", "added",
          "plus", "up", "bonus", "extra", "raise", "raised", "restore", "restored", "recovered", "recovery"}
_K_DELTA = {"delta", "change", "changes", "changed", "adjust", "adjustment", "diff", "shift", "net"}
_K_SET = {"set", "new", "value", "available", "availability", "level", "remaining", "absolute"}
_K_PCT = {"percent", "pct", "percentage", "perc", "%"}
_K_ABS = {"units", "unit", "abs", "amount", "qty", "quantity", "count", "points"}
_K_FILLER = {"time", "hours", "hour", "hrs", "generation", "capacity", "supply", "output", "production",
             "pool", "resource", "of", "the", "total"}
_KEY_WORDS = _K_DEC | _K_INC | _K_DELTA | _K_SET | _K_PCT | _K_ABS | _K_FILLER

_CHANGE_CONTAINERS = {"changes": "delta", "resource_changes": "delta", "deltas": "delta", "delta": "delta",
                      "adjustments": "delta", "percent_changes": "percent", "percentages": "percent",
                      "percent": "percent", "set_values": "set", "new_values": "set", "absolute_values": "set",
                      "set": "set"}
_IMPACT_KEYS = ("impact", "impacts", "effects", "effect")
_FORBID_KEYS = ("forbidden_modes", "unavailable_modes", "disabled_modes", "blocked_modes", "forbidden")
_TEXT_KEYS = ("description", "text", "summary", "details", "message", "event")
_META_KEYS = ({"event_id", "id", "event_name", "name", "title", "message_to_commander", "trigger_time", "time",
               "timestamp", "requires_replan", "replan", "duration_hours", "duration", "interpretation_notes",
               "raw", "type", "severity", "source", "category", "affected_systems", "resource", "unit"}
              | set(_TEXT_KEYS) | set(_IMPACT_KEYS) | set(_FORBID_KEYS) | set(_CHANGE_CONTAINERS))

# Free-text regexes.
_RES_RE = re.compile(
    r"\b(robot[\s_-]?time|robot[\s_-]?hours|" + "|".join(sorted(_SYNONYMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)
_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])([+-]?)\s?(\d+(?:\.\d+)?)\s*(%|percent\b|per\s?cent\b|pct\b)?",
                     re.IGNORECASE)
_DEC_RE = re.compile(r"\b(decreas\w*|reduc\w*|drop\w*|fall\w*|fell|lose|loses|losing|loss|lost|down|cut\w*|"
                     r"minus|less|fewer|lower\w*|declin\w*|shrink\w*|deplet\w*|remov\w*|sink\w*|plummet\w*)\b",
                     re.IGNORECASE)
_INC_RE = re.compile(r"\b(increas\w*|rise|rises|risen|rising|rose|gain\w*|grow\w*|boost\w*|add\w*|plus|more|"
                     r"extra|up|restor\w*|recover\w*|improv\w*|rais\w*|bonus|surg\w*|jump\w*|climb\w*)\b",
                     re.IGNORECASE)
_SET_TAIL_RE = re.compile(r"(\bto|=|\bset\s+at|\bnow|\bbecomes?|\bonly)\s*$", re.IGNORECASE)
_DURATION_AFTER_RE = re.compile(r"^\s*(hours?|hrs?|h\b|minutes?|mins?|days?|sols?|cycles?|weeks?|seconds?)",
                                re.IGNORECASE)
_SKIP_BEFORE_RE = re.compile(r"(\bhour|\bhr|\bday|\bsol|\bcycle|\bround|\bphase|\bzone|\bbay|\barray|"
                             r"\bsector|#|\bno\.?)\s*$", re.IGNORECASE)
_B_GAP_RE = re.compile(r"^\s*(?:(?:units?|kwh|kw|liters?|litres?|points?|extra|more|additional|less|fewer|"
                       r"of|the|available|colony|total|spare)\s+)*$", re.IGNORECASE)
_FROM_TO_RE = re.compile(r"^\s*to\s+(\d+(?:\.\d+)?)\s*(%|percent\b)?", re.IGNORECASE)
_AND_GAP_RE = re.compile(r"^\s*(?:,|and|&|/|or)\s*(?:available\s+)?$", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<!\d)[.;!?\n]+|[.;!?\n]+(?!\d)")
_MODE_RE = re.compile(r"\b([LMFE][123])\b")
_FORBID_WORDS_RE = re.compile(r"\b(unavailable|forbidden|offline|disabled|banned|not\s+allowed|cannot|can't|"
                              r"impossible|destroyed|not\s+available|blocked)\b", re.IGNORECASE)


def _label(res: str) -> str:
    return RESOURCE_LABELS.get(res, res)


def normalize_resource(name: str) -> str | None:
    """Map a resource name or synonym ("O2", "robot time", "comms") to a canonical resource id."""
    key = re.sub(r"[\s\-]+", "_", str(name).strip().lower())
    if key in _SYNONYMS:
        return _SYNONYMS[key]
    found = {_SYNONYMS[t] for t in re.split(r"[^a-z0-9]+", key) if t in _SYNONYMS}
    return found.pop() if len(found) == 1 else None


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _signed(v: float) -> str:
    return ("+" if v > 0 else "") + _fmt_num(v)


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------
class _Acc:
    def __init__(self) -> None:
        self.deltas: dict[str, float] = {}
        self.percent: dict[str, float] = {}
        self.sets: dict[str, float] = {}
        self.notes: list[str] = []
        self.count = 0

    def add(self, res: str, kind: str, value: float, source: str) -> None:
        self.count += 1
        if kind == "set":
            value = max(0.0, value)
            if res in self.sets:
                self.notes.append(f"{_label(res)} set twice; last value {_fmt_num(value)} wins")
            self.sets[res] = value
            self.notes.append(f"{source} -> set {_label(res)} to {_fmt_num(value)}")
        elif kind == "percent":
            if value < -100:
                self.notes.append(f"{source}: reduction of {_fmt_num(-value)}% capped at 100%")
                value = -100.0
            if res in self.percent:
                self.notes.append(f"Multiple percent changes for {_label(res)} are added together")
            self.percent[res] = self.percent.get(res, 0.0) + value
            self.notes.append(f"{source} -> {_signed(value)}% of {_label(res)}")
        else:
            if res in self.deltas:
                self.notes.append(f"Multiple absolute changes for {_label(res)} are added together")
            self.deltas[res] = self.deltas.get(res, 0.0) + value
            self.notes.append(f"{source} -> {_signed(value)} {_label(res)}")


def _parse_value(value: Any) -> tuple[float, bool, bool] | None:
    """Return (number, is_percent, is_set) for numbers and numeric strings; None otherwise."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value), False, False) if math.isfinite(value) else None
    if isinstance(value, str):
        m = re.match(r"^\s*(=|set\s*(?:to)?|to)?\s*([+-]?\d+(?:\.\d+)?)\s*(%|percent|pct)?", value, re.IGNORECASE)
        if m:
            return float(m.group(2)), bool(m.group(3)), bool(m.group(1))
    return None


def _interpret_key(key: str, value: Any, acc: _Acc, where: str, default: str = "delta",
                   force_pct: bool = False) -> bool:
    """Interpret one `<resource>[_<op>][_<unit>]` key. Returns False if the key names no resource."""
    raw_tokens = [t for t in re.split(r"[^a-z0-9%]+", str(key).lower()) if t]
    res_found = {_SYNONYMS[t] for t in raw_tokens if t in _SYNONYMS}
    if not res_found:
        return False
    src = f"{where}{key}={value!r}"
    if len(res_found) > 1:
        acc.notes.append(f"Ignored {src}: names more than one resource")
        return True
    res = res_found.pop()
    rest = {t for t in raw_tokens if t not in _SYNONYMS}
    unknown = rest - _KEY_WORDS
    if unknown:
        acc.notes.append(f"Ignored {src}: unrecognised word(s) {', '.join(sorted(unknown))}")
        return True
    if isinstance(value, Mapping):
        unit = str(value.get("unit", "")).strip().lower()
        pct = force_pct or unit in _K_PCT
        for sub_key, sub_val in value.items():
            if sub_key == "unit":
                continue
            _interpret_key(f"{res}_{sub_key}", sub_val, acc, f"{where}{key}.", default, pct)
        return True
    parsed = _parse_value(value)
    if parsed is None:
        acc.notes.append(f"Ignored {src}: value is not numeric")
        return True
    num, pct_str, set_str = parsed
    is_pct = force_pct or pct_str or bool(rest & _K_PCT)
    dec, inc = bool(rest & _K_DEC), bool(rest & _K_INC)
    if dec and inc:
        acc.notes.append(f"Ignored {src}: both increase and decrease words")
        return True
    if dec or inc:
        signed = -abs(num) if dec else abs(num)
        if num < 0 and inc:
            acc.notes.append(f"{src}: negative value under an increase key treated as +{_fmt_num(abs(num))}")
        acc.add(res, "percent" if is_pct else "delta", signed, src)
    elif set_str or (rest & _K_SET and not rest & _K_DELTA) or (default == "set" and not rest & _K_DELTA):
        if is_pct:
            acc.add(res, "percent", num - 100.0, f"{src} (to {_fmt_num(num)}% of current)")
        elif num < 0:
            acc.add(res, "delta", num, f"{src} (negative availability read as a change)")
        else:
            acc.add(res, "set", num, src)
    else:
        kind = "percent" if is_pct or (default == "percent" and not rest & _K_ABS) else "delta"
        acc.add(res, kind, num, src)
    return True


def _scan_changes(container: Any, acc: _Acc, where: str, default: str) -> None:
    if isinstance(container, Mapping):
        for k, v in container.items():
            if not _interpret_key(str(k), v, acc, where, default):
                acc.notes.append(f"Ignored {where}{k}: not a known resource")
    elif isinstance(container, list):
        for i, item in enumerate(container):
            if not isinstance(item, Mapping):
                acc.notes.append(f"Ignored {where}[{i}]: not an object")
                continue
            name = item.get("resource") or item.get("name") or item.get("res") or item.get("type")
            res = normalize_resource(name) if name else None
            if res is None:
                acc.notes.append(f"Ignored {where}[{i}]: no recognised resource ({name!r})")
                continue
            unit = str(item.get("unit", "")).strip().lower()
            rest = {k: v for k, v in item.items() if k not in ("resource", "name", "res", "type", "unit")}
            for k, v in rest.items():
                _interpret_key(f"{res}_{k}", v, acc, f"{where}[{i}].", default, unit in _K_PCT)
    elif isinstance(container, str):
        _parse_text(container, acc, [], f"{where.rstrip('.')} text")


def _scan_mapping(data: Mapping[str, Any], acc: _Acc, forbid: list[str], where: str = "") -> None:
    ignored: list[str] = []
    if isinstance(data.get("resource"), str):
        # Single-resource form: {"resource": "power", "change": -4, "unit": "%"}
        op_words = _K_DEC | _K_INC | _K_DELTA | _K_SET | _K_PCT | _K_ABS
        item = {k: v for k, v in data.items()
                if k in ("resource", "unit") or set(re.split(r"[^a-z0-9]+", str(k).lower())) <= op_words}
        # Nested containers are handled by the main loop below; only scalars belong to this item.
        item = {k: v for k, v in item.items() if not isinstance(v, (Mapping, list))}
        _scan_changes([item], acc, where, "delta")
    for key, value in data.items():
        k = str(key).strip().lower().replace(" ", "_").replace("-", "_")
        if k in _CHANGE_CONTAINERS:
            _scan_changes(value, acc, f"{where}{key}.", _CHANGE_CONTAINERS[k])
        elif k in _IMPACT_KEYS:
            if isinstance(value, Mapping):
                _scan_mapping(value, acc, forbid, f"{where}{key}.")
            else:
                _scan_changes(value, acc, f"{where}{key}.", "delta")
        elif k in _FORBID_KEYS:
            items = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value))
            forbid.extend(str(x) for x in items if str(x).strip())
        elif k in _META_KEYS:
            if k == "affected_systems" or (where and k not in ("duration_hours", "duration", "unit")):
                ignored.append(str(key))
        elif not _interpret_key(k, value, acc, where):
            ignored.append(str(key))
    if ignored:
        acc.notes.append(f"Ignored non-resource field(s){' in ' + where.rstrip('.') if where else ''}: "
                         f"{', '.join(ignored)}")


# ---------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------
def _direction(context: str) -> str | None:
    """Last direction word in `context`: 'dec' | 'inc' | None."""
    best: tuple[int, str] | None = None
    for rx, tag in ((_DEC_RE, "dec"), (_INC_RE, "inc")):
        for m in rx.finditer(context):
            if best is None or m.start() > best[0]:
                best = (m.start(), tag)
    return best[1] if best else None


def _parse_text(text: str, acc: _Acc, forbid: list[str], source: str = "text") -> int:
    """Regex-based extraction of resource changes from free text. Returns changes found."""
    found = 0
    for sentence in _SENTENCE_RE.split(text):
        if not sentence.strip():
            continue
        mentions = [(m.start(), m.end(), normalize_resource(m.group(1).replace("-", " ")))
                    for m in _RES_RE.finditer(sentence)]
        mentions = [m for m in mentions if m[2]]
        consumed: set[int] = set()
        last_dir: str | None = None
        last_end = 0
        skip_until = -1
        for nm in _NUM_RE.finditer(sentence):
            n_start, n_end = nm.start(), nm.end()
            after, before = sentence[n_end:], sentence[:n_start]
            if n_start < skip_until or _DURATION_AFTER_RE.match(after) or _SKIP_BEFORE_RE.search(before):
                continue
            sign, num, pct = nm.group(1), float(nm.group(2)), bool(nm.group(3))
            targets: list[int] = []
            outer_ctx = ""      # words before the resource mention, e.g. "reducing" in "reducing power by 10"
            nxt = next((i for i, m in enumerate(mentions) if m[0] >= n_end and i not in consumed), None)
            if nxt is not None and _B_GAP_RE.match(sentence[n_end:mentions[nxt][0]]):
                targets = [nxt]
                # Gap words ("6 extra water") come last so they win over earlier verbs.
                pre_ctx = sentence[max(last_end, n_start - 25):n_start] + " " + sentence[n_end:mentions[nxt][0]]
            else:
                prev = [i for i, m in enumerate(mentions) if m[1] <= n_start and i not in consumed]
                if not prev:
                    acc.notes.append(f"{source}: ignored number {nm.group(0).strip()!r} (no resource nearby)")
                    continue
                targets = [prev[-1]]
                while (len(targets) <= len(prev) - 1 and prev[-len(targets) - 1] == targets[0] - 1
                       and _AND_GAP_RE.match(sentence[mentions[targets[0] - 1][1]:mentions[targets[0]][0]])):
                    targets.insert(0, targets[0] - 1)
                pre_ctx = sentence[mentions[targets[-1]][1]:n_start]
                first = mentions[targets[0]][0]
                outer_ctx = sentence[max(last_end, first - 30):first]
                from_to = _FROM_TO_RE.match(after)
                if from_to and re.search(r"\bfrom\s*$", pre_ctx, re.IGNORECASE):
                    # "Oxygen falls from 59 to 50" -> new availability 50
                    num, pct = float(from_to.group(1)), bool(from_to.group(2))
                    pre_ctx += " to "
                    skip_until = n_end + from_to.end()
            nxt_any = min([m[0] for m in mentions if m[0] >= n_end] + [len(sentence)])
            post_ctx = sentence[n_end:nxt_any]
            set_mode = bool(_SET_TAIL_RE.search(pre_ctx)) and not re.search(r"\bby\s*$", pre_ctx, re.I)
            direction = _direction(pre_ctx)
            how = ""
            if set_mode:
                how = "set"
            elif direction:
                how = direction
            elif sign:
                how = "dec" if sign == "-" else "inc"
            elif _direction(outer_ctx):
                how = _direction(outer_ctx) or ""
            elif _direction(post_ctx):
                how = _direction(post_ctx) or ""
            elif last_dir:
                how = last_dir
                acc.notes.append(f"{source}: no direction for {nm.group(0).strip()!r}; reused previous ({last_dir})")
            else:
                how = "dec"
                acc.notes.append(f"{source}: no direction for {nm.group(0).strip()!r}; assumed a decrease")
            phrase = sentence[mentions[targets[0]][0]:n_end].strip() if mentions[targets[0]][0] < n_start \
                else sentence[n_start:mentions[targets[-1]][1]].strip()
            for t in targets:
                res = mentions[t][2]
                assert res is not None
                src = f"{source} '{phrase}'"
                if how == "set":
                    if pct:
                        acc.add(res, "percent", num - 100.0, f"{src} (to {_fmt_num(num)}% of current)")
                    else:
                        acc.add(res, "set", num, src)
                else:
                    signed = -abs(num) if how == "dec" else abs(num)
                    acc.add(res, "percent" if pct else "delta", signed, src)
                consumed.add(t)
                found += 1
            if how in ("dec", "inc"):
                last_dir = how
            last_end = n_end
        if _FORBID_WORDS_RE.search(sentence):
            for code in _MODE_RE.findall(sentence):
                forbid.append(code)
                acc.notes.append(f"{source}: mode {code} marked unavailable ('{sentence.strip()}')")
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _to_bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() not in ("false", "no", "0", "n", "off", "")


def _hours(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?)?\b", str(v), re.IGNORECASE)
    return float(m.group(1)) if m else None


def _first(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, ""):
            return v
    return None


def _event_id(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, default=str)
    return "EVT-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8].upper()


def _name_from_text(text: str) -> str | None:
    m = re.match(r"^\s*([^:\n—–]{2,60}?)\s*(?::|—|–|\s-\s)\s*\S", text)
    if m and not re.search(r"\d", m.group(1)) and not _RES_RE.fullmatch(m.group(1).strip()):
        return m.group(1).strip()
    return None


def _round_delta(value: float, res: str, notes: list[str]) -> int:
    if float(value).is_integer():
        return int(value)
    out = math.floor(value)  # conservative: larger loss, smaller gain
    notes.append(f"{_label(res)} change {_fmt_num(value)} is fractional; rounded conservatively to {out}")
    return out


def parse_event(data: dict | str) -> CrisisEvent:
    """Parse a judge-supplied event (dict, JSON string, or free text) into a CrisisEvent.

    Raises ValueError only when the input is empty or not a dict/str.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    notes: list[str] = []
    text_only: str | None = None
    if isinstance(data, str):
        s = data.strip()
        if not s:
            raise ValueError("Event input is empty")
        obj: Any = None
        if "{" in s and "}" in s:
            for candidate in (s, s[s.find("{"):s.rfind("}") + 1]):
                try:
                    obj = json.loads(candidate)
                    break
                except (json.JSONDecodeError, ValueError):
                    continue
        if isinstance(obj, dict) and obj:
            notes.append("Parsed JSON object from text input")
            data = obj
        else:
            text_only = s
    elif not isinstance(data, Mapping):
        raise ValueError(f"Event must be a dict or string, got {type(data).__name__}")
    if text_only is None and not data:
        raise ValueError("Event input is empty")

    acc = _Acc()
    forbid: list[str] = []
    if text_only is not None:
        raw: dict[str, Any] = {"text": text_only}
        name = _name_from_text(text_only) or "Unnamed event"
        description = text_only
        requires_replan, message, duration = True, "", None
        _parse_text(text_only, acc, forbid, "text")
        dm = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", text_only, re.IGNORECASE)
        duration = float(dm.group(1)) if dm else None
        event_id = _event_id(raw)
    else:
        assert isinstance(data, Mapping)
        raw = copy.deepcopy(dict(data))
        impact = data.get("impact") if isinstance(data.get("impact"), Mapping) else {}
        name = str(_first(data, ("event_name", "name", "title")) or "Unnamed event")
        description = str(_first(data, ("description", "summary", "details", "text")) or "")
        requires_replan = _to_bool(_first(data, ("requires_replan", "replan")), True)
        message = str(data.get("message_to_commander") or "")
        duration = _hours(_first(impact, ("duration_hours", "duration")) if impact else None)
        if duration is None:
            duration = _hours(_first(data, ("duration_hours", "duration")))
        event_id = str(_first(data, ("event_id", "id")) or _event_id(raw))
        if data.get("trigger_time"):
            notes.append(f"trigger_time {data['trigger_time']!r} recorded (informational)")
        if duration is not None:
            notes.append(f"duration {_fmt_num(duration)} h recorded (does not change the pool)")
        _scan_mapping(data, acc, forbid)
        if acc.count == 0:
            sources = [(k, data.get(k)) for k in (*_TEXT_KEYS, "event_name", "name", "title",
                                                   "message_to_commander")]
            for key, val in sources:
                if isinstance(val, str) and val.strip() and _parse_text(val, acc, forbid, key):
                    notes.append(f"No structured resource change; parsed the {key} text")
                    break
        elif description:
            notes.append("Structured impact used; description text not re-parsed (avoids double counting)")
        if not data.get("event_id") and not data.get("id"):
            notes.append(f"No event_id given; generated {event_id}")
        if name == "Unnamed event":
            notes.append("No event name given; using 'Unnamed event'")

    # Forbidden modes: keep only published codes.
    forbidden: list[str] = []
    for code in forbid:
        c = str(code).strip().upper()
        if c in MODE_PACKAGES:
            if c not in forbidden:
                forbidden.append(c)
        elif c:
            notes.append(f"Dropped unknown forbidden mode code {code!r}")
    if forbidden:
        notes.append(f"Forbidden modes: {', '.join(forbidden)}")

    deltas = {r: _round_delta(v, r, notes) for r, v in acc.deltas.items() if r in RESOURCES}
    sets = {r: math.floor(v) for r, v in acc.sets.items() if r in RESOURCES}
    percent = {r: float(v) for r, v in acc.percent.items() if r in RESOURCES}
    all_notes = notes + acc.notes
    if not (deltas or sets or percent):
        all_notes.append(f"No recognised resource change; pool unchanged (requires_replan={requires_replan})")

    return CrisisEvent(
        event_id=event_id,
        name=name,
        description=description,
        deltas={r: deltas[r] for r in RESOURCES if r in deltas and deltas[r] != 0},
        percent_changes={r: percent[r] for r in RESOURCES if r in percent and percent[r] != 0},
        set_values={r: sets[r] for r in RESOURCES if r in sets},
        forbidden_modes=forbidden,
        requires_replan=requires_replan,
        message_to_commander=message,
        duration_hours=duration,
        interpretation_notes=all_notes,
        raw=raw,
    )


def apply_event(pool: Mapping[str, int], event: CrisisEvent) -> tuple[dict[str, int], list[str]]:
    """Apply set values, then percent changes, then absolute deltas. Never mutates `pool`."""
    new = dict(pool)
    for r in RESOURCES:
        new[r] = int(new.get(r, 0))
    parts: dict[str, list[str]] = {r: [] for r in RESOURCES}
    start = {r: new[r] for r in RESOURCES}

    for r, v in event.set_values.items():
        if r in RESOURCES:
            new[r] = max(0, int(v))
            parts[r].append(f"set to {new[r]}")
    for r, pct in event.percent_changes.items():
        if r not in RESOURCES or not pct:
            continue
        base = new[r]
        frac = Fraction(base) * Fraction(str(abs(pct))) / 100
        diff = -math.ceil(frac) if pct < 0 else math.floor(frac)
        new[r] = max(0, base + diff)
        parts[r].append(f"{_signed(diff) if diff else '0'}, {_signed(pct)}%")
    for r, d in event.deltas.items():
        if r not in RESOURCES or not d:
            continue
        raw_val = new[r] + int(d)
        new[r] = max(0, raw_val)
        parts[r].append(_signed(int(d)))
        if raw_val < 0:
            parts[r].append("clamped at 0")

    changes = [f"{_label(r)} {start[r]} -> {new[r]} ({', '.join(parts[r])})"
               for r in RESOURCES if parts[r]]
    return new, changes
