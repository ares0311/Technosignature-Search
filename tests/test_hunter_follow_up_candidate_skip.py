"""Regression: one un-cadenceable candidate must not abort follow-up discovery.

Observed field failure: `Create-New-Search --mode follow-up` aborted with
"no complete later-epoch three-ON cadence was found for HIP40501" at BOTH
--targets 5 and --targets 1, while 803 follow-up entries were eligible. The
rank-1 candidate had no usable cadence and the failure escaped the caller's
per-candidate handler, so no follow-up search could be frozen at all.

Contract section 1 requires returning the best available N and shrinking only
after proving fewer valid candidates exist.
"""

from __future__ import annotations

from typing import Any

import pytest

from techno_search.hunter_follow_up_discovery import (
    FollowUpCandidateUnavailable,
    FollowUpDiscoveryError,
    discover_follow_up_targets,
)


def _candidate(name: str) -> dict[str, Any]:
    return {
        "hip": name,
        "recommended_next_action": "acquire a later epoch ON/OFF cadence",
        "follow_up_priority": 1.0,
    }


class TestExceptionContract:
    def test_candidate_unavailable_is_a_discovery_error_subclass(self) -> None:
        """The per-candidate signal must remain catchable as the base class."""
        assert issubclass(FollowUpCandidateUnavailable, FollowUpDiscoveryError)

    def test_missing_cadence_raises_the_per_candidate_type(self) -> None:
        """The decisive assertion: this condition is per-candidate, not fatal.

        Raising the base class here is what aborted the entire search.
        """
        from techno_search import hunter_follow_up_discovery as module

        def empty_fetcher(_params: dict[str, str]) -> str:
            return ""

        with pytest.raises(FollowUpCandidateUnavailable):
            module._discover_later_cadence(
                target_name="HIP40501",
                prior_max_mjd=59000.0,
                fetcher=empty_fetcher,
                retrieved_at_utc="2026-08-01T00:00:00Z",
            )


class TestDiscoveryContinuesPastAnUnusableCandidate:
    def test_unusable_rank_one_does_not_abort_discovery(self) -> None:
        """A dead rank-1 candidate must be recorded and skipped, not fatal."""
        calls: list[str] = []

        def fetcher(params: dict[str, str]) -> str:
            calls.append(params.get("target", ""))
            return ""  # no products for anyone

        targets = [_candidate("HIP40501"), _candidate("HIP93101")]
        selected, report = discover_follow_up_targets(
            targets, target_count=2, fetcher=fetcher, retrieved_at_utc="2026-08-01T00:00:00Z"
        )

        # Nothing is executable, but discovery completed rather than raising.
        assert selected == []
        unavailable = report.get("unavailable_candidates") or []
        recorded = {str(entry.get("target_id")) for entry in unavailable}
        assert {"HIP40501", "HIP93101"} <= recorded, report
        # DISC-02: every rejection carries a reason.
        assert all(entry.get("reason") for entry in unavailable)

    def test_a_candidate_needing_no_cadence_is_still_returned(self) -> None:
        """Targets whose action needs no cadence bypass discovery entirely."""
        plain = {"hip": "HIP999", "recommended_next_action": "re-score existing data"}

        def fetcher(_params: dict[str, str]) -> str:
            raise AssertionError("must not be consulted for a non-cadence action")

        selected, _ = discover_follow_up_targets(
            [plain], target_count=1, fetcher=fetcher, retrieved_at_utc="2026-08-01T00:00:00Z"
        )
        assert [entry["hip"] for entry in selected] == ["HIP999"]
