
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(
    page_title="HBCC Content Studio",
    page_icon="🏏",
    layout="wide"
)

BASE = "https://grassrootsapiproxy.cricket.com.au/scores"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

GRADES = {
    "Women's 1st XI": (
        "9b70ae63-142e-492b-b266-f42590204e93",
        "121436ac-b8db-40c5-9fdc-2bac4439419a"
    ),
    "Men's 1st XI": (
        "f5b98728-e7b2-40e6-af73-ac2eae56cedf",
        "94ced5f2-a9f0-4ab3-b8aa-c3d0a643deab"
    ),
    "Men's 2nd XI": (
        "d67df9f0-a982-461f-b0ee-4aa6bd89b6a6",
        "74392aeb-a39d-4f11-9d34-8bff6ca4690d"
    )
}

UUID = re.compile(
    r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}"
    r"-[0-9a-fA-F]{12}$"
)

st.markdown(
    """
<style>
.stApp {background:#f5f7fb}
.block-container {max-width:1150px;padding-top:2rem}
.hbhero {
    background:linear-gradient(110deg,#0f192d,#1a3154);
    border-bottom:5px solid #c8102e;
    border-radius:16px;
    padding:32px 36px;
    margin-bottom:24px
}
.hbhero h1 {color:white!important;margin:6px 0 12px}
.hbhero p {color:#e1e7f0;margin:0}
.hbhero small {
    color:#f5b82e;
    font-weight:800;
    letter-spacing:2px
}
.stButton>button[kind="primary"] {
    background:#c8102e;
    border-color:#c8102e;
    color:white
}
</style>
""",
    unsafe_allow_html=True
)

st.markdown(
    '<div class="hbhero">'
    '<small>HAWTHORN BOROONDARA CRICKET CLUB</small>'
    '<h1>Content Studio</h1>'
    '<p>Match reports, weekend wrap-ups and '
    'published team selections.</p>'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def name_of(value):
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        for key in (
            "displayName",
            "fullName",
            "name",
            "playerName",
            "participantName",
            "teamName",
            "shortName",
            "playerShortName",
            "title"
        ):
            result = value.get(key)
            if isinstance(result, str) and result.strip():
                return result.strip()

        for key in ("player", "participant", "team", "person"):
            if isinstance(value.get(key), dict):
                result = name_of(value[key])
                if result:
                    return result

    return ""


def parse_date(value):
    """
    Read dates from PlayCricket matchSchedule,
    including nested values and unknown field names.
    """

    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, (int, float)):
        if value < 100000000:
            return None

        try:
            stamp = (
                value / 1000
                if value > 100000000000
                else value
            )
            return datetime.fromtimestamp(
                stamp,
                tz=timezone.utc
            ).date()
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        match = re.search(
            r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)",
            value
        )

        if match:
            try:
                return date(
                    *(int(x) for x in match.groups())
                )
            except ValueError:
                pass

        return None

    if isinstance(value, list):
        for item in value:
            parsed = parse_date(item)
            if parsed:
                return parsed
        return None

    if isinstance(value, dict):
        for year_key, month_key, day_key in (
            ("year", "month", "day"),
            ("startYear", "startMonth", "startDay")
        ):
            if all(
                key in value
                for key in (year_key, month_key, day_key)
            ):
                try:
                    return date(
                        int(value[year_key]),
                        int(value[month_key]),
                        int(value[day_key])
                    )
                except (TypeError, ValueError):
                    pass

        keys = (
            "matchSchedule",
            "startDateTime",
            "startDate",
            "matchDate",
            "date",
            "scheduledStart",
            "scheduledDate",
            "startTime",
            "start",
            "matchStartDate",
            "matchStartDateTime",
            "days",
            "dates",
            "firstDay",
            "dateTime",
            "utcStartTime",
            "localStartTime",
            "startDateTimeLocal"
        )

        for key in keys:
            if key in value:
                parsed = parse_date(value[key])
                if parsed:
                    return parsed

        # Check values under unexpected field names too.
        for item in value.values():
            if isinstance(
                item,
                (str, int, float, dict, list)
            ):
                parsed = parse_date(item)
                if parsed:
                    return parsed

    return None


def match_id(obj):
    if not isinstance(obj, dict):
        return None

    for key in ("id", "matchId", "matchID"):
        value = obj.get(key)
        if isinstance(value, str) and UUID.fullmatch(value):
            return value

    return None


