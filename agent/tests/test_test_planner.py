import json
from pathlib import Path

import pytest

import qa_agent.agents.test_planner as tp
from qa_agent.agents.test_planner import (
    PROMPT_VERSION,
    DraftScenario,
    build_system_prompt,
    pack_batches,
    parse_target,
    plan_tests,
    sha_of,
)
from qa_agent.config import REASONING_MODEL
from qa_agent.contracts import RequirementSet, SiteMap
from qa_agent.llm.client import FakeLLM

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def one_area_per_call(monkeypatch):
    """The fixtures are tiny, so packing would merge every area; test per-area calls by default."""
    monkeypatch.setattr(tp, "BATCH_REQS", 1)


def _reqs() -> RequirementSet:
    data = json.loads((FIX / "requirements.sample.json").read_text())
    data["requirements"].append({
        "id": "REQ-ACC-001", "area": "access", "statement": "Log out returns to landing.",
        "source_quote": "Logging out returns the user to the landing page.", "kind": "functional",
        "priority": "P0", "testability": "automatable",
    })
    data["requirements"].append({
        "id": "REQ-UI-009", "area": "ui", "statement": "Labels are readable.",
        "source_quote": "readable labels", "kind": "ux", "priority": "P2", "testability": "manual_only",
    })
    return RequirementSet.model_validate(data)


def _sitemap() -> SiteMap:
    data = json.loads((FIX / "sitemap.sample.json").read_text())
    data["pages"]["/transactions"] = {
        "route": "/transactions", "title": "History", "purpose": "List transactions",
        "elements": {
            "history.start_date": {"key": "history.start_date", "label": "Start date"},
            "history.end_date": {"key": "history.end_date", "label": "End date"},
            "history.row": {"key": "history.row", "role": "row", "multiple": True},
        },
    }
    data["pages"]["/"] = {
        "route": "/", "title": "Home", "purpose": "Landing",
        "elements": {"header.logout": {"key": "header.logout", "role": "button", "name": "Log out"}},
    }
    return SiteMap.model_validate(data)


def neg_transfer(**over):
    d = {
        "requirement_ids": ["REQ-TRF-001"], "polarity": "negative", "title": "Zero amount rejected",
        "params": [{"name": "src", "source": "account", "query_json": '{"select": "index=0", "field": "id"}'}],
        "steps": [
            {"action": "goto", "value": "/transfer", "note": "Open the transfer page"},
            {"action": "select", "target": "transfer.source", "value": "{{src}}", "note": "Pick source"},
            {"action": "fill", "target": "transfer.amount", "value": "0", "note": "Enter 0"},
            {"action": "click", "target": "transfer.submit", "note": "Submit"},
        ],
        "oracles": [
            {"type": "no_success_ui", "args_json": "{}", "severity": "critical"},
            {"type": "no_state_change", "args_json": "{}", "severity": "critical"},
        ],
    }
    d.update(over)
    return d


def pos_transfer():
    return {
        "requirement_ids": ["REQ-TRF-001"], "polarity": "positive", "title": "Valid amount moves money",
        "params": [
            {"name": "src", "source": "account", "query_json": '{"select": "index=0", "field": "id"}'},
            {"name": "dst", "source": "account", "query_json": '{"select": "not=src", "field": "id"}'},
        ],
        "steps": [
            {"action": "snapshot_state", "note": "before"},
            {"action": "fill", "target": "transfer.amount", "value": "{{unique_amount}}", "note": "Enter amount"},
            {"action": "click", "target": "transfer.submit", "note": "Submit"},
            {"action": "snapshot_state", "note": "after"},
        ],
        "oracles": [{"type": "balance_delta",
                     "args_json": '{"deltas": {"src": "-{{unique_amount}}", "dst": "{{unique_amount}}"}}'}],
    }


def his_pos():
    return {
        "requirement_ids": ["REQ-HIS-001"], "polarity": "edge", "title": "Same-day range includes record",
        "params": [{"name": "d", "source": "transaction", "query_json": '{"pick": "any", "field": "local_date"}'}],
        "steps": [
            {"action": "goto", "value": "/transactions", "note": "Open history"},
            {"action": "fill", "target": "history.start_date", "value": "{{d}}", "note": "Set start"},
            {"action": "fill", "target": "history.end_date", "value": "{{d}}", "note": "Set end"},
        ],
        "oracles": [{"type": "rows_equal_api_set",
                     "args_json": '{"filters": {"start_date": "{{d}}", "end_date": "{{d}}"}}'}],
    }


def acc_pos():
    return {
        "requirement_ids": ["REQ-ACC-001"], "polarity": "positive", "title": "Logout",
        "steps": [{"action": "click", "target": "header.logout", "note": "Click Log out"}],
        "oracles": [{"type": "url_matches", "args_json": '{"pattern": "^/$"}'}],
    }


