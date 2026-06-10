"""Seed the 48 World Cup 2026 teams across 12 groups (A-L).

This is an EDITABLE TEMPLATE. The teams/groups below are a plausible field, not the
official final draw — edit TEAMS to match reality, then re-run. Re-running is safe:
existing teams (matched by name) are left as-is, only missing ones are inserted.
"""

from __future__ import annotations

from sqlalchemy import select

from ..db import init_db, session_scope
from ..models import Team

# (group_letter, name, flag_emoji). Hosts: USA, Mexico, Canada.
TEAMS: list[tuple[str, str, str]] = [
    ("A", "Mexico", "🇲🇽"),
    ("A", "South Africa", "🇿🇦"),
    ("A", "South Korea", "🇰🇷"),
    ("A", "Czechia", "🇨🇿"),
    ("B", "Canada", "🇨🇦"),
    ("B", "Switzerland", "🇨🇭"),
    ("B", "Qatar", "🇶🇦"),
    ("B", "Bosnia & Herzegovina", "🇧🇦"),
    ("C", "Brazil", "🇧🇷"),
    ("C", "Morocco", "🇲🇦"),
    ("C", "Haiti", "🇭🇹"),
    ("C", "Scotland", "🏴󠁧󠁢󠁳󠁣󠁴󠁿"),
    ("D", "United States", "🇺🇸"),
    ("D", "Paraguay", "🇵🇾"),
    ("D", "Australia", "🇦🇺"),
    ("D", "Türkiye", "🇹🇷"),
    ("E", "Germany", "🇩🇪"),
    ("E", "Curaçao", "🇨🇼"),
    ("E", "Côte d'Ivoire", "🇨🇮"),
    ("E", "Ecuador", "🇪🇨"),
    ("F", "Netherlands", "🇳🇱"),
    ("F", "Japan", "🇯🇵"),
    ("F", "Tunisia", "🇹🇳"),
    ("F", "Sweden", "🇸🇪"),
    ("G", "Belgium", "🇧🇪"),
    ("G", "Egypt", "🇪🇬"),
    ("G", "Iran", "🇮🇷"),
    ("G", "New Zealand", "🇳🇿"),
    ("H", "Spain", "🇪🇸"),
    ("H", "Cabo Verde", "🇨🇻"),
    ("H", "Saudi Arabia", "🇸🇦"),
    ("H", "Uruguay", "🇺🇾"),
    ("I", "France", "🇫🇷"),
    ("I", "Senegal", "🇸🇳"),
    ("I", "Iraq", "🇮🇶"),
    ("I", "Norway", "🇳🇴"),
    ("J", "Argentina", "🇦🇷"),
    ("J", "Austria", "🇦🇹"),
    ("J", "Algeria", "🇩🇿"),
    ("J", "Jordan", "🇯🇴"),
    ("K", "Portugal", "🇵🇹"),
    ("K", "DR Congo", "🇨🇩"),
    ("K", "Uzbekistan", "🇺🇿"),
    ("K", "Colombia", "🇨🇴"),
    ("L", "England", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    ("L", "Croatia", "🇭🇷"),
    ("L", "Panama", "🇵🇦"),
    ("L", "Ghana", "🇬🇭"),
]



def seed(teams: list[tuple[str, str, str]] = TEAMS) -> int:
    """Insert teams that don't already exist (matched by name). Returns inserted count."""
    init_db()
    inserted = 0
    with session_scope() as session:
        existing = set(session.scalars(select(Team.name)))
        seen: set[str] = set()
        for group, name, flag in teams:
            if name in existing or name in seen:
                continue
            session.add(Team(name=name, group_letter=group, flag_emoji=flag))
            seen.add(name)
            inserted += 1
    return inserted


def main() -> None:
    n = seed()
    print(f"Seeded {n} new team(s).")


if __name__ == "__main__":
    main()