def teams_of(obj):
    if not isinstance(obj, dict):
        return []

    for key in ("teams", "teamSummaries"):
        if isinstance(obj.get(key), list):
            return [
                value for value in obj[key]
                if isinstance(value, dict)
            ]

    return [
        obj[key]
        for key in ("homeTeam", "awayTeam")
        if isinstance(obj.get(key), dict)
    ]


def hbcc_team(obj, expected_id):
    if not isinstance(obj, dict):
        return False

    ids = [
        str(obj.get(key) or "")
        for key in ("id", "teamId", "clubTeamId")
    ]

    name = name_of(obj).lower()

    return (
        (bool(expected_id) and expected_id in ids)
        or "hawthorn boroondara" in name
        or name == "hb hawks"
    )


def fixture_name(obj):
    names = [
        name_of(team)
        for team in teams_of(obj)
    ]
    names = [name for name in names if name]

    if len(names) >= 2:
        return " vs ".join(names[:2])

    return name_of(obj) or "Fixture"


def venue_of(obj):
    if not isinstance(obj, dict):
        return ""

    for key in (
        "venue",
        "ground",
        "venueName",
        "groundName"
    ):
        value = obj.get(key)
        if value:
            return name_of(value) or str(value)

    return ""


# ============================================================
# PLAYCRICKET API
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def fetch(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def grade_matches(grade_id):
    return fetch(
        f"{BASE}/grades/{grade_id}/matches"
        "?jsconfig=eccn%3Atrue"
    )


def match_detail(mid):
    return fetch(
        f"{BASE}/matches/{mid}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )


def match_balls(mid):
    return fetch(
        f"{BASE}/matches/{mid}/balls"
        "?jsconfig=eccn%3Atrue"
    )


def match_url(mid):
    return f"https://play.cricket.com.au/match/{mid}"


def collect_matches(payload):
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("matches"), list)
    ):
        return [
            match
            for match in payload["matches"]
            if isinstance(match, dict)
        ]

    found = {}

    def walk(value):
        if isinstance(value, dict):
            mid = match_id(value)

            if mid:
                found[mid] = value

            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    walk(nested)

        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)
    return list(found.values())


def fixture_from(record, grade, expected_id, detail=None):
    summary = (detail or {}).get("matchSummary") or {}

    when = (
        parse_date(record.get("matchSchedule"))
        or parse_date(record)
        or parse_date(summary)
    )

    label = fixture_name(record)

    if label == "Fixture":
        label = fixture_name(summary)

    mid = match_id(record)

    return {
        "id": mid,
        "grade": grade,
        "team_id": expected_id,
        "date": str(when) if when else "",
        "name": label,
        "venue": (
            venue_of(record)
            or venue_of(summary)
            or venue_of(detail)
        ),
        "url": match_url(mid)
    }


def manual_ids(text):
    ids = []

    for token in re.split(r"[\s,]+", text):
        match = re.search(
            r"/match/([0-9a-fA-F-]{36})",
            token
        )

        mid = match.group(1) if match else token

        if UUID.fullmatch(mid) and mid not in ids:
            ids.append(mid)

    return ids


# ============================================================
# PUBLISHED TEAM SELECTIONS
# ============================================================

SELECTION_KEYS = (
    "selectedPlayers",
    "teamSelection",
    "teamSelections",
    "lineup",
    "lineUp",
    "playingXI",
    "players",
    "participants",
    "selectedParticipants",
    "squad"
)

PLAYER_ID_KEYS = (
    "playerId",
    "participantId",
    "id"
)


def player_name(item):
    if isinstance(item, str):
        return item.strip()

    if not isinstance(item, dict):
        return ""

    return name_of(item)


def captain_flag(item):
    if not isinstance(item, dict):
        return False

    for key in (
        "isCaptain",
        "captain",
        "teamCaptain",
        "isTeamCaptain"
    ):
        if item.get(key) is True:
            return True

    for key in (
        "role",
        "teamRole",
        "playerRole",
        "designation"
    ):
        role = item.get(key)

        if isinstance(role, dict):
            role = name_of(role)

        if (
            isinstance(role, str)
            and role.strip().lower()
            in ("captain", "c", "skipper")
        ):
            return True

    return False