def bad_drafts():
    return [
        neg_transfer(title="unknown key", steps=[{"action": "click", "target": "transfer.nope", "note": "x"}]),
        neg_transfer(title="unknown req", requirement_ids=["REQ-XXX-999"]),
        neg_transfer(title="bad oracle only", oracles=[{"type": "made_up", "args_json": "{}"}]),
        neg_transfer(title="undeclared param", steps=[
            {"action": "fill", "target": "transfer.amount", "value": "{{ghost}}", "note": "x"}]),
        neg_transfer(title="bad oracle target", oracles=[
            {"type": "element_visible", "args_json": '{"target": {"key": "nowhere.thing"}}'}]),
        neg_transfer(title="bad action", steps=[{"action": "teleport", "note": "x"}]),
        neg_transfer(title="balance of ghost", params=[
            {"name": "amt", "source": "balance", "query_json": '{"of": "ghost", "plus": "0.01"}'}]),
    ]


def _responses(access, history, transfers, gapfill=None):
    r = {
        ("test_planner", "plan:access"): {"scenarios": access},
        ("test_planner", "plan:history"): {"scenarios": history},
        ("test_planner", "plan:transfers"): {"scenarios": transfers},
    }
    if gapfill is not None:
        r[("test_planner", "plan:gapfill")] = {"scenarios": gapfill}
    return r


async def test_one_call_per_area_with_identical_system_prompt():
    reqs, sm = _reqs(), _sitemap()
    llm = FakeLLM(_responses([acc_pos()], [his_pos()], [neg_transfer(), pos_transfer()]))
    plan = await plan_tests(reqs, sm, llm)

    purposes = sorted(c["purpose"] for c in llm.calls)
    assert purposes == ["plan:access", "plan:history", "plan:transfers"]  # manual_only "ui" area skipped
    systems = {c["system"] for c in llm.calls}
    assert len(systems) == 1
    assert next(iter(systems)) == build_system_prompt(reqs, sm)
    assert all(c["model"] == REASONING_MODEL for c in llm.calls)
    users = {c["purpose"]: c["user"] for c in llm.calls}
    assert "REQ-TRF-001" in users["plan:transfers"] and "REQ-HIS-001" not in users["plan:transfers"]
    assert plan.requirement_set_sha == sha_of(reqs)
    assert plan.sitemap_sha == sha_of(sm)


async def test_system_prompt_contains_inputs_and_catalogue():
    system = build_system_prompt(_reqs(), _sitemap())
    assert "REQ-HIS-001" in system and "transfer.amount" in system
    assert "inclusive" in system and "button_disabled_during" in system and "{{unique_amount}}" in system


async def test_cache_parts_and_effort_passed():
    seen = []

    class Spy(FakeLLM):
        async def complete(self, **kw):
            seen.append(kw)
            return await super().complete(**kw)

    reqs, sm = _reqs(), _sitemap()
    await plan_tests(reqs, sm, Spy(_responses([acc_pos()], [his_pos()], [pos_transfer()])))
    for kw in seen:
        area = kw["purpose"].split(":", 1)[1]
        assert kw["effort"] == tp.EFFORT
        assert kw["cache_parts"] == (PROMPT_VERSION, sha_of(reqs), sha_of(sm), area)


async def test_validation_drops_bad_scenarios_and_fixes_state():
    llm = FakeLLM(_responses([acc_pos()], [his_pos()], [neg_transfer(), pos_transfer(), *bad_drafts()]))
    plan = await plan_tests(_reqs(), _sitemap(), llm)

    titles = {s.title for s in plan.scenarios}
    assert titles == {"Logout", "Same-day range includes record", "Zero amount rejected", "Valid amount moves money"}
    by_title = {s.title: s for s in plan.scenarios}

    neg = by_title["Zero amount rejected"]
    assert neg.mutates_state and neg.phase == "negative_mutation"
    assert neg.steps[0].action == "snapshot_state" and neg.steps[-1].action == "snapshot_state"
    assert neg.params[0].query == {"select": "index=0", "field": "id"}

    pos = by_title["Valid amount moves money"]
    assert pos.phase == "positive_mutation" and pos.mutates_state
    assert pos.expected_money_delta == {"src": "-{{unique_amount}}", "dst": "{{unique_amount}}"}
    assert [s.action for s in pos.steps].count("snapshot_state") == 2  # not duplicated

    his = by_title["Same-day range includes record"]
    assert his.phase == "read_only" and not his.mutates_state

    logout = by_title["Logout"]
    assert logout.phase == "session_destructive"


