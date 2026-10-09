
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests
import streamlit as st
from google import genai
from google.genai import types


# ============================================================
# HBCC CONTENT STUDIO
# MATCH REPORTS | WEEKEND REPORTS | TEAM SELECTIONS
# ============================================================

st.set_page_config(
    page_title="HBCC Content Studio",
    page_icon="🏏",
    layout="wide"
)

API_BASE = "https://grassrootsapiproxy.cricket.com.au/scores"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

DEFAULT_GRADES = {
    "Women's 1st XI": {
        "grade_id": "9b70ae63-142e-492b-b266-f42590204e93",
        "team_id": "121436ac-b8db-40c5-9fdc-2bac4439419a"
    },
    "Men's 1st XI": {
        "grade_id": "f5b98728-e7b2-40e6-af73-ac2eae56cedf",
        "team_id": "94ced5f2-a9f0-4ab3-b8aa-c3d0a643deab"
    },
    "Men's 2nd XI": {
        "grade_id": "d67df9f0-a982-461f-b0ee-4aa6bd89b6a6",
        "team_id": "74392aeb-a39d-4f11-9d34-8bff6ca4690d"
    }
}

UUID_PATTERN = re.compile(
    r"^[a-fA-F0-9]{8}-"
    r"[a-fA-F0-9]{4}-"
    r"[a-fA-F0-9]{4}-"
    r"[a-fA-F0-9]{4}-"
    r"[a-fA-F0-9]{12}$"
)


# ============================================================
# STYLING
# ============================================================

st.markdown(
    """
<style>
.stApp {
    background-color: #f5f7fb;
}

.block-container {
    max-width: 1200px;
    padding-top: 2rem;
}

.hbcc-hero {
    background: linear-gradient(110deg, #0f192d, #1a3154);
    border-bottom: 5px solid #d00832;
    border-radius: 16px;
    padding: 32px 36px;
    margin-bottom: 25px;
}

.hbcc-hero h1 {
    color: #ffffff !important;
    margin: 8px 0 12px;
    font-size: 2.4rem;
}

.hbcc-hero p {
    color: #e1e7f0;
    margin: 0;
}

.hbcc-label {
    color: #f5b82e;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 2px;
}

.stButton > button[kind="primary"] {
    background-color: #c8102e;
    border-color: #c8102e;
    color: white;
    border-radius: 9px;
    font-weight: 700;
}
</style>
""",
    unsafe_allow_html=True
)

# HTML is deliberately not indented.
# This avoids Streamlit displaying HTML as code.

st.markdown(
    '<div class="hbcc-hero">'
    '<div class="hbcc-label">'
    'HAWTHORN BOROONDARA CRICKET CLUB'
    '</div>'
    '<h1>Content Studio</h1>'
    '<p>Generate match reports, weekend wrap-ups '
    'and team selections from PlayCricket.</p>'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def get_name(value):
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        for key in (
            "displayName",
            "fullName",
            "name",
            "teamName",
            "playerName",
            "participantName",
            "shortName",
            "title"
        ):
            result = value.get(key)

            if isinstance(result, str) and result.strip():
                return result.strip()

        for key in ("player", "participant", "team"):
            nested = value.get(key)

            if isinstance(nested, dict):
                result = get_name(nested)

                if result:
                    return result

    return ""


def parse_date(value):
    """
    Handles strings, Unix timestamps, nested dictionaries,
    lists and common PlayCricket date representations.
    """

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        try:
            if value < 100000000:
                return None

            timestamp = (
                value / 1000
                if value > 100000000000
                else value
            )

            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc
            ).date()

        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(value, str):
        match = re.search(
            r"\d{4}-\d{2}-\d{2}",
            value
        )

        if match:
            try:
                return date.fromisoformat(match.group())
            except ValueError:
                pass

        return None

    if isinstance(value, list):
        for item in value:
            result = parse_date(item)

            if result:
                return result

        return None

    if isinstance(value, dict):
        priority_keys = (
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
            "matchSchedule",
            "days",
            "dates",
            "firstDay",
            "dateTime",
            "timestamp"
        )

        for key in priority_keys:
            if key in value:
                result = parse_date(value[key])

                if result:
                    return result

        # Try other nested values as a fallback.
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                result = parse_date(nested)

                if result:
                    return result

    return None