def captain_references(team):
    refs = set()

    for key in (
        "captainId",
        "captainPlayerId",
        "captainParticipantId",
        "teamCaptainId",
        "captainName"
    ):
        value = team.get(key)

        if (
            isinstance(value, (str, int))
            and str(value).strip()
        ):
            refs.add(str(value).strip().lower())

    captain = team.get("captain")

    if isinstance(captain, dict):
        for key in (
            "id",
            "playerId",
            "participantId",
            "displayName",
            "name"
        ):
            if captain.get(key):
                refs.add(
                    str(captain[key]).strip().lower()
                )

    elif isinstance(captain, str) and captain.strip():
        refs.add(captain.strip().lower())

    return refs


def parse_player_list(items, team):
    if isinstance(items, dict):
        for key in SELECTION_KEYS:
            if isinstance(items.get(key), list):
                items = items[key]
                break

    if not isinstance(items, list):
        return []

    captain_refs = captain_references(team)

    result = []
    seen = set()

    for item in items:
        name = player_name(item)

        if (
            not name
            or "*" in name
            or name.lower() == "private player"
        ):
            continue

        clean_name = re.sub(
            r"\s*\(c\)\s*$",
            "",
            name,
            flags=re.I
        ).strip()

        refs = {
            name.lower(),
            clean_name.lower()
        }

        if isinstance(item, dict):
            refs.update(
                str(item[key]).lower()
                for key in PLAYER_ID_KEYS
                if item.get(key)
            )

            for nested_key in ("player", "participant"):
                nested = item.get(nested_key)

                if isinstance(nested, dict):
                    refs.update(
                        str(nested[key]).lower()
                        for key in PLAYER_ID_KEYS
                        if nested.get(key)
                    )

        captain = (
            captain_flag(item)
            or bool(refs & captain_refs)
            or bool(re.search(r"\(c\)", name, re.I))
        )

        if clean_name.lower() in seen:
            continue

        seen.add(clean_name.lower())

        result.append(
            clean_name + (" (c)" if captain else "")
        )

    return result


def selection_candidates(detail, expected_id):
    """
    Find player lists belonging to identifiable HBCC teams.

    Published-selection fields receive priority.
    General participant lists are shown as alternatives.
    """

    candidates = []
    seen = set()

    skip = {
        "scorecard",
        "batting",
        "bowling",
        "innings",
        "balls",
        "deliveries",
        "scorecards"
    }

    def inspect_team(team, path):
        if not hbcc_team(team, expected_id):
            return

        for key in SELECTION_KEYS:
            if key not in team:
                continue

            players = parse_player_list(
                team[key],
                team
            )

            if not players:
                continue

            source = f"{path}.{key}"
            signature = (source, tuple(players))

            if signature in seen:
                continue

            seen.add(signature)

            explicit = key not in (
                "players",
                "participants",
                "squad"
            )

            score = (
                (100 if explicit else 50)
                + len(players)
                + (
                    15
                    if any("(c)" in p for p in players)
                    else 0
                )
            )

            candidates.append({
                "source": source,
                "players": players,
                "score": score,
                "explicit": explicit
            })

    def walk(obj, path="root", depth=0):
        if depth > 14:
            return

        if isinstance(obj, dict):
            if hbcc_team(obj, expected_id):
                inspect_team(obj, path)

            for key, value in obj.items():
                if key in skip:
                    continue

                if isinstance(value, (dict, list)):
                    walk(
                        value,
                        f"{path}.{key}",
                        depth + 1
                    )

        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                if isinstance(value, (dict, list)):
                    walk(
                        value,
                        f"{path}[{index}]",
                        depth + 1
                    )

    walk(detail)

    candidates.sort(
        key=lambda item: (
            item["score"],
            len(item["players"])
        ),
        reverse=True
    )

    return candidates


