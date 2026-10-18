import pytest

from qa_agent.cache.artifacts import ArtifactCache, cache_key
from qa_agent.contracts import RequirementSet
from qa_agent.llm.client import FakeLLM
from qa_agent.llm.cost import BudgetExceeded, CostTracker, price
from qa_agent.contracts import LlmCall


def test_price_sonnet():
    # 1M input + 1M output on Sonnet 5.5 = $2 + $10
    assert price("claude-sonnet-5-5", 1_000_000, 1_000_000, 0, 0) == pytest.approx(12.0)


def test_cache_key_is_order_insensitive_for_dicts():
    assert cache_key({"a": 1, "b": 2}) == cache_key({"b": 2, "a": 1})
    assert cache_key("x") != cache_key("y")


def test_artifact_cache_roundtrip(tmp_path):
    cache = ArtifactCache(tmp_path)
    assert cache.get("ns", "k") is None
    cache.put("ns", "k", {"v": 1})
    assert cache.get("ns", "k") == {"v": 1}
    assert ArtifactCache(tmp_path, enabled=False).get("ns", "k") is None


def test_budget_soft_and_hard_limits():
    t = CostTracker(budget_usd=1.0)
    t.record(LlmCall(agent="a", model="claude-haiku-4-5", purpose="p", usd=0.85))
    t.check(optional=False)  # required calls still allowed above the soft limit
    with pytest.raises(BudgetExceeded):
        t.check(optional=True)
    t.record(LlmCall(agent="a", model="claude-haiku-4-5", purpose="p", usd=0.2))
    with pytest.raises(BudgetExceeded):
        t.check(optional=False)
    assert t.report().degraded


async def test_fake_llm_returns_validated_model():
    payload = {"prd_sha256": "x", "prompt_version": "v", "requirements": []}
    llm = FakeLLM({("spec_analyst", "extract"): payload})
    out = await llm.complete(
        agent="spec_analyst", purpose="extract", model="claude-sonnet-5-5",
        system="s", user="u", output_model=RequirementSet,
    )
    assert isinstance(out, RequirementSet)
    assert llm.tracker.report().by_agent["spec_analyst"]["calls"] == 1


def test_artifact_cache_meta_sidecar(tmp_path):
    cache = ArtifactCache(tmp_path)
    cache.put("ns", "k", {"v": 1}, meta={"usd": 0.12})
    assert cache.get("ns", "k") == {"v": 1}
    assert cache.get_meta("ns", "k") == {"usd": 0.12}
    cache.put("ns", "old", {"v": 2})
    assert cache.get_meta("ns", "old") is None


def test_cold_usd_counts_artifact_hits_at_original_cost():
    t = CostTracker(budget_usd=1.0)
    t.record(LlmCall(agent="planner", model="claude-sonnet-5-5", purpose="p", usd=0.05))
    t.record(LlmCall(agent="planner", model="claude-sonnet-5-5", purpose="q", cached_artifact_hit=True), cold_usd=0.2)
    t.record(LlmCall(agent="explorer", model="claude-haiku-4-5", purpose="l", cached_artifact_hit=True))
    rep = t.report()
    assert rep.total_usd == pytest.approx(0.05)
    assert t.cold_total_usd == pytest.approx(0.25)
    assert rep.by_agent["planner"]["cold_usd"] == pytest.approx(0.25)
    assert rep.by_model["claude-sonnet-5-5"]["cold_usd"] == pytest.approx(0.25)
    assert rep.by_agent["explorer"]["cold_unknown_hits"] == 1


async def test_claude_llm_records_original_cost_on_artifact_hit(tmp_path):
    from qa_agent.llm.client import ClaudeLLM

    cache = ArtifactCache(tmp_path)
    llm = ClaudeLLM(CostTracker(1.0), cache)
    key = cache_key("claude-haiku-4-5", "s", "u", None, "v1")
    cache.put("explorer", key, "cached text", meta={"usd": 0.003})
    out = await llm.complete(agent="explorer", purpose="label", model="claude-haiku-4-5",
                             system="s", user="u", cache_parts=("v1",))
    assert out == "cached text"
    assert llm.tracker.total_usd == 0
    assert llm.tracker.cold_total_usd == pytest.approx(0.003)