def match_date(match):
    if not isinstance(match, dict):
        return None

    for key in (
        "matchSchedule",
        "startDateTime",
        "startDate",
        "matchDate",
        "date",
        "scheduledStart",
        "scheduledDate",
        "startTime"
    ):
        if key in match:
            result = parse_date(match[key])

            if result:
                return result

    return None


def match_id(match):
    if not isinstance(match, dict):
        return None

    for key in ("id", "matchId", "matchID"):
        value = match.get(key)

        if isinstance(value, str):
            if UUID_PATTERN.fullmatch(value):
                return value

    return None


def match_teams(match):
    if not isinstance(match, dict):
        return []

    for key in ("teams", "teamSummaries"):
        value = match.get(key)

        if isinstance(value, list):
            return [
                item
                for item in value
                if isinstance(item, dict)
            ]

    teams = []

    for key in (
        "homeTeam",
        "awayTeam",
        "homeTeamSummary",
        "awayTeamSummary"
    ):
        value = match.get(key)

        if isinstance(value, dict):
            teams.append(value)

    return teams


def is_hbcc(team, expected_id):
    if not isinstance(team, dict):
        return False

    identifier = str(
        team.get("id")
        or team.get("teamId")
        or ""
    )

    name = get_name(team).lower()

    return (
        (bool(expected_id) and identifier == expected_id)
        or "hawthorn boroondara" in name
        or name == "hb hawks"
    )


def fixture_name(match):
    teams = match_teams(match)

    names = [
        get_name(team)
        for team in teams
    ]

    names = [name for name in names if name]

    if len(names) >= 2:
        return " vs ".join(names[:2])

    return get_name(match) or "Fixture"


def venue_name(match):
    if not isinstance(match, dict):
        return ""

    for key in (
        "venue",
        "ground",
        "venueName",
        "groundName"
    ):
        value = match.get(key)

        if value:
            return get_name(value) or str(value)

    return ""


def match_link(identifier):
    return (
        "https://play.cricket.com.au/"
        f"match/{identifier}"
    )


