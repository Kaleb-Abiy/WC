"""Ingest/reconciliation: upsert, manual-override protection, name matching, scoring."""

from __future__ import annotations

from datetime import datetime

from wcsweep.models import MatchStatus, ResultSource, Stage
from wcsweep.services import picks, scoring
from wcsweep.services.results.provider import ProviderMatch, ProviderTeam
from wcsweep.services.results.sync import _normalize, ingest_matches


def _pm(mid, home, away, hs=None, as_=None, status=MatchStatus.FINISHED,
        home_api=None, away_api=None, pens=False, pen_winner=None):
    return ProviderMatch(
        api_match_id=mid,
        stage=Stage.GROUP,
        home=ProviderTeam(api_id=home_api, name=home),
        away=ProviderTeam(api_id=away_api, name=away),
        kickoff_utc=datetime(2026, 6, 11, 19, 0),
        status=status,
        home_score=hs,
        away_score=as_,
        decided_by_pens=pens,
        pen_winner=pen_winner,
    )


def test_normalize_handles_accents_and_aliases():
    assert _normalize("Türkiye") == "turkiye"
    assert _normalize("Turkey") == "turkiye"          # alias
    assert _normalize("USA") == "united states"        # alias
    assert _normalize("Côte d'Ivoire") == "cote d ivoire"
    assert _normalize("Ivory Coast") == "cote d ivoire"  # alias -> matches seed name


def test_creates_matches_and_scores_owned_teams(session, make_player, make_team):
    alice = make_player("Alice")
    brazil = make_team("Brazil")
    france = make_team("France")
    picks.assign(session, alice.id, brazil.id, round_no=1)
    picks.assign(session, alice.id, france.id, round_no=2)

    report = ingest_matches(session, [_pm(10, "Brazil", "France", 2, 1)])
    assert report.created == 1 and report.changed
    board = scoring.leaderboard(session)
    # Brazil win (3) + France loss (0)
    assert board[0].points == 3


def test_manual_result_is_not_overwritten(session, make_player, make_team):
    from wcsweep.models import Match
    from wcsweep.services import game

    a = make_player("Alice")
    brazil = make_team("Brazil")
    france = make_team("France")
    picks.assign(session, a.id, brazil.id, round_no=1)
    picks.assign(session, a.id, france.id, round_no=2)

    # Admin manually sets a result first (this match also has an api id).
    m = Match(api_match_id=10, stage=Stage.GROUP,
              home_team_id=brazil.id, away_team_id=france.id)
    session.add(m)
    session.flush()
    game.set_result(session, m, 5, 0)  # source -> MANUAL

    # Provider later reports a different (wrong) score — must be ignored.
    report = ingest_matches(session, [_pm(10, "Brazil", "France", 1, 1)])
    assert report.skipped_manual == 1
    refreshed = session.get(Match, m.id)
    assert (refreshed.home_score, refreshed.away_score) == (5, 0)
    assert refreshed.source == ResultSource.MANUAL


def test_unmatched_teams_are_reported_not_ingested(session, make_team):
    make_team("Brazil")
    # Atlantis has a real api id -> reported as a (name, id) pair the admin can /mapteam.
    report = ingest_matches(session, [_pm(11, "Brazil", "Atlantis", 1, 0, away_api=999)])
    assert report.created == 0
    assert ("Atlantis", 999) in report.unmatched_teams


def test_placeholder_fixture_is_pending_not_unmatched(session, make_team):
    make_team("Brazil")
    # Knockout TBD: opponent has no api id and no resolvable name -> pending, not unmatched.
    report = ingest_matches(session, [_pm(15, "Brazil", "?", None, None,
                                          status=MatchStatus.SCHEDULED)])
    assert report.created == 0
    assert report.unmatched_teams == []
    assert report.pending_fixtures == 1


def test_cape_verde_variants_match(session, make_team):
    cv = make_team("Cabo Verde")
    other = make_team("Brazil")
    _ = other
    ingest_matches(session, [_pm(16, "Cape Verde Islands", "Brazil", 1, 0,
                                 home_api=321, away_api=654)])
    assert cv.api_team_id == 321  # alias-matched and backfilled


def test_name_match_backfills_api_id(session, make_team):
    t = make_team("Türkiye")
    other = make_team("Brazil")
    _ = other
    ingest_matches(session, [_pm(12, "Turkey", "Brazil", 1, 0, home_api=777, away_api=888)])
    assert t.api_team_id == 777  # backfilled from the name match


def test_newly_finished_tracked_only_on_transition(session, make_player, make_team):
    a = make_player("Alice")
    brazil = make_team("Brazil")
    france = make_team("France")
    picks.assign(session, a.id, brazil.id, round_no=1)
    picks.assign(session, a.id, france.id, round_no=2)

    # First seen as scheduled -> not finished
    r1 = ingest_matches(session, [_pm(13, "Brazil", "France", None, None, status=MatchStatus.SCHEDULED)])
    assert r1.newly_finished == []
    # Now finished -> reported once
    r2 = ingest_matches(session, [_pm(13, "Brazil", "France", 0, 0)])
    assert len(r2.newly_finished) == 1
    # Synced again while still finished -> not re-reported
    r3 = ingest_matches(session, [_pm(13, "Brazil", "France", 0, 0)])
    assert r3.newly_finished == []


def test_penalty_winner_resolved_to_team(session, make_player, make_team):
    a = make_player("Alice")
    brazil = make_team("Brazil")
    france = make_team("France")
    picks.assign(session, a.id, brazil.id, round_no=1)
    picks.assign(session, a.id, france.id, round_no=2)

    # 1-1, France win on pens (AWAY). France should get the WIN (3).
    ingest_matches(session, [_pm(14, "Brazil", "France", 1, 1, pens=True, pen_winner="AWAY")])
    board = {s.player_id: s for s in scoring.leaderboard(session)}
    # Alice owns both: France win (3) + Brazil loss (0) = 3
    assert board[a.id].points == 3
    assert board[a.id].wins == 1