def selection_output(rows):
    lines = [
        "HAWTHORN BOROONDARA CRICKET CLUB",
        "TEAM SELECTIONS",
        ""
    ]

    for row in rows:
        lines.extend([
            row["grade"].upper(),
            row["name"],
            f"Date: {row['date']}",
            f"Venue: {row['venue'] or 'Not available'}",
            ""
        ])

        if row["players"]:
            lines.extend(
                f"{index}. {name}"
                for index, name in enumerate(
                    row["players"],
                    1
                )
            )
        else:
            lines.append(
                "Selection not verified — check PlayCricket"
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# SCORECARD AND BALL-BY-BALL
# ============================================================

def innings_summary(detail):
    output = []

    def walk(value):
        if isinstance(value, dict):
            if (
                isinstance(value.get("batting"), list)
                and isinstance(value.get("bowling"), list)
            ):
                batting = [
                    {
                        "name": name_of(
                            player.get("playerShortName")
                            or player.get("playerName")
                            or player.get("player")
                        ),
                        "runs": player.get("runsScored"),
                        "balls": player.get("ballsFaced"),
                        "dismissal": player.get("dismissalText")
                    }
                    for player in value["batting"]
                    if isinstance(player, dict)
                ]

                bowling = [
                    {
                        "name": name_of(
                            player.get("playerShortName")
                            or player.get("playerName")
                            or player.get("player")
                        ),
                        "wickets": player.get("wicketsTaken"),
                        "runs": player.get("runsConceded"),
                        "overs": player.get("oversBowled")
                    }
                    for player in value["bowling"]
                    if isinstance(player, dict)
                ]

                output.append({
                    "runs": value.get("runsScored"),
                    "wickets": value.get(
                        "numberOfWicketsFallen"
                    ),
                    "batting": [
                        player
                        for player in batting
                        if player["name"]
                        and "*" not in player["name"]
                    ],
                    "bowling": [
                        player
                        for player in bowling
                        if player["name"]
                        and "*" not in player["name"]
                    ]
                })

                return

            for nested in value.values():
                walk(nested)

        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(detail)
    return output


def ball_highlights(data):
    events = []

    def walk(value):
        if isinstance(value, dict):
            required = {
                "overNumber",
                "ballNumber",
                "progressRuns"
            }

            if required <= value.keys():
                if (
                    value.get("dismissedParticipantId")
                    or (value.get("runsBat") or 0) >= 4
                ):
                    events.append({
                        "over": value.get("overNumber"),
                        "ball": value.get("ballNumber"),
                        "score": value.get("progressRuns"),
                        "wickets": value.get(
                            "progressWickets"
                        ),
                        "runsBat": value.get("runsBat"),
                        "wicket": bool(
                            value.get(
                                "dismissedParticipantId"
                            )
                        ),
                        "batter": value.get(
                            "strikerShortName"
                        ),
                        "bowler": value.get(
                            "bowlerShortName"
                        )
                    })

                return

            for nested in value.values():
                walk(nested)

        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(data)
    return events[:100]


def report_data(fixture):
    detail = match_detail(fixture["id"])
    summary = detail.get("matchSummary") or {}

    result = {
        "fixture": fixture["name"],
        "grade": fixture["grade"],
        "date": fixture["date"],
        "venue": fixture["venue"],
        "result": summary.get("resultText"),
        "teams": teams_of(summary),
        "innings": innings_summary(detail)
    }

    try:
        result["key_balls"] = ball_highlights(
            match_balls(fixture["id"])
        )
    except Exception:
        result["key_balls"] = "Unavailable"

    return result


# ============================================================
# GEMINI REPORT GENERATION
# ============================================================

def generate_article(
    fixtures,
    mode,
    target,
    context,
    avoid
):
    key = st.secrets.get("GEMINI_API_KEY", "")

    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from Streamlit Secrets."
        )

    data = [
        report_data(fixture)
        for fixture in fixtures
    ]

    client = genai.Client(api_key=key)

    prompt = f"""
You are the cricket reporter for Hawthorn Boroondara
Cricket Club (HB Hawks).

Write a complete {mode.lower()} of approximately
{target} words in Australian English.

Start with a compelling headline.

Tell the story using the scores, batting, bowling
and verified ball-by-ball moments.

For a weekend report:
- Cover every supplied fixture.
- Create a cohesive club-wide narrative.
- Include sections for each grade.

Only use supplied facts.

Never invent:
- Results
- Partnerships
- Quotes
- Weather
- Pitch conditions
- Tactics
- Player identities
- Injuries

Treat official result text as authoritative.
Never infer a result from an unfinished game.

Additional context:
{context}

Things to avoid:
{avoid}

Match data:
{json.dumps(data, ensure_ascii=False, default=str)}

Return only the finished article.
"""

    last_error = None

    for model in (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ):
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.35,
                        max_output_tokens=6500
                    )
                )

                if response.text and response.text.strip():
                    return response.text.strip()

            except Exception as error:
                last_error = error
                message = str(error).lower()

                if any(
                    term in message
                    for term in (
                        "429",
                        "503",
                        "unavailable",
                        "resource_exhausted"
                    )
                ):
                    time.sleep(2 + attempt * 2)
                    continue

                if (
                    "404" in message
                    or "not found" in message
                ):
                    break

                raise

    raise RuntimeError(
        "Gemini could not generate the article: "
        + str(last_error)
    )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("HBCC Grades")

    st.caption(
        "Women's 1st XI, Men's 1st XI and "
        "Men's 2nd XI are preconfigured."
    )

    additional = st.text_area(
        "Additional grades",
        placeholder="Men's 3rd XI | GRADE_ID | TEAM_ID",
        height=100
    )

    if st.button("Clear PlayCricket cache"):
        st.cache_data.clear()
        st.success("Cache cleared")

    st.caption(
        "Format: Grade name | Grade ID | Team ID"
    )