# ============================================================
# PLAYCRICKET API
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def fetch_json(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


def get_grade_matches(grade_id):
    return fetch_json(
        f"{API_BASE}/grades/{grade_id}/matches"
        "?jsconfig=eccn%3Atrue"
    )


def get_match_detail(identifier):
    return fetch_json(
        f"{API_BASE}/matches/{identifier}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )


def get_ball_data(identifier):
    return fetch_json(
        f"{API_BASE}/matches/{identifier}/balls"
        "?jsconfig=eccn%3Atrue"
    )


# ============================================================
# FIXTURE DISCOVERY
# ============================================================

def collect_matches(payload):
    """
    PlayCricket grade response is known to contain
    a top-level 'matches' array.

    This also supports nested structures.
    """

    if isinstance(payload, dict):
        matches = payload.get("matches")

        if isinstance(matches, list):
            return [
                item
                for item in matches
                if isinstance(item, dict)
            ]

    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            identifier = match_id(obj)

            if identifier:
                found[identifier] = obj

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(payload)

    return list(found.values())


def build_fixture(
    record,
    grade,
    team_id,
    detail=None
):
    identifier = match_id(record)

    summary = {}

    if isinstance(detail, dict):
        summary = detail.get(
            "matchSummary",
            {}
        )

        if not isinstance(summary, dict):
            summary = {}

    selected_date = (
        match_date(record)
        or match_date(summary)
        or match_date(detail)
    )

    name = fixture_name(record)

    if name == "Fixture":
        name = fixture_name(summary)

    if name == "Fixture":
        name = fixture_name(detail)

    venue = (
        venue_name(record)
        or venue_name(summary)
        or venue_name(detail)
    )

    return {
        "id": identifier,
        "grade": grade,
        "team_id": team_id,
        "date": str(selected_date) if selected_date else "",
        "name": name,
        "venue": venue,
        "url": match_link(identifier)
    }


def extract_manual_ids(text):
    identifiers = []

    for token in re.split(r"[\s,]+", text):
        token = token.strip()

        match = re.search(
            r"/match/([a-fA-F0-9-]{36})",
            token
        )

        identifier = (
            match.group(1)
            if match
            else token
        )

        if UUID_PATTERN.fullmatch(identifier):
            if identifier not in identifiers:
                identifiers.append(identifier)

    return identifiers


# ============================================================
# SCORECARD EXTRACTION
# ============================================================

def extract_innings(detail):
    innings = []

    def walk(obj):
        if isinstance(obj, dict):
            batting = obj.get("batting")
            bowling = obj.get("bowling")

            if (
                isinstance(batting, list)
                and isinstance(bowling, list)
            ):
                batters = []
                bowlers = []

                for player in batting:
                    if not isinstance(player, dict):
                        continue

                    name = get_name(
                        player.get("playerShortName")
                        or player.get("playerName")
                        or player.get("player")
                    )

                    if name and "*" not in name:
                        batters.append({
                            "name": name,
                            "runs": player.get("runsScored"),
                            "balls": player.get("ballsFaced"),
                            "dismissal": player.get("dismissalText")
                        })

                for player in bowling:
                    if not isinstance(player, dict):
                        continue

                    name = get_name(
                        player.get("playerShortName")
                        or player.get("playerName")
                        or player.get("player")
                    )

                    if name and "*" not in name:
                        bowlers.append({
                            "name": name,
                            "wickets": player.get("wicketsTaken"),
                            "runs": player.get("runsConceded"),
                            "overs": player.get("oversBowled")
                        })

                innings.append({
                    "runs": obj.get("runsScored"),
                    "wickets": obj.get("numberOfWicketsFallen"),
                    "batting": batters,
                    "bowling": bowlers
                })

                return

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(detail)

    return innings


def extract_ball_highlights(payload):
    events = []

    def walk(obj):
        if isinstance(obj, dict):
            if (
                "overNumber" in obj
                and "ballNumber" in obj
                and "progressRuns" in obj
            ):
                wicket = bool(
                    obj.get("dismissedParticipantId")
                )

                runs = obj.get("runsBat") or 0

                if wicket or runs >= 4:
                    events.append({
                        "over": obj.get("overNumber"),
                        "ball": obj.get("ballNumber"),
                        "score": obj.get("progressRuns"),
                        "wickets": obj.get("progressWickets"),
                        "runs": runs,
                        "wicket": wicket,
                        "striker": obj.get("strikerShortName"),
                        "bowler": obj.get("bowlerShortName")
                    })

                return

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(payload)

    return events[:120]


def report_data(fixture):
    detail = get_match_detail(
        fixture["id"]
    )

    summary = detail.get(
        "matchSummary",
        {}
    )

    if not isinstance(summary, dict):
        summary = {}

    data = {
        "grade": fixture["grade"],
        "fixture": fixture["name"],
        "date": fixture["date"],
        "venue": fixture["venue"],
        "result": summary.get("resultText"),
        "teams": match_teams(summary),
        "innings": extract_innings(detail)
    }

    try:
        data["ball_highlights"] = (
            extract_ball_highlights(
                get_ball_data(fixture["id"])
            )
        )
    except Exception:
        data["ball_highlights"] = "Unavailable"

    return data


# ============================================================
# GEMINI REPORT GENERATION
# ============================================================

def generate_article(
    fixtures,
    mode,
    target_words,
    context,
    avoid
):
    key = st.secrets.get(
        "GEMINI_API_KEY",
        ""
    )

    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from Streamlit Secrets."
        )

    client = genai.Client(api_key=key)

    data = [
        report_data(fixture)
        for fixture in fixtures
    ]

    prompt = f"""
You are writing for Hawthorn Boroondara
Cricket Club (HB Hawks).

Create a complete {mode.lower()} of
approximately {target_words} words.

Use Australian English.

Start with an engaging headline.

If this is a single match report:
- Tell the story of the match.
- Cover important batting and bowling.
- Explain turning points when supported.

If this is a weekend report:
- Write one cohesive club-wide article.
- Cover every supplied match.
- Include clear sections for each grade.
- Highlight important performances.

Use only the supplied match information.

Never invent:
- Weather
- Pitch conditions
- Quotes
- Injuries
- Partnerships
- Player backgrounds
- Tactics
- Results

Never guess private player names.

Do not assume a result for unfinished matches.

Treat the official result as authoritative.

Additional context:
{context}

Things to avoid:
{avoid}

MATCH INFORMATION:
{json.dumps(data, ensure_ascii=False, default=str)}

Return only the complete article and headline.
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

                if response.text:
                    return response.text.strip()

            except Exception as error:
                last_error = error

                error_text = str(error).lower()

                if (
                    "429" in error_text
                    or "503" in error_text
                    or "unavailable" in error_text
                ):
                    time.sleep(2 + attempt * 2)
                    continue

                if (
                    "404" in error_text
                    or "not found" in error_text
                ):
                    break

                raise

    raise RuntimeError(
        f"Gemini could not generate the report: {last_error}"
    )


# ============================================================
# TEAM SELECTION EXTRACTION
# ============================================================

def extract_selected_players(detail, expected_team_id):
    """
    Only retrieve players attached to an identifiable
    HBCC team selection.

    Do not use batting/bowling scorecards as selections.
    """

    candidates = []

    def walk(obj, depth=0):
        if depth > 12:
            return

        if isinstance(obj, dict):
            for key in (
                "teams",
                "teamSummaries",
                "homeTeam",
                "awayTeam",
                "teamSelections",
                "lineups",
                "lineUps"
            ):
                value = obj.get(key)

                if isinstance(value, list):
                    for team in value:
                        if is_hbcc(team, expected_team_id):
                            candidates.append(team)

                elif isinstance(value, dict):
                    if is_hbcc(value, expected_team_id):
                        candidates.append(value)

            for key, value in obj.items():
                if key in (
                    "scorecard",
                    "innings",
                    "batting",
                    "bowling",
                    "balls",
                    "deliveries"
                ):
                    continue

                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)

        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)

    walk(detail)

    for team in candidates:
        for key in (
            "selectedPlayers",
            "players",
            "lineup",
            "lineUp",
            "teamSelection",
            "participants",
            "selectedParticipants"
        ):
            players = team.get(key)

            if isinstance(players, dict):
                for nested_key in (
                    "players",
                    "selectedPlayers",
                    "participants"
                ):
                    if isinstance(
                        players.get(nested_key),
                        list
                    ):
                        players = players[nested_key]
                        break

            if not isinstance(players, list):
                continue

            names = []

            for player in players:
                name = get_name(player)

                if not name or "*" in name:
                    continue

                if name.lower() == "private player":
                    continue

                if isinstance(player, dict):
                    role = str(
                        player.get("role")
                        or player.get("teamRole")
                        or ""
                    ).lower()

                    captain = (
                        player.get("isCaptain") is True
                        or player.get("captain") is True
                        or role == "captain"
                    )

                    if captain and "(c)" not in name.lower():
                        name += " (c)"

                if name not in names:
                    names.append(name)

            if names:
                return names

    return []


def format_selections(rows):
    lines = [
        "HAWTHORN BOROONDARA CRICKET CLUB",
        "TEAM SELECTIONS",
        ""
    ]

    for row in rows:
        lines.append(row["grade"].upper())
        lines.append(row["name"])

        if row["date"]:
            lines.append("Date: " + row["date"])

        if row["venue"]:
            lines.append("Venue: " + row["venue"])

        lines.append("")

        if row["players"]:
            for index, player in enumerate(
                row["players"],
                1
            ):
                lines.append(f"{index}. {player}")
        else:
            lines.append(
                "Selection unavailable — check PlayCricket"
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# SESSION STATE
# ============================================================

if "fixtures" not in st.session_state:
    st.session_state.fixtures = []

if "diagnostics" not in st.session_state:
    st.session_state.diagnostics = []

if "schedule_samples" not in st.session_state:
    st.session_state.schedule_samples = []

if "article" not in st.session_state:
    st.session_state.article = ""

if "selection_rows" not in st.session_state:
    st.session_state.selection_rows = []


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("HBCC Grades")

    st.caption(
        "Women's 1st XI, Men's 1st XI and "
        "Men's 2nd XI are preconfigured."
    )

    additional_grades = st.text_area(
        "Additional grades",
        placeholder=(
            "Men's 3rd XI | GRADE_ID | TEAM_ID"
        ),
        height=100
    )

    st.caption(
        "Format: Grade name | Grade ID | Team ID"
    )

    if st.button("Clear PlayCricket cache"):
        st.cache_data.clear()
        st.success("Cache cleared")


all_grades = dict(DEFAULT_GRADES)

for line in additional_grades.splitlines():
    parts = [
        part.strip()
        for part in line.split("|")
    ]

    if (
        len(parts) == 3
        and UUID_PATTERN.fullmatch(parts[1])
        and UUID_PATTERN.fullmatch(parts[2])
    ):
        all_grades[parts[0]] = {
            "grade_id": parts[1],
            "team_id": parts[2]
        }


# ============================================================
# GENERATOR SELECTION
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


# ============================================================
# STEP 1 — DATES
# ============================================================

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

    selected_dates = set()

    if date_mode == "Date range":
        chosen = st.date_input(
            "Match date range",
            value=(
                date.today(),
                date.today() + timedelta(days=1)
            )
        )

        if (
            isinstance(chosen, (tuple, list))
            and len(chosen) == 2
        ):
            start, end = chosen

            if 0 <= (end - start).days <= 45:
                selected_dates = {
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
        number_of_dates = st.number_input(
            "Number of dates",
            min_value=1,
            max_value=10,
            value=1
        )

        for i in range(number_of_dates):
            chosen_date = st.date_input(
                f"Date {i + 1}",
                value=date.today() + timedelta(days=i),
                key=f"date_{i}"
            )

            selected_dates.add(chosen_date)


# ============================================================
# STEP 2 — GRADES
# ============================================================

with right:
    st.subheader("2. Choose grades")

    selected_grades = st.multiselect(
        "HBCC grades",
        list(all_grades),
        default=list(DEFAULT_GRADES)
    )


# ============================================================
# OPTIONAL MANUAL MATCH URLS
# ============================================================

with st.expander(
    "Optional: Enter match URLs manually"
):
    manual_urls = st.text_area(
        "PlayCricket match links",
        placeholder=(
            "https://play.cricket.com.au/match/..."
        ),
        height=90
    )

    st.caption(
        "Use this if automatic fixture discovery "
        "cannot find a published match."
    )


# ============================================================
# FIND FIXTURES
# ============================================================

if st.button(
    "🔎 Find HBCC fixtures",
    type="primary",
    use_container_width=True,
    disabled=(
        not selected_dates
        or not selected_grades
    )
):
    found = {}
    diagnostics = []
    schedule_samples = []

    with st.spinner("Searching PlayCricket..."):

        for grade in selected_grades:
            config = all_grades[grade]

            grade_id = config["grade_id"]
            expected_team_id = config["team_id"]

            try:
                payload = get_grade_matches(
                    grade_id
                )

                records = collect_matches(
                    payload
                )

                # ==========================================
                # BUILT-IN MATCH SCHEDULE DIAGNOSTIC
                # ==========================================

                if records:
                    sample = records[0]

                    schedule_samples.append({
                        "grade": grade,
                        "match_id": sample.get("id"),
                        "matchSchedule": sample.get(
                            "matchSchedule"
                        ),
                        "sample_record": sample
                    })

                # ==========================================
                # FIXTURE SEARCH
                # ==========================================

                stats = {
                    "grade": grade,
                    "api_response_type": type(
                        payload
                    ).__name__,
                    "records_identified": len(records),
                    "records_without_date": 0,
                    "records_outside_dates": 0,
                    "records_other_team": 0,
                    "matched": 0
                }

                for record in records:
                    identifier = match_id(record)

                    if not identifier:
                        continue

                    fixture_date = match_date(
                        record
                    )

                    detail = None

                    # If date is missing, try match detail.
                    if not fixture_date:
                        try:
                            detail = get_match_detail(
                                identifier
                            )

                            summary = detail.get(
                                "matchSummary",
                                {}
                            )

                            fixture_date = (
                                match_date(summary)
                                or match_date(detail)
                            )

                        except Exception:
                            pass

                    if not fixture_date:
                        stats[
                            "records_without_date"
                        ] += 1
                        continue

                    if fixture_date not in selected_dates:
                        stats[
                            "records_outside_dates"
                        ] += 1
                        continue

                    teams = match_teams(record)

                    if not teams and detail:
                        teams = match_teams(detail)

                    if teams:
                        if not any(
                            is_hbcc(
                                team,
                                expected_team_id
                            )
                            for team in teams
                        ):
                            stats[
                                "records_other_team"
                            ] += 1
                            continue

                    if identifier not in found:
                        found[identifier] = build_fixture(
                            record,
                            grade,
                            expected_team_id,
                            detail
                        )

                        stats["matched"] += 1

                diagnostics.append(stats)

            except Exception as error:
                diagnostics.append({
                    "grade": grade,
                    "error": str(error)
                })

        # ==========================================
        # MANUAL MATCH URL FALLBACK
        # ==========================================

        for identifier in extract_manual_ids(
            manual_urls
        ):
            if identifier in found:
                continue

            try:
                detail = get_match_detail(
                    identifier
                )

                summary = detail.get(
                    "matchSummary",
                    {}
                )

                if not isinstance(summary, dict):
                    summary = {}

                grade = selected_grades[0]

                teams = (
                    match_teams(summary)
                    or match_teams(detail)
                )

                for candidate in selected_grades:
                    expected_id = all_grades[
                        candidate
                    ]["team_id"]

                    if any(
                        is_hbcc(team, expected_id)
                        for team in teams
                    ):
                        grade = candidate
                        break

                record = dict(summary or detail)
                record["id"] = identifier

                found[identifier] = build_fixture(
                    record,
                    grade,
                    all_grades[grade]["team_id"],
                    detail
                )

            except Exception as error:
                diagnostics.append({
                    "manual_match": identifier,
                    "error": str(error)
                })

    st.session_state.fixtures = sorted(
        found.values(),
        key=lambda item: (
            item["date"],
            item["grade"]
        )
    )

    st.session_state.diagnostics = diagnostics
    st.session_state.schedule_samples = schedule_samples
    st.session_state.article = ""
    st.session_state.selection_rows = []


# ============================================================
# STEP 3 — REVIEW FIXTURES
# ============================================================

st.subheader("3. Review fixtures")

fixtures = st.session_state.fixtures

chosen_fixtures = []

if fixtures:
    st.success(
        f"{len(fixtures)} fixture(s) found."
    )

    for fixture in fixtures:
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
            chosen_fixtures.append(fixture)

        st.caption(
            f"[View on PlayCricket]({fixture['url']})"
        )

else:
    st.info(
        "No fixtures loaded. Click Find HBCC fixtures. "
        "If none are found, check the diagnostics below."
    )


# ============================================================
# DIAGNOSTICS — BUILT INTO THE APP
# ============================================================

if st.session_state.diagnostics:

    with st.expander(
        "Fixture search diagnostics",
        expanded=not bool(fixtures)
    ):
        st.json(
            st.session_state.diagnostics
        )

    with st.expander(
        "PlayCricket match schedule diagnostic",
        expanded=not bool(fixtures)
    ):
        st.info(
            "This shows the actual matchSchedule "
            "field returned by PlayCricket. "
            "It will help identify why fixture "
            "dates are not being recognised."
        )

        for sample in st.session_state.schedule_samples:
            st.markdown(
                f"**{sample['grade']}**"
            )

            st.write(
                "Match ID:",
                sample["match_id"]
            )

            st.markdown(
                "**Match schedule**"
            )

            st.json(
                sample["matchSchedule"]
            )

            with st.expander(
                "View full sample fixture record"
            ):
                st.json(
                    sample["sample_record"]
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

    if mode == "Match Report" and chosen_fixtures:
        chosen_match = st.selectbox(
            "Choose match",
            chosen_fixtures,
            format_func=lambda item: (
                f"{item['grade']} — {item['name']}"
            )
        )

        report_fixtures = [chosen_match]

    else:
        report_fixtures = chosen_fixtures

    context = st.text_area(
        "Additional context (optional)",
        placeholder=(
            "Club milestones, debuts, "
            "special achievements..."
        )
    )

    avoid = st.text_area(
        "Anything to avoid (optional)",
        height=70
    )

    length = st.select_slider(
        "Target report length",
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
                "Reading scorecards and writing..."
            ):
                article = generate_article(
                    report_fixtures,
                    mode,
                    length,
                    context,
                    avoid
                )

                st.session_state.article = article

        except Exception as error:
            st.error(
                "Could not generate report: "
                + str(error)
            )

    if st.session_state.article:
        st.subheader("Generated article")

        st.markdown(
            st.session_state.article
        )

        st.text_area(
            "Copy article",
            st.session_state.article,
            height=350
        )

        st.download_button(
            "Download article",
            st.session_state.article,
            file_name="hbcc_report.txt",
            mime="text/plain"
        )


# ============================================================
# TEAM SELECTIONS GENERATOR
# ============================================================

else:
    st.subheader("4. Retrieve selected teams")

    if st.button(
        "Retrieve selected teams",
        type="primary",
        disabled=not chosen_fixtures
    ):
        rows = []

        with st.spinner(
            "Retrieving published team selections..."
        ):
            for fixture in chosen_fixtures:
                row = dict(fixture)

                row["players"] = []
                row["error"] = ""

                try:
                    detail = get_match_detail(
                        fixture["id"]
                    )

                    row["players"] = (
                        extract_selected_players(
                            detail,
                            fixture["team_id"]
                        )
                    )

                    row["venue"] = (
                        row["venue"]
                        or venue_name(detail)
                    )

                except Exception as error:
                    row["error"] = str(error)

                rows.append(row)

        st.session_state.selection_rows = rows

    if st.session_state.selection_rows:
        edited_rows = []

        for row in st.session_state.selection_rows:

            with st.container(border=True):
                st.markdown(
                    f"**{row['grade']} — {row['name']}**"
                )

                st.caption(
                    f"{row['date']} · {row['venue']}"
                )

                if row["error"]:
                    st.warning(
                        "Could not retrieve selection: "
                        + row["error"]
                    )

                elif not row["players"]:
                    st.warning(
                        "No published HBCC players found "
                        "in the supported API fields. "
                        "Check the match page and enter "
                        "the players manually if needed."
                    )

                players_text = st.text_area(
                    "Players (one per line)",
                    value="\n".join(row["players"]),
                    height=180,
                    key="players_" + row["id"]
                )

                edited = dict(row)

                edited["players"] = [
                    line.strip()
                    for line in players_text.splitlines()
                    if line.strip()
                ]

                edited_rows.append(edited)

                st.markdown(
                    f"[Verify on PlayCricket]({row['url']})"
                )

        output = format_selections(
            edited_rows
        )

        st.subheader(
            "Combined team announcement"
        )

        st.text_area(
            "Copy-ready selections",
            output,
            height=350
        )

        st.download_button(
            "Download selections",
            output,
            file_name="hbcc_team_selections.txt",
            mime="text/plain"
        )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "HBCC Content Studio · "
    "Verify all generated information "
    "against PlayCricket before publishing."
)
