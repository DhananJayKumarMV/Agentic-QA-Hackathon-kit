"""Test Planner: turns requirements + SiteMap into an executable, validated TestPlan.

Requirement areas are packed into a few batches (one structured LLM call each). The system
prompt is byte-identical across batches (instructions, DSL vocabulary, compact RequirementSet
and SiteMap), so the first call writes the prompt cache and the rest read it concurrently;
only the short user message varies.

Cost notes: output tokens (scenario JSON + thinking) dominate this stage, so the draft schema
carries only what the model must decide. Everything derivable is filled in deterministically:
phase, mutates_state, expected_money_delta (from the balance_delta oracle), snapshot_state
steps, default preconditions, rationale (from the requirement statement) and oracle severity
(from requirement priority unless overridden). The draft is then validated: anything that
would not run (unknown element keys, requirement ids, oracle types, undeclared params) is dropped.
"""
import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from ..cache.artifacts import stable_json
from ..config import REASONING_MODEL
from ..contracts import Oracle, ParamBinding, RequirementSet, Scenario, SiteMap, Step, Target, TestPlan
from ..contracts.scenarios import Action, OracleType
from ..llm.client import LLM, load_prompt

log = logging.getLogger(__name__)

PROMPT_VERSION = "plan-v6"
AGENT = "test_planner"
PLANNABLE = ("automatable", "partially")
BUILTIN_PARAMS = frozenset({"run_tag", "unique_amount"})
MAX_TOKENS = 32000
EFFORT = "medium"
# Areas are packed into batches of at most this many plannable requirements per call:
# fewer calls means less per-call overhead (cache reads, preamble thinking).
BATCH_REQS = 14
_SEVERITY_BY_PRIORITY = {"P0": "critical", "P1": "major", "P2": "minor"}

_ACTIONS = frozenset(get_args(Action))
_ORACLE_TYPES = frozenset(get_args(OracleType))
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_POLARITY_LETTER = {"positive": "P", "negative": "N", "edge": "E"}
_MUTATING_PHASES = ("negative_mutation", "positive_mutation")


# --------------------------------------------------------------------------- draft schema
# Strict, closed schema for structured output. Free-form maps travel as JSON strings
# (structured outputs require closed objects), and action/oracle type are plain strings
# so one bad entry is dropped by validation instead of failing the whole response.

class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftParam(_Draft):
    name: str
    source: Literal["account", "transaction", "balance", "date", "literal", "derived"]
    query_json: str = "{}"


class DraftStep(_Draft):
    action: str
    target: str | None = None  # "key", "key[n]" (nth match) or "key~text" (match containing text)
    value: str | None = None
    note: str


class DraftOracle(_Draft):
    type: str
    args_json: str = "{}"
    severity: Literal["critical", "major", "minor"] | None = None  # None: from requirement priority


class DraftScenario(_Draft):
    requirement_ids: list[str]
    polarity: Literal["positive", "negative", "edge"]
    title: str
    preconditions: list[Literal["logged_in", "logged_out", "fresh_context"]] | None = None
    params: list[DraftParam] = []
    steps: list[DraftStep]
    oracles: list[DraftOracle]


class PlanDraft(_Draft):
    scenarios: list[DraftScenario]


# --------------------------------------------------------------------------- helpers

def sha_of(model: BaseModel) -> str:
    return hashlib.sha256(stable_json(model.model_dump(mode="json")).encode()).hexdigest()


def element_keys(sitemap: SiteMap) -> set[str]:
    keys: set[str] = set()
    for page in sitemap.pages.values():
        keys.update(page.elements.keys())
        for el in page.elements.values():
            keys.add(el.key)
    return keys


def compact(obj: Any) -> Any:
    """Drop null/false/empty values recursively: same information, fewer prompt tokens."""
    if isinstance(obj, dict):
        out = {k: compact(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, False, [], {}, "")}
    if isinstance(obj, list):
        return [compact(v) for v in obj]
    return obj


def _compact_sitemap(sitemap: SiteMap) -> dict[str, Any]:
    data = sitemap.model_dump(mode="json")
    for route, page in data.get("pages", {}).items():
        if page.get("route") == route:
            del page["route"]
        for key, el in page.get("elements", {}).items():
            if el.get("key") == key:
                del el["key"]
    return compact(data)


