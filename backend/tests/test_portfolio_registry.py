"""
Unit tests for the portfolio registry's tag-based ranker.
"""

from __future__ import annotations

from backend.outreach.portfolio_registry import (
    PortfolioItem,
    PortfolioRegistry,
    default_registry,
)


def _item(id_: str, *, domain: tuple = (), skill: tuple = ()) -> PortfolioItem:
    return PortfolioItem(
        id=id_,
        title=id_.title(),
        summary="Summary",
        domain_tags=domain,
        skill_tags=skill,
    )


class TestTopMatches:
    def test_domain_match_beats_skill_match(self):
        reg = PortfolioRegistry(items=[
            _item("only_skill", skill=("growth",)),
            _item("only_domain", domain=("fintech",)),
        ])
        out = reg.top_matches(domain_tags=["fintech"], skill_tags=["growth"], limit=2)
        # Domain match weighs double (2×1 + 0 > 0 + 1) — domain wins when tied otherwise.
        assert [i.id for i in out] == ["only_domain", "only_skill"]

    def test_limit_truncates_results(self):
        reg = PortfolioRegistry(items=[
            _item("a", domain=("fintech",)),
            _item("b", domain=("fintech",)),
            _item("c", domain=("fintech",)),
        ])
        assert len(reg.top_matches(domain_tags=["fintech"], limit=2)) == 2

    def test_non_matching_items_excluded(self):
        reg = PortfolioRegistry(items=[
            _item("match", domain=("fintech",)),
            _item("no_match", domain=("gaming",)),
        ])
        out = reg.top_matches(domain_tags=["fintech"])
        assert [i.id for i in out] == ["match"]

    def test_no_tags_returns_empty(self):
        reg = PortfolioRegistry(items=[_item("a", domain=("fintech",))])
        assert reg.top_matches(domain_tags=[], skill_tags=[]) == []

    def test_case_insensitive_match(self):
        reg = PortfolioRegistry(items=[_item("a", domain=("FinTech",))])
        assert reg.top_matches(domain_tags=["fintech"]) == reg.all_items()

    def test_ties_broken_by_registry_order(self):
        reg = PortfolioRegistry(items=[
            _item("first", domain=("fintech",)),
            _item("second", domain=("fintech",)),
        ])
        out = reg.top_matches(domain_tags=["fintech"], limit=2)
        assert [i.id for i in out] == ["first", "second"]


class TestDefaultRegistry:
    def test_default_has_items(self):
        reg = default_registry()
        assert len(reg.all_items()) >= 3

    def test_default_items_have_unique_ids(self):
        ids = [i.id for i in default_registry().all_items()]
        assert len(ids) == len(set(ids))

    def test_lending_job_surfaces_lending_item(self):
        """A lending-tagged search should put the lending case study on top."""
        reg = default_registry()
        top = reg.top_matches(domain_tags=["fintech", "lending"], skill_tags=["0-1"], limit=1)
        assert top and "lending" in top[0].domain_tags
