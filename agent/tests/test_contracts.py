import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from qa_agent.contracts import RequirementSet, ScenarioRun, SiteMap, TestPlan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "filename,model",
    [
        ("requirements.sample.json", RequirementSet),
        ("sitemap.sample.json", SiteMap),
        ("plan.sample.json", TestPlan),
        ("run.sample.json", ScenarioRun),
    ],
)
def test_sample_fixture_validates(filename, model):
    data = json.loads((FIXTURES / filename).read_text())
    obj = model.model_validate(data)
    # Round-trip: what we write is what we read.
    assert model.model_validate_json(obj.model_dump_json()) == obj


def test_extra_fields_rejected():
    data = json.loads((FIXTURES / "requirements.sample.json").read_text())
    data["requirements"][0]["unexpected"] = 1
    with pytest.raises(ValidationError):
        RequirementSet.model_validate(data)


def test_plan_coverage_references_existing_scenarios():
    plan = TestPlan.model_validate_json((FIXTURES / "plan.sample.json").read_text())
    ids = {s.id for s in plan.scenarios}
    for scenario_ids in plan.coverage.values():
        assert set(scenario_ids) <= ids