def render_requirements(reqs: RequirementSet) -> str:
    """One compact block per requirement (no repeated JSON keys); same facts as the JSON form."""
    lines: list[str] = []
    for r in reqs.requirements:
        flags = f"{r.kind}, {r.priority}, {r.testability}" + (", mutates state" if r.mutates_state else "")
        lines.append(f"{r.id} ({r.area}; {flags}): {r.statement}")
        lines.append(f'  quote: "{r.source_quote}"')
        if r.verification_hints:
            lines.append("  hints: " + " | ".join(r.verification_hints))
    return "\n".join(lines)


def build_system_prompt(reqs: RequirementSet, sitemap: SiteMap) -> str:
    """Static across calls: instructions, vocabulary, then the compact inputs (sorted keys).

    Must stay byte-identical for every call of a run (no timestamps, sorted JSON) or the
    prompt cache silently misses.
    """
    keys = sorted(element_keys(sitemap))
    return "\n\n".join([
        load_prompt("plan_system.md").strip(),
        load_prompt("plan_vocabulary.md").strip(),
        "# Valid element keys\n\n" + "\n".join(keys),
        "# Requirements\n\n" + render_requirements(reqs),
        "# SiteMap\n\n" + stable_json(_compact_sitemap(sitemap)),
    ])


def _user_message(label: str, req_ids: list[str]) -> str:
    return (
        f"Plan scenarios for {label}: requirement ids {', '.join(req_ids)}.\n"
        "Cover each listed id with at least one positive scenario plus the negative/edge scenarios "
        "its PRD text and the edge-case catalogue make meaningful. Return only scenarios for these ids."
    )