async def test_unknown_oracle_dropped_but_scenario_kept():
    d = neg_transfer(oracles=[{"type": "made_up", "args_json": "{}"},
                              {"type": "no_success_ui", "args_json": "{}"}])
    plan = await plan_tests(_reqs(), _sitemap(), FakeLLM(_responses([acc_pos()], [his_pos()], [d])))
    scn = next(s for s in plan.scenarios if s.requirement_ids == ["REQ-TRF-001"])
    assert [o.type for o in scn.oracles] == ["no_success_ui"]


async def test_unique_ids_and_coverage():
    llm = FakeLLM(_responses([acc_pos()], [his_pos()], [neg_transfer(), neg_transfer(title="Negative"), pos_transfer()]))
    plan = await plan_tests(_reqs(), _sitemap(), llm)
    ids = [s.id for s in plan.scenarios]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"SCN-ACC-001-P1", "SCN-HIS-001-E1", "SCN-TRF-001-N1", "SCN-TRF-001-N2", "SCN-TRF-001-P1"}
    assert plan.coverage == {
        "REQ-ACC-001": ["SCN-ACC-001-P1"],
        "REQ-HIS-001": ["SCN-HIS-001-E1"],
        "REQ-TRF-001": ["SCN-TRF-001-N1", "SCN-TRF-001-N2", "SCN-TRF-001-P1"],
    }
    assert "REQ-UI-009" not in plan.coverage


async def test_gapfill_not_called_when_covered():
    llm = FakeLLM(_responses([acc_pos()], [his_pos()], [pos_transfer()]))
    await plan_tests(_reqs(), _sitemap(), llm)
    assert "plan:gapfill" not in [c["purpose"] for c in llm.calls]


async def test_gapfill_called_once_for_missing_ids_only():
    # history area returns only invalid scenarios -> REQ-HIS-001 uncovered
    bad_his = his_pos() | {"steps": [{"action": "click", "target": "history.ghost", "note": "x"}]}
    llm = FakeLLM(_responses([acc_pos()], [bad_his], [pos_transfer()], gapfill=[his_pos()]))
    plan = await plan_tests(_reqs(), _sitemap(), llm)
    gap = [c for c in llm.calls if c["purpose"] == "plan:gapfill"]
    assert len(gap) == 1
    assert "REQ-HIS-001" in gap[0]["user"]
    assert "REQ-TRF-001" not in gap[0]["user"] and "REQ-ACC-001" not in gap[0]["user"]
    assert gap[0]["system"] == llm.calls[0]["system"]
    assert plan.coverage["REQ-HIS-001"] == ["SCN-HIS-001-E1"]


async def test_gapfill_still_missing_leaves_empty_coverage():
    llm = FakeLLM(_responses([acc_pos()], [], [pos_transfer()], gapfill=[]))
    plan = await plan_tests(_reqs(), _sitemap(), llm)
    assert plan.coverage["REQ-HIS-001"] == []
    assert sum(c["purpose"] == "plan:gapfill" for c in llm.calls) == 1


def test_draft_schema_is_closed():
    """Structured outputs need closed objects: no free-form dicts in the draft schema."""
    schema = json.dumps(DraftScenario.model_json_schema())
    assert '"additionalProperties": true' not in schema


def test_pack_batches_merges_small_areas_and_puts_smallest_first():
    by_area = {"a": ["1"] * 9, "b": ["2"] * 3, "c": ["3"] * 2, "d": ["4"] * 14, "e": ["5"] * 4}
    batches = pack_batches(by_area, limit=12)
    assert sorted(a for areas, _ in batches for a in areas) == ["a", "b", "c", "d", "e"]
    assert all(len(ids) <= 12 or len(areas) == 1 for areas, ids in batches)
    assert len(batches) == 3  # d alone, a+b, c+e
    assert len(batches[0][1]) == min(len(ids) for _, ids in batches)
    assert pack_batches(by_area, limit=12) == batches  # deterministic


async def test_batched_call_covers_all_areas_in_one_request(monkeypatch):
    monkeypatch.setattr(tp, "BATCH_REQS", 50)
    llm = FakeLLM({("test_planner", "plan:access+history+transfers"): {
        "scenarios": [acc_pos(), his_pos(), pos_transfer()]}})
    plan = await plan_tests(_reqs(), _sitemap(), llm)
    assert [c["purpose"] for c in llm.calls] == ["plan:access+history+transfers"]
    assert {r for s in plan.scenarios for r in s.requirement_ids} == {"REQ-ACC-001", "REQ-HIS-001", "REQ-TRF-001"}


def test_parse_target_compact_syntax():
    assert parse_target("history.row") == {"key": "history.row"}
    assert parse_target("history.row[2]") == {"key": "history.row", "nth": 2}
    assert parse_target("history.row~Savings acct") == {"key": "history.row", "text_contains": "Savings acct"}
    assert parse_target("history.row[0]~x") == {"key": "history.row", "nth": 0, "text_contains": "x"}