all_grades = dict(GRADES)

for line in additional.splitlines():
    parts = [
        part.strip()
        for part in line.split("|")
    ]

    if (
        len(parts) == 3
        and UUID.fullmatch(parts[1])
        and UUID.fullmatch(parts[2])
    ):
        all_grades[parts[0]] = (
            parts[1],
            parts[2]
        )


# ============================================================
# GENERATOR SETTINGS
# ============================================================

mode = st.radio(
    "What would you like to create?",
    [
        "Match Report",
        "Weekend Report",
        "Team Selections"
    ],
    horizontal=True
)

left, right = st.columns(2)

with left:
    st.subheader("1. Choose dates")

    date_mode = st.radio(
        "Date selection",
        [
            "Date range",
            "Individual dates"
        ],
        horizontal=True
    )

    dates = set()

    if date_mode == "Date range":
        chosen = st.date_input(
            "Match date range",
            value=(
                date.today(),
                date.today() + timedelta(days=1)
            )
        )

        if (
            isinstance(chosen, (list, tuple))
            and len(chosen) == 2
        ):
            start, end = chosen

            if 0 <= (end - start).days <= 45:
                dates = {
                    start + timedelta(days=i)
                    for i in range(
                        (end - start).days + 1
                    )
                }
            else:
                st.warning(
                    "Choose a range of 46 days or less."
                )

    else:
        count = st.number_input(
            "Number of dates",
            min_value=1,
            max_value=10,
            value=1
        )

        for i in range(count):
            dates.add(
                st.date_input(
                    f"Date {i + 1}",
                    value=date.today() + timedelta(days=i),
                    key=f"date_{i}"
                )
            )

with right:
    st.subheader("2. Choose grades")

    selected_grades = st.multiselect(
        "HBCC grades",
        list(all_grades),
        default=list(GRADES)
    )


with st.expander(
    "Optional: Enter PlayCricket match URLs manually"
):
    manual_urls = st.text_area(
        "One match link per line",
        height=90
    )


# ============================================================
# SESSION STATE
# ============================================================

for key, default in (
    ("fixtures", []),
    ("diagnostics", []),
    ("schedule_samples", []),
    ("article", ""),
    ("selection_rows", [])
):
    if key not in st.session_state:
        st.session_state[key] = default


# ============================================================
# SEARCH FIXTURES
# ============================================================