def _placeholders(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, str):
        found.update(_PLACEHOLDER.findall(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found |= _placeholders(k) | _placeholders(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _placeholders(v)
    return found


_TARGET = re.compile(r"^\s*(?P<key>[^\s\[~]+)\s*(?:\[\s*(?P<nth>-?\d+)\s*\])?\s*(?:~\s*(?P<text>.+?))?\s*$")


def parse_target(spec: str) -> dict[str, Any]:
    """Compact target syntax -> Target dict: "key", "key[2]", "key~Savings", "key[0]~Savings"."""
    m = _TARGET.match(spec)
    if not m:
        raise Rejected(f"bad target {spec!r}")
    out: dict[str, Any] = {"key": m.group("key")}
    if m.group("nth") is not None:
        out["nth"] = int(m.group("nth"))
    if m.group("text"):
        out["text_contains"] = m.group("text")
    return out


def _expand_targets(obj: Any) -> Any:
    """Turn compact string targets inside oracle args into Target dicts."""
    if isinstance(obj, dict):
        return {k: (parse_target(v) if k == "target" and isinstance(v, str) else _expand_targets(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_targets(v) for v in obj]
    return obj


def _target_keys(obj: Any) -> list[str]:
    """Element keys referenced by `target` dicts anywhere inside oracle args."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "target" and isinstance(v, dict) and "key" in v:
                out.append(str(v["key"]))
            else:
                out.extend(_target_keys(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_target_keys(v))
    return out


def _param_refs(params: list[ParamBinding], oracles: list[Oracle], deltas: dict[str, str]) -> set[str]:
    """Param names referenced structurally (not via {{...}})."""
    refs: set[str] = set(deltas)
    for p in params:
        q = p.query
        if p.source == "balance" and isinstance(q.get("of"), str):
            refs.add(q["of"])
        if p.source == "date" and isinstance(q.get("from"), str):
            refs.add(q["from"])
        if p.source == "account" and isinstance(q.get("select"), str) and q["select"].startswith("not="):
            refs.add(q["select"][4:])
        if p.source == "transaction" and isinstance(q.get("where"), dict) and isinstance(q["where"].get("account"), str):
            refs.add(q["where"]["account"])
    for o in oracles:
        if o.type == "balance_delta" and isinstance(o.args.get("deltas"), dict):
            refs.update(o.args["deltas"].keys())
        if o.type == "tx_present_in_histories" and isinstance(o.args.get("accounts"), list):
            refs.update(str(a) for a in o.args["accounts"])
    return refs


class Rejected(ValueError):
    pass


def _severity(req_ids: list[str], priorities: dict[str, str]) -> str:
    ranks = [priorities.get(r, "P1") for r in req_ids]
    return _SEVERITY_BY_PRIORITY[min(ranks)] if ranks else "major"


def _rationale(req_ids: list[str], statements: dict[str, str], polarity: str) -> str:
    text = statements.get(req_ids[0], "").strip()
    if len(text) > 160:
        text = text[:157].rstrip() + "..."
    return f"{polarity.capitalize()} check of {', '.join(req_ids)}: {text}" if text else f"{polarity} check"


def convert(
    draft: DraftScenario,
    *,
    known_reqs: set[str] | dict[str, Any],
    keys: set[str],
) -> Scenario:
    """Validate one draft and convert it to a contract Scenario (id assigned later). Raises Rejected.

    `known_reqs` may map requirement id -> Requirement, which enables derived severity/rationale.
    """
    req_ids = [r for r in dict.fromkeys(draft.requirement_ids) if r in known_reqs]
    if not req_ids:
        raise Rejected(f"no known requirement ids in {draft.requirement_ids}")
    req_map = known_reqs if isinstance(known_reqs, dict) else {}
    priorities = {k: getattr(v, "priority", "P1") for k, v in req_map.items()}
    statements = {k: getattr(v, "statement", "") for k, v in req_map.items()}
    default_severity = _severity(req_ids, priorities)

    params: list[ParamBinding] = []
    for p in draft.params:
        try:
            query = json.loads(p.query_json or "{}")
        except json.JSONDecodeError as e:
            raise Rejected(f"param {p.name}: bad query_json ({e})") from e
        if not isinstance(query, dict):
            raise Rejected(f"param {p.name}: query is not an object")
        params.append(ParamBinding(name=p.name, source=p.source, query=query))
    names = [p.name for p in params]
    if len(names) != len(set(names)):
        raise Rejected("duplicate param names")

    steps: list[Step] = []
    for s in draft.steps:
        if s.action not in _ACTIONS:
            raise Rejected(f"unknown action {s.action!r}")
        target = None
        if s.target:
            t = parse_target(s.target)
            if t["key"] not in keys:
                raise Rejected(f"unknown element key {t['key']!r}")
            target = Target(**t)
        steps.append(Step(action=s.action, target=target, value=s.value, note=s.note))  # type: ignore[arg-type]
    if not steps:
        raise Rejected("no steps")

    oracles: list[Oracle] = []
    for o in draft.oracles:
        if o.type not in _ORACLE_TYPES:
            continue  # drop just this oracle
        try:
            args = json.loads(o.args_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        args = _expand_targets(args)
        bad = [k for k in _target_keys(args) if k not in keys]
        if bad:
            raise Rejected(f"oracle {o.type} targets unknown element keys {bad}")
        oracles.append(Oracle(type=o.type, args=args, severity=o.severity or default_severity))  # type: ignore[arg-type]
    if not oracles:
        raise Rejected("no valid oracles")

    # The PRD-stated money movement is exactly what the balance_delta oracle checks.
    deltas: dict[str, str] = {}
    for o in oracles:
        if o.type == "balance_delta" and isinstance(o.args.get("deltas"), dict):
            deltas.update({str(k): str(v) for k, v in o.args["deltas"].items()})

    declared = set(names) | BUILTIN_PARAMS
    used = _placeholders([s.value for s in steps]) | _placeholders([o.args for o in oracles])
    used |= _placeholders([p.query for p in params]) | _placeholders(deltas)
    used |= _param_refs(params, oracles, deltas)
    missing = sorted(used - declared)
    if missing:
        raise Rejected(f"undeclared params {missing}")

    scn = Scenario(
        id="pending",
        requirement_ids=req_ids,
        polarity=draft.polarity,
        title=draft.title,
        rationale=_rationale(req_ids, statements, draft.polarity),
        preconditions=list(dict.fromkeys(draft.preconditions or [])) or ["logged_in"],
        params=params,
        steps=steps,
        oracles=oracles,
        expected_money_delta=deltas,
    )
    return add_api_cross_check(normalize_state(scn))


_ROW_KEY = re.compile(r"\.rows?$")
_VALUE_ORACLES = {"text_matches", "money_format", "row_count_equals"}


def add_api_cross_check(scn: Scenario) -> Scenario:
    """A displayed row value must match the system of record, not just look well-formed.

    When a read-only scenario inspects list rows without changing any filter input, add an
    unfiltered `rows_equal_api_set` check so displayed rows (and their amounts) are compared
    with the API. Scenarios that fill or select inputs are left alone: their filters are the
    planner's to state.
    """
    if scn.mutates_state or any(o.type == "rows_equal_api_set" for o in scn.oracles):
        return scn
    if any(s.action in ("fill", "select", "submit") for s in scn.steps) or _scoped_page(scn):
        return scn
    inspects_rows = any(
        o.type in _VALUE_ORACLES and _ROW_KEY.search(str((o.args.get("target") or {}).get("key", "")))
        for o in scn.oracles
    )
    if not inspects_rows:
        return scn
    extra = Oracle(type="rows_equal_api_set", args={"filters": {}, "check_amounts": True}, severity="major")
    return scn.model_copy(update={"oracles": [*scn.oracles, extra]})


def _scoped_page(scn: Scenario) -> bool:
    """The scenario opens a parameterised page (e.g. one record's detail view), whose list is
    already narrowed by that parameter, so an unfiltered list-vs-API comparison does not apply."""
    return any(s.action == "goto" and "{{" in (s.value or "") for s in scn.steps)


_AMOUNT_DISPLAY = re.compile(r"\bamounts?\b", re.I)


def flag_amount_display(scn: Scenario, reqs: RequirementSet) -> Scenario:
    """If a covered requirement is about what rows display, including the amount, then every
    unfiltered row-vs-API check in the scenario must also compare the displayed amounts."""
    text = " ".join(f"{r.statement} {r.source_quote}" for r in reqs.requirements if r.id in scn.requirement_ids)
    if not _AMOUNT_DISPLAY.search(text) or _scoped_page(scn):
        return scn
    changed = False
    oracles = []
    for o in scn.oracles:
        filters = o.args.get("filters") or {}
        if o.type == "rows_equal_api_set" and not any(v not in (None, "", []) for v in filters.values()) \
                and not o.args.get("check_amounts"):
            o = o.model_copy(update={"args": {**o.args, "check_amounts": True}})
            changed = True
        oracles.append(o)
    return scn.model_copy(update={"oracles": oracles}) if changed else scn


_LOGOUT_KEY = re.compile(r"log_?out|sign_?out", re.I)
_SUBMIT_KEY = re.compile(r"submit|send|confirm|pay", re.I)


def submits_form(step: Step) -> bool:
    """A step that can write server state (form submission), whatever the model's oracles say."""
    if step.action == "api_post":
        return True
    if step.target is None or step.action not in ("click", "submit"):
        return False
    return step.action == "submit" and "filter" not in step.target.key or bool(_SUBMIT_KEY.search(step.target.key))


def ends_session(step: Step) -> bool:
    return step.action == "logout" or (
        step.action in ("click", "submit") and step.target is not None and bool(_LOGOUT_KEY.search(step.target.key))
    )


def normalize_state(scn: Scenario) -> Scenario:
    """Make mutates_state, phase and snapshot steps consistent."""
    moves_money = any(_nonzero(v) for v in scn.expected_money_delta.values()) or any(
        (o.type == "balance_delta" and any(_nonzero(v) for v in (o.args.get("deltas") or {}).values()))
        or (o.type == "new_tx_count" and _nonzero(o.args.get("n", 0)))
        for o in scn.oracles
    )
    expects_no_change = any(o.type == "no_state_change" for o in scn.oracles)
    state_oracle = expects_no_change or any(o.type in ("new_tx_count", "balance_delta") for o in scn.oracles)
    writes = any(submits_form(s) for s in scn.steps)

    mutates = scn.mutates_state or scn.phase in _MUTATING_PHASES or moves_money or writes or state_oracle
    phase = scn.phase
    if mutates:
        if moves_money:
            phase = "positive_mutation"
        elif expects_no_change:
            phase = "negative_mutation"
        elif phase not in _MUTATING_PHASES:
            phase = "positive_mutation" if scn.polarity == "positive" else "negative_mutation"
    elif phase in _MUTATING_PHASES:
        phase = "read_only"
    if not mutates and phase == "read_only" and any(ends_session(s) for s in scn.steps):
        phase = "session_destructive"

    steps = list(scn.steps)
    if mutates:
        if steps[0].action != "snapshot_state" and not _snapshot_before_first_action(steps):
            steps.insert(0, Step(action="snapshot_state", note="Record balances and transactions before the action"))
        if steps[-1].action != "snapshot_state":
            steps.append(Step(action="snapshot_state", note="Record balances and transactions after the action"))
    return scn.model_copy(update={"mutates_state": mutates, "phase": phase, "steps": steps})


def _nonzero(v: Any) -> bool:
    s = str(v).strip().lstrip("+-")
    if _PLACEHOLDER.search(s):
        return True
    try:
        return float(s) != 0
    except ValueError:
        return bool(s)


def _snapshot_before_first_action(steps: list[Step]) -> bool:
    """True when a snapshot precedes every step that could change state."""
    passive = {"goto", "set_viewport", "wait_for", "reload", "enter_demo", "api_get"}
    for s in steps:
        if s.action == "snapshot_state":
            return True
        if s.action not in passive:
            return False
    return False


def assign_ids(scenarios: list[Scenario]) -> list[Scenario]:
    counters: dict[tuple[str, str], int] = {}
    out = []
    for s in scenarios:
        primary = s.requirement_ids[0]
        stem = primary[4:] if primary.startswith("REQ-") else primary
        letter = _POLARITY_LETTER[s.polarity]
        n = counters.get((stem, letter), 0) + 1
        counters[(stem, letter)] = n
        out.append(s.model_copy(update={"id": f"SCN-{stem}-{letter}{n}"}))
    return out


def build_coverage(plannable: list[str], scenarios: list[Scenario]) -> dict[str, list[str]]:
    cov: dict[str, list[str]] = {r: [] for r in plannable}
    for s in scenarios:
        for r in s.requirement_ids:
            cov.setdefault(r, []).append(s.id)
    return cov


def validate_drafts(
    drafts: list[DraftScenario], *, known_reqs: set[str] | dict[str, Any], keys: set[str],
) -> tuple[list[Scenario], list[str]]:
    kept: list[Scenario] = []
    dropped: list[str] = []
    for d in drafts:
        try:
            kept.append(convert(d, known_reqs=known_reqs, keys=keys))
        except (Rejected, ValidationError) as e:
            dropped.append(f"{d.title}: {e}")
            log.warning("test_planner: dropped draft %r: %s", d.title, e)
    return kept, dropped


# --------------------------------------------------------------------------- entry point

def pack_batches(by_area: dict[str, list[str]], limit: int | None = None) -> list[tuple[list[str], list[str]]]:
    """Group areas into (areas, requirement ids) batches of at most `limit` ids (larger areas stay whole).

    Deterministic (first-fit decreasing, ties by name) so cache keys are stable across runs.
    The smallest batch comes first: it runs alone to write the prompt cache, the rest run concurrently.
    """
    limit = limit or BATCH_REQS
    bins: list[tuple[list[str], list[str]]] = []
    for area in sorted(by_area, key=lambda a: (-len(by_area[a]), a)):
        ids = by_area[area]
        for areas, members in bins:
            if len(members) + len(ids) <= limit:
                areas.append(area)
                members.extend(ids)
                break
        else:
            bins.append(([area], list(ids)))
    for areas, _ in bins:
        areas.sort()
    return sorted(bins, key=lambda b: (len(b[1]), b[0]))


async def plan_tests(reqs: RequirementSet, sitemap: SiteMap, llm: LLM) -> TestPlan:
    reqs_sha = sha_of(reqs)
    map_sha = sha_of(sitemap)
    system = build_system_prompt(reqs, sitemap)
    keys = element_keys(sitemap)
    known = {r.id: r for r in reqs.requirements}

    by_area: dict[str, list[str]] = {}
    for r in reqs.requirements:
        if r.testability in PLANNABLE:
            by_area.setdefault(r.area, []).append(r.id)
    plannable = [rid for ids in by_area.values() for rid in ids]

    async def call(purpose: str, label: str, ids: list[str], part: str) -> list[DraftScenario]:
        draft = await llm.complete(
            agent=AGENT, purpose=purpose, model=REASONING_MODEL, system=system,
            user=_user_message(label, ids), output_model=PlanDraft, effort=EFFORT,
            max_tokens=MAX_TOKENS, cache_parts=(PROMPT_VERSION, reqs_sha, map_sha, part),
        )
        assert isinstance(draft, PlanDraft)
        return draft.scenarios

    def batch_call(areas: list[str], ids: list[str]):
        name = "+".join(areas)
        label = ("area " if len(areas) == 1 else "areas ") + ", ".join(areas)
        return call(f"plan:{name}", label, ids, name)

    batches = pack_batches(by_area)
    drafts: list[DraftScenario] = []
    if batches:
        # First call alone writes the prompt cache; the rest read it concurrently.
        drafts += await batch_call(*batches[0])
        rest = await asyncio.gather(*(batch_call(*b) for b in batches[1:]))
        for chunk in rest:
            drafts += chunk

    scenarios, _dropped = validate_drafts(drafts, known_reqs=known, keys=keys)

    covered = {r for s in scenarios for r in s.requirement_ids}
    missing = [r for r in plannable if r not in covered]
    if missing:
        extra = await call("plan:gapfill", "previously uncovered requirements", missing, "gapfill:" + ",".join(missing))
        more, _ = validate_drafts(extra, known_reqs=known, keys=keys)
        scenarios += more

    scenarios = assign_ids([flag_amount_display(s, reqs) for s in scenarios])
    return TestPlan(
        requirement_set_sha=reqs_sha,
        sitemap_sha=map_sha,
        scenarios=scenarios,
        coverage=build_coverage(plannable, scenarios),
    )