async def test_derived_fields_rationale_severity_and_string_targets():
    d = neg_transfer(oracles=[
        {"type": "no_state_change", "args_json": "{}"},
        {"type": "element_visible", "args_json": '{"target": "transfer.amount"}'},
        {"type": "element_visible", "args_json": '{"target": "transfer.amount"}', "severity": "minor"},
    ])
    plan = await plan_tests(_reqs(), _sitemap(), FakeLLM(_responses([acc_pos()], [his_pos()], [d])))
    scn = next(s for s in plan.scenarios if s.requirement_ids == ["REQ-TRF-001"])
    prio = next(r.priority for r in _reqs().requirements if r.id == "REQ-TRF-001")
    expected = {"P0": "critical", "P1": "major", "P2": "minor"}[prio]
    assert [o.severity for o in scn.oracles] == [expected, expected, "minor"]
    assert scn.oracles[1].args == {"target": {"key": "transfer.amount"}}
    assert "REQ-TRF-001" in scn.rationale and scn.rationale.strip()
    assert scn.preconditions == ["logged_in"]


async def test_submit_without_state_oracle_still_treated_as_mutation():
    d = neg_transfer(oracles=[{"type": "no_success_ui", "args_json": "{}"}])
    plan = await plan_tests(_reqs(), _sitemap(), FakeLLM(_responses([acc_pos()], [his_pos()], [d])))
    scn = next(s for s in plan.scenarios if s.requirement_ids == ["REQ-TRF-001"])
    assert scn.mutates_state and scn.phase == "negative_mutation"
    assert scn.steps[0].action == "snapshot_state" and scn.steps[-1].action == "snapshot_state"


def test_system_prompt_is_compact_and_stable():
    a = build_system_prompt(_reqs(), _sitemap())
    assert a == build_system_prompt(_reqs(), _sitemap())
    assert "null" not in a.split("# SiteMap", 1)[1]


def test_row_inspection_gets_api_cross_check():
    from qa_agent.agents.test_planner import add_api_cross_check
    from qa_agent.contracts import Oracle, Scenario, Step, Target

    base = dict(id="S1", requirement_ids=["R1"], polarity="positive", title="Rows show columns", rationale="r")
    rows = Scenario(**base, steps=[Step(action="goto", value="/list", note="Open list")],
                    oracles=[Oracle(type="money_format", args={"target": {"key": "list.row"}})])
    added = add_api_cross_check(rows)
    assert [o.type for o in added.oracles] == ["money_format", "rows_equal_api_set"]
    assert added.oracles[-1].args == {"filters": {}, "check_amounts": True}

    filtered = rows.model_copy(update={"steps": [*rows.steps, Step(action="select", target=Target(key="list.filter"), value="x", note="Pick")]})
    assert add_api_cross_check(filtered).oracles == filtered.oracles

    not_rows = rows.model_copy(update={"oracles": [Oracle(type="money_format", args={"target": {"key": "card.balance"}})]})
    assert add_api_cross_check(not_rows).oracles == not_rows.oracles


def test_amount_requirement_flags_unfiltered_cross_checks():
    from qa_agent.agents.test_planner import flag_amount_display
    from qa_agent.contracts import Oracle, Requirement, RequirementSet, Scenario, Step

    def req(rid, statement):
        return Requirement(id=rid, area="history", statement=statement, source_quote=statement, kind="functional",
                           priority="P1", testability="automatable")
    reqs = RequirementSet(prd_sha256="x", prompt_version="v", requirements=[
        req("R1", "Each row shows date, type, amount and status."), req("R2", "Filters narrow the list.")])
    scn = Scenario(id="S", requirement_ids=["R1"], polarity="positive", title="t", rationale="r",
                   steps=[Step(action="goto", value="/h", note="Open")],
                   oracles=[Oracle(type="rows_equal_api_set", args={"filters": {}}),
                            Oracle(type="rows_equal_api_set", args={"filters": {"type": "deposit"}})])
    out = flag_amount_display(scn, reqs)
    assert out.oracles[0].args["check_amounts"] is True
    assert "check_amounts" not in out.oracles[1].args  # filtered checks are about the filter
    other = scn.model_copy(update={"requirement_ids": ["R2"]})
    assert flag_amount_display(other, reqs) is other


def test_parameterised_pages_get_no_unfiltered_cross_check():
    from qa_agent.agents.test_planner import add_api_cross_check
    from qa_agent.contracts import Oracle, Scenario, Step

    scn = Scenario(id="S", requirement_ids=["R1"], polarity="positive", title="Detail rows", rationale="r",
                   steps=[Step(action="goto", value="/items/{{item}}", note="Open the item")],
                   oracles=[Oracle(type="money_format", args={"target": {"key": "item.row"}})])
    assert add_api_cross_check(scn).oracles == scn.oracles