if st.button(
    "🔎 Find HBCC fixtures",
    type="primary",
    use_container_width=True,
    disabled=not dates or not selected_grades
):
    found = {}
    diagnostics = []
    schedule_samples = []

    with st.spinner("Searching PlayCricket..."):

        for grade in selected_grades:
            gid, tid = all_grades[grade]

            try:
                payload = grade_matches(gid)
                records = collect_matches(payload)

                if records:
                    schedule_samples.append({
                        "grade": grade,
                        "match_id": records[0].get("id"),
                        "matchSchedule": records[0].get(
                            "matchSchedule"
                        ),
                        "parsed_date": str(
                            parse_date(
                                records[0].get("matchSchedule")
                            )
                        )
                    })

                stats = {
                    "grade": grade,
                    "records_identified": len(records),
                    "without_date": 0,
                    "outside_dates": 0,
                    "other_team": 0,
                    "matched": 0
                }

                for record in records:
                    mid = match_id(record)

                    if not mid:
                        continue

                    # Read matchSchedule FIRST.
                    when = (
                        parse_date(
                            record.get("matchSchedule")
                        )
                        or parse_date(record)
                    )

                    detail = None

                    if not when:
                        try:
                            detail = match_detail(mid)

                            when = (
                                parse_date(
                                    detail.get("matchSummary")
                                )
                                or parse_date(detail)
                            )
                        except Exception:
                            pass

                    if not when:
                        stats["without_date"] += 1
                        continue

                    if when not in dates:
                        stats["outside_dates"] += 1
                        continue

                    teams = (
                        teams_of(record)
                        or teams_of(
                            (detail or {}).get(
                                "matchSummary"
                            )
                        )
                    )

                    if teams and not any(
                        hbcc_team(team, tid)
                        for team in teams
                    ):
                        stats["other_team"] += 1
                        continue

                    if mid not in found:
                        found[mid] = fixture_from(
                            record,
                            grade,
                            tid,
                            detail
                        )

                        stats["matched"] += 1

                diagnostics.append(stats)

            except Exception as error:
                diagnostics.append({
                    "grade": grade,
                    "error": str(error)
                })

        # Manual match URL fallback
        for mid in manual_ids(manual_urls):
            if mid in found:
                continue

            try:
                detail = match_detail(mid)
                summary = detail.get("matchSummary") or {}

                teams = (
                    teams_of(summary)
                    or teams_of(detail)
                )

                grade = next(
                    (
                        grade
                        for grade in selected_grades
                        if any(
                            hbcc_team(
                                team,
                                all_grades[grade][1]
                            )
                            for team in teams
                        )
                    ),
                    selected_grades[0]
                )

                record = dict(summary or detail)
                record["id"] = mid

                found[mid] = fixture_from(
                    record,
                    grade,
                    all_grades[grade][1],
                    detail
                )

            except Exception as error:
                diagnostics.append({
                    "manual_match": mid,
                    "error": str(error)
                })

    st.session_state.fixtures = sorted(
        found.values(),
        key=lambda fixture: (
            fixture["date"],
            fixture["grade"]
        )
    )

    st.session_state.diagnostics = diagnostics
    st.session_state.schedule_samples = schedule_samples
    st.session_state.selection_rows = []
    st.session_state.article = ""


# ============================================================
# REVIEW FIXTURES
# ============================================================

st.subheader("3. Review fixtures")

chosen = []

if st.session_state.fixtures:
    st.success(
        f"{len(st.session_state.fixtures)} fixture(s) found."
    )

    for fixture in st.session_state.fixtures:
        label = (
            f"{fixture['date']} · "
            f"{fixture['grade']} · "
            f"{fixture['name']}"
        )

        if st.checkbox(
            label,
            value=True,
            key="fixture_" + fixture["id"]
        ):
            chosen.append(fixture)

        st.caption(
            f"[View on PlayCricket]({fixture['url']})"
        )

else:
    st.info(
        "Select dates and grades, "
        "then click Find HBCC fixtures."
    )


# ============================================================
# DIAGNOSTICS
# ============================================================

if st.session_state.diagnostics:

    with st.expander(
        "Fixture search diagnostics",
        expanded=not bool(
            st.session_state.fixtures
        )
    ):
        st.json(
            st.session_state.diagnostics
        )

    with st.expander(
        "Actual PlayCricket matchSchedule",
        expanded=not bool(
            st.session_state.fixtures
        )
    ):
        st.json(
            st.session_state.schedule_samples
        )

st.divider()


# ============================================================
# MATCH REPORT AND WEEKEND REPORT
# ============================================================

if mode in (
    "Match Report",
    "Weekend Report"
):
    st.subheader("4. Generate article")

    if mode == "Match Report" and chosen:
        picked = st.selectbox(
            "Choose one match",
            chosen,
            format_func=lambda fixture: (
                f"{fixture['grade']} — {fixture['name']}"
            )
        )

        report_fixtures = [picked]

    else:
        report_fixtures = chosen

    context = st.text_area(
        "Additional context (optional)"
    )

    avoid = st.text_area(
        "Anything to avoid (optional)",
        height=70
    )

    length = st.select_slider(
        "Target words",
        options=[
            300,
            500,
            700,
            1000,
            1500
        ],
        value=(
            500
            if mode == "Match Report"
            else 1000
        )
    )

    if st.button(
        "Generate " + mode,
        type="primary",
        disabled=not report_fixtures
    ):
        try:
            with st.spinner(
                "Reading scorecards and generating report..."
            ):
                st.session_state.article = (
                    generate_article(
                        report_fixtures,
                        mode,
                        length,
                        context,
                        avoid
                    )
                )

        except Exception as error:
            st.error(str(error))

    if st.session_state.article:
        st.subheader("Generated article")

        st.markdown(
            st.session_state.article
        )

        st.text_area(
            "Copy article",
            value=st.session_state.article,
            height=340
        )

        st.download_button(
            "Download article",
            st.session_state.article,
            "hbcc_report.txt",
            "text/plain"
        )


# ============================================================
# TEAM SELECTION GENERATOR
# ============================================================

else:
    st.subheader(
        "4. Retrieve published team selections"
    )

    if st.button(
        "Retrieve selected teams",
        type="primary",
        disabled=not chosen
    ):
        rows = []

        with st.spinner(
            "Checking published team selections..."
        ):
            for fixture in chosen:
                row = dict(fixture)
                row["candidates"] = []
                row["error"] = ""

                try:
                    detail = match_detail(
                        fixture["id"]
                    )

                    row["candidates"] = (
                        selection_candidates(
                            detail,
                            fixture["team_id"]
                        )
                    )

                    row["venue"] = (
                        row["venue"]
                        or venue_of(
                            detail.get("matchSummary") or {}
                        )
                    )

                except Exception as error:
                    row["error"] = str(error)

                rows.append(row)

        st.session_state.selection_rows = rows

    if st.session_state.selection_rows:
        edited_rows = []

        for row in st.session_state.selection_rows:
            mid = row["id"]

            with st.container(border=True):
                st.markdown(
                    f"**{row['grade']} — {row['name']}**"
                )

                st.caption(
                    f"{row['date']} · "
                    f"{row['venue'] or 'Venue unavailable'}"
                )

                if row["error"]:
                    st.error(
                        "Unable to retrieve selections: "
                        + row["error"]
                    )

                candidates = row["candidates"]

                if candidates:
                    options = list(
                        range(len(candidates))
                    )

                    selected = st.selectbox(
                        "Choose the published-team data source",
                        options,
                        format_func=lambda i: (
                            f"{len(candidates[i]['players'])} players · "
                            f"{candidates[i]['source']}"
                            + (
                                " · selection field"
                                if candidates[i]["explicit"]
                                else " · general team field"
                            )
                        ),
                        key="source_" + mid
                    )

                    candidate = candidates[selected]

                    if not candidate["explicit"]:
                        st.warning(
                            "This source is a general team list, "
                            "not a confirmed published-selection "
                            "field. Verify it against PlayCricket."
                        )

                    if not any(
                        "(c)" in player
                        for player in candidate["players"]
                    ):
                        st.info(
                            "No captain marker found in this "
                            "API list. Add (c) beside the "
                            "captain if PlayCricket displays one."
                        )

                    source_players = candidate["players"]

                else:
                    st.warning(
                        "No HBCC player list found in "
                        "supported fields. Check PlayCricket "
                        "and enter the published players below."
                    )

                    source_players = []

                edit_key = "player_edit_" + mid
                source_key = "player_source_" + mid
                source_signature = tuple(
                    source_players
                )

                if (
                    st.session_state.get(source_key)
                    != source_signature
                ):
                    st.session_state[edit_key] = (
                        "\n".join(source_players)
                    )

                    st.session_state[source_key] = (
                        source_signature
                    )

                raw = st.text_area(
                    "Published HBCC players "
                    "(one per line; mark captain with (c))",
                    key=edit_key,
                    height=220
                )

                players = [
                    player.strip()
                    for player in raw.splitlines()
                    if player.strip()
                ]

                st.caption(
                    f"{len(players)} players in the "
                    "current list. Do not assume 11 "
                    "until all names are published."
                )

                st.markdown(
                    "[Verify published team on PlayCricket]"
                    f"({row['url']})"
                )

                with st.expander(
                    "Technical details: team selection sources"
                ):
                    st.json([
                        {
                            "source": candidate["source"],
                            "count": len(
                                candidate["players"]
                            ),
                            "players": candidate["players"]
                        }
                        for candidate in candidates
                    ])

                updated = dict(row)
                updated["players"] = players

                edited_rows.append(updated)

        output = selection_output(
            edited_rows
        )

        st.subheader(
            "Combined team announcement"
        )

        st.text_area(
            "Copy-ready selections",
            value=output,
            height=350
        )

        st.download_button(
            "Download selections",
            output,
            "hbcc_team_selections.txt",
            "text/plain"
        )

        st.caption(
            "Verify every player and captain "
            "against PlayCricket before publishing. "
            "Player lists may change."
        )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "HBCC Content Studio · "
    "Verify all published information "
    "against PlayCricket."
)
