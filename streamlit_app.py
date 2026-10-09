
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
# ============================================================

st.set_page_config(
    page_title="HBCC Content Studio",
    page_icon="https://static.wixstatic.com/media/629003_3235aad8ce9148d48818443fc6d8b5df%7Emv2.png/v1/fill/w_192%2Ch_192%2Clg_1%2Cusm_0.66_1.00_0.01/629003_3235aad8ce9148d48818443fc6d8b5df%7Emv2.png",
    layout="wide",
    initial_sidebar_state="collapsed"
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
    "Women's 2nd XI": (
        "71054210-a0e9-46ef-aa44-44c5b7bdff97",
        "9fc4af1a-c13a-4f23-9a0e-8cf10915843d"
    ),
    "Men's 1st XI": (
        "f5b98728-e7b2-40e6-af73-ac2eae56cedf",
        "94ced5f2-a9f0-4ab3-b8aa-c3d0a643deab"
    ),
    "Men's 2nd XI": (
        "d67df9f0-a982-461f-b0ee-4aa6bd89b6a6",
        "74392aeb-a39d-4f11-9d34-8bff6ca4690d"
    ),
    "Men's 3rd XI": (
        "5e75167c-a2b6-49e4-8e3e-ee308a415204",
        "5bd6f18c-b7f5-4153-8698-17ef26a87800"
    ),
    "Men's 4th XI": (
        "08850a6a-6343-4518-bf55-1cf23123382f",
        "7aaa499c-8f46-4289-86b4-9cced14d5989"
    ),
    "Men's 5th XI": (
        "491395bf-4a1b-498b-8e20-49bdd9fc112f",
        "16e6e08f-1b80-4a18-9e70-186a0ae0893f"
    ),
    "Men's 6th XI": (
        "576cc3b9-8a3b-4fe2-8e8e-e70a1aee0708",
        "8dd03070-4aa5-4ed9-9122-177a4fc5816a"
    ),
    "Men's Over 40s 1st XI": (
        "30f3da7c-1e66-41ea-9ae1-9fe5691eecc1",
        "25587b62-d7c6-4916-a2ed-6b42fed0434a"
    ),
    "Men's Over 50s 1st XI": (
        "9a0cd4bf-0ad5-4a15-acd3-e7d781c6e76a",
        "1b9fb198-ddc4-4117-a183-3bee9fae7d94"
    ),
    "Men's Sunday 1st XI": (
        "b5df35ef-35b6-41c0-b736-40c51839acdb",
        "2ba73357-c3fd-41af-a9df-6648e8d7112b"
    ),
    "Men's Sunday 2nd XI": (
        "4a84a74b-f201-481a-93a9-cc61dd14cad0",
        "018cab89-ff43-4cac-8d1a-c96ee5579b39"
    ),
    "All Abilities Mixed XI": (
        "6014df30-7eac-46c9-8516-236426a1187e",
        "b4fcbc4a-ca5e-4864-9fd6-2cbd95c791fd"
    )
}

UUID = re.compile(
    r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}"
    r"-[0-9a-fA-F]{12}$"
)


# ============================================================
# STYLING
# ============================================================

st.markdown(
    """
<style>
.stApp {
    background: #f5f7fa;
    color: #18263a;
}

.block-container {
    max-width: 1200px;
    padding-top: 1.2rem;
    padding-bottom: 3rem;
}

.hbcc-hero {
    background: linear-gradient(115deg, #0f192d, #203a5b);
    padding: 40px 30px 24px;
    border-radius: 15px;
    border-bottom: 4px solid #c8102e;
    margin-bottom: 18px;
}

.hbcc-hero h1 {
      font-size: 30px;
    font-weight: 750;
    line-height: 1.15;
    margin: 0 0 6px 0 !important;
    padding: 0 !important;
    color: white !important;
}

.hbcc-hero p {
    color: #d3dce9;
    font-size: 13px;
    line-height: 1.4;
    margin: 0 !important;
    padding: 0 !important;
}

.hbcc-eyebrow {
    color: #f5b82e;
    font-weight: 800;
    font-size: 11px;
    letter-spacing: 1.5px;
    line-height: 1.2;
    margin: 0 0 8px 0;
}

.hbcc-step {
    padding: 10px 15px;
    border-radius: 10px;
    background: white;
    border: 1px solid #e0e6ee;
    font-size: 13px;
    text-align: center;
}

.hbcc-step.active {
    background: #0f192d;
    color: white;
    border-color: #0f192d;
}

.hbcc-section {
    color: #c8102e;
    font-weight: 800;
    letter-spacing: 1px;
    font-size: 12px;
    margin: 10px 0;
}

.stButton > button[kind="primary"] {
    background: #c8102e;
    border-color: #c8102e;
    color: white;
    border-radius: 10px;
    font-weight: 700;
}

.stButton > button[kind="primary"]:hover {
    background: #a30e27;
    border-color: #a30e27;
}

.stDownloadButton > button {
    border: 1px solid #0f192d;
    border-radius: 10px;
}

div[data-testid="stMetric"] {
    background: white;
    border: 1px solid #e4e8ef;
    padding: 12px;
    border-radius: 12px;
}
</style>
""",
    unsafe_allow_html=True
)


# ============================================================
# PLAYCRICKET HELPERS
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

        for key in (
            "player",
            "participant",
            "team",
            "person"
        ):
            if isinstance(value.get(key), dict):
                result = name_of(value[key])

                if result:
                    return result

    return ""


def parse_date(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        if value < 100000000:
            return None

        try:
            timestamp = (
                value / 1000
                if value > 100000000000
                else value
            )

            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc
            ).date()

        except (ValueError, OSError, OverflowError):
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
            result = parse_date(item)

            if result:
                return result

        return None

    if isinstance(value, dict):
        for parts in (
            ("year", "month", "day"),
            ("startYear", "startMonth", "startDay")
        ):
            if all(part in value for part in parts):
                try:
                    return date(
                        *(int(value[p]) for p in parts)
                    )

                except (ValueError, TypeError):
                    pass

        priority = (
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

        for key in priority:
            if key in value:
                result = parse_date(value[key])

                if result:
                    return result

        for item in value.values():
            if isinstance(
                item,
                (str, int, float, dict, list)
            ):
                result = parse_date(item)

                if result:
                    return result

    return None


def match_id(obj):
    if isinstance(obj, dict):
        for key in (
            "id",
            "matchId",
            "matchID"
        ):
            value = obj.get(key)

            if (
                isinstance(value, str)
                and UUID.fullmatch(value)
            ):
                return value

    return None


def teams_of(obj):
    if not isinstance(obj, dict):
        return []

    for key in (
        "teams",
        "teamSummaries"
    ):
        if isinstance(obj.get(key), list):
            return [
                team
                for team in obj[key]
                if isinstance(team, dict)
            ]

    return [
        obj[key]
        for key in (
            "homeTeam",
            "awayTeam"
        )
        if isinstance(obj.get(key), dict)
    ]


def hbcc_team(obj, expected_id):
    if not isinstance(obj, dict):
        return False

    identifiers = [
        str(obj.get(key) or "")
        for key in (
            "id",
            "teamId",
            "clubTeamId"
        )
    ]

    name = name_of(obj).lower()

    return (
        (bool(expected_id) and expected_id in identifiers)
        or "hawthorn boroondara" in name
        or name == "Hawthorn Boroondara"
    )


def fixture_name(obj):
    names = [
        name_of(team)
        for team in teams_of(obj)
    ]

    names = [
        name
        for name in names
        if name
    ]

    if len(names) >= 2:
        return " vs ".join(names[:2])

    return name_of(obj) or "Fixture"


def venue_of(obj):
    if isinstance(obj, dict):
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

            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)

    return list(found.values())


def fixture_from(record, grade, team_id, detail=None):
    summary = (
        (detail or {}).get("matchSummary")
        or {}
    )

    if not isinstance(summary, dict):
        summary = {}

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
        "team_id": team_id,
        "date": str(when) if when else "",
        "name": label,
        "venue": (
            venue_of(record)
            or venue_of(summary)
            or venue_of(detail)
        ),
        "url": match_url(mid),
        "status": str(record.get("status") or "")
    }


def manual_ids(text):
    identifiers = []

    for token in re.split(r"[\s,]+", text):
        found = re.search(
            r"/match/([0-9a-fA-F-]{36})",
            token
        )

        mid = (
            found.group(1)
            if found
            else token
        )

        if (
            UUID.fullmatch(mid)
            and mid not in identifiers
        ):
            identifiers.append(mid)

    return identifiers


# ============================================================
# TEAM SELECTIONS
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
        if team.get(key):
            refs.add(
                str(team[key]).strip().lower()
            )

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

    elif isinstance(captain, str):
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

    refs = captain_references(team)
    players = []
    seen = set()

    for item in items:
        name = name_of(item)

        if not name or "*" in name:
            continue

        if name.lower() == "private player":
            continue

        clean = re.sub(
            r"\s*\(c\)\s*$",
            "",
            name,
            flags=re.I
        ).strip()

        player_refs = {
            name.lower(),
            clean.lower()
        }

        if isinstance(item, dict):
            for key in (
                "id",
                "playerId",
                "participantId"
            ):
                if item.get(key):
                    player_refs.add(
                        str(item[key]).lower()
                    )

            for nested_key in (
                "player",
                "participant"
            ):
                nested = item.get(nested_key)

                if isinstance(nested, dict):
                    for key in (
                        "id",
                        "playerId",
                        "participantId"
                    ):
                        if nested.get(key):
                            player_refs.add(
                                str(nested[key]).lower()
                            )

        is_captain = (
            captain_flag(item)
            or bool(player_refs & refs)
            or "(c)" in name.lower()
        )

        if clean.lower() in seen:
            continue

        seen.add(clean.lower())

        players.append(
            clean + (" (c)" if is_captain else "")
        )

    return players


def selection_candidates(detail, expected_id):
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

    def inspect(team, path):
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
            signature = (
                source,
                tuple(players)
            )

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
                "explicit": explicit,
                "score": score
            })

    def walk(obj, path="root", depth=0):
        if depth > 14:
            return

        if isinstance(obj, dict):
            if hbcc_team(obj, expected_id):
                inspect(obj, path)

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
            for i, value in enumerate(obj):
                if isinstance(value, (dict, list)):
                    walk(
                        value,
                        f"{path}[{i}]",
                        depth + 1
                    )

    walk(detail)

    return sorted(
        candidates,
        key=lambda x: (
            x["score"],
            len(x["players"])
        ),
        reverse=True
    )


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
                f"{i}. {player}"
                for i, player in enumerate(
                    row["players"],
                    1
                )
            )

        else:
            lines.append(
                "Selected team coming soon"
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# REPORT GENERATION
# ============================================================

def innings_summary(detail):
    output = []

    def walk(value):
        if isinstance(value, dict):
            batting = value.get("batting")
            bowling = value.get("bowling")

            if (
                isinstance(batting, list)
                and isinstance(bowling, list)
            ):
                batters = []
                bowlers = []

                for player in batting:
                    if not isinstance(player, dict):
                        continue

                    name = name_of(
                        player.get("playerShortName")
                        or player.get("playerName")
                        or player.get("player")
                    )

                    if name and "*" not in name:
                        batters.append({
                            "name": name,
                            "runs": player.get(
                                "runsScored"
                            ),
                            "balls": player.get(
                                "ballsFaced"
                            ),
                            "dismissal": player.get(
                                "dismissalText"
                            )
                        })

                for player in bowling:
                    if not isinstance(player, dict):
                        continue

                    name = name_of(
                        player.get("playerShortName")
                        or player.get("playerName")
                        or player.get("player")
                    )

                    if name and "*" not in name:
                        bowlers.append({
                            "name": name,
                            "wickets": player.get(
                                "wicketsTaken"
                            ),
                            "runs": player.get(
                                "runsConceded"
                            ),
                            "overs": player.get(
                                "oversBowled"
                            )
                        })

                output.append({
                    "runs": value.get("runsScored"),
                    "wickets": value.get(
                        "numberOfWicketsFallen"
                    ),
                    "batting": batters,
                    "bowling": bowlers
                })

                return

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

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
                        "over": value.get(
                            "overNumber"
                        ),
                        "ball": value.get(
                            "ballNumber"
                        ),
                        "score": value.get(
                            "progressRuns"
                        ),
                        "wickets": value.get(
                            "progressWickets"
                        ),
                        "runsBat": value.get(
                            "runsBat"
                        ),
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

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)

    return events[:100]


def report_data(fixture):
    detail = match_detail(
        fixture["id"]
    )

    summary = (
        detail.get("matchSummary")
        or {}
    )

    if not isinstance(summary, dict):
        summary = {}

    result = {
        "fixture": fixture["name"],
        "grade": fixture["grade"],
        "date": fixture["date"],
        "venue": fixture["venue"],
        "result": summary.get(
            "resultText"
        ),
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


def generate_article(
    fixtures,
    mode,
    target,
    context,
    avoid
):
    key = st.secrets.get(
        "GEMINI_API_KEY",
        ""
    )

    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing "
            "from Streamlit Secrets."
        )

    data = [
        report_data(fixture)
        for fixture in fixtures
    ]

    client = genai.Client(
        api_key=key
    )

    prompt = f"""
You are the cricket reporter for Hawthorn Boroondara
Cricket Club.

Write a complete {mode.lower()} of approximately
{target} words in Australian English.

Begin with a compelling headline.

Use the verified scorecard data and significant
ball-by-ball moments to tell the story.

For a Weekend Wrap:
- Cover every supplied fixture.
- Include sections for each grade.
- Create a cohesive club-wide narrative.

Only use supplied facts.

Never invent results, statistics, quotes,
partnerships, pitch conditions, weather,
tactics, player identities or injuries.

Treat official result text as authoritative.
Never infer a result from an unfinished match.

Additional context:
{context}

Things to avoid:
{avoid}

Match data:
{json.dumps(data, ensure_ascii=False, default=str)}

Return only the complete finished article.
"""

    last_error = None

    for model in (
        "gemini-3.5-flash-lite",
    ):
        for attempt in range(2):
            try:
                response = (
                    client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.35,
                            max_output_tokens=6500
                        )
                    )
                )

                if (
                    response.text
                    and response.text.strip()
                ):
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
                    time.sleep(
                        2 + attempt * 2
                    )
                    continue

                if (
                    "404" in message
                    or "not found" in message
                ):
                    break

                raise

    raise RuntimeError(
        "Gemini could not generate the article: "
        f"{last_error}"
    )


# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "workflow": "setup",
    "content_mode": "Weekend Wrap",
    "fixtures": [],
    "selected_fixture_ids": [],
    "diagnostics": [],
    "schedule_samples": [],
    "article": "",
    "selection_rows": []
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


def reset_results():
    st.session_state.workflow = "setup"
    st.session_state.fixtures = []
    st.session_state.selected_fixture_ids = []
    st.session_state.selection_rows = []
    st.session_state.article = ""


# ============================================================
# ADVANCED SETTINGS
# ============================================================

with st.sidebar:
    st.header("Advanced settings")

    additional = st.text_area(
        "Additional grades",
        placeholder=(
            "Grade name | GRADE_ID | TEAM_ID"
        ),
        height=100
    )

    st.caption(
        "One grade per line. "
        "Your 13 senior teams are already configured."
    )

    manual_urls = st.text_area(
        "Manual PlayCricket match links",
        placeholder=(
            "https://play.cricket.com.au/match/..."
        ),
        height=100
    )

    if st.button(
        "Clear PlayCricket cache"
    ):
        st.cache_data.clear()
        st.success(
            "Cache cleared"
        )


all_grades = dict(GRADES)

for line in additional.splitlines():
    parts = [
        x.strip()
        for x in line.split("|")
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
# HEADER AND PROGRESS
# ============================================================

st.markdown(
    '<div class="hbcc-hero">'
    '<div class="hbcc-eyebrow">'
    'HAWTHORN BOROONDARA CRICKET CLUB'
    '</div>'
    '<h1>Content Studio</h1>'
    '<p>Create. Review. Publish.</p>'
    '</div>',
    unsafe_allow_html=True
)


stage = st.session_state.workflow

steps = [
    ("setup", "01 · Setup"),
    ("review", "02 · Review Fixtures"),
    ("export", "03 · Create & Export")
]

columns = st.columns(3, gap="small")

for column, (step_key, label) in zip(columns, steps):
    with column:
        is_active = stage == step_key

        # Only allow navigation to steps already reached
        can_navigate = (
            step_key == "setup"
            or (
                step_key == "review"
                and bool(st.session_state.fixtures)
            )
            or (
                step_key == "export"
                and bool(st.session_state.selected_fixture_ids)
            )
        )

        if st.button(
            label,
            key=f"nav_{step_key}",
            type="primary" if is_active else "secondary",
            use_container_width=True,
            disabled=not can_navigate
        ):
            st.session_state.workflow = step_key
            st.rerun()



# ============================================================
# STEP 1 — SETUP
# ============================================================

if stage == "setup":
    st.markdown(
        "### What are you creating?"
    )

    modes = [
        "Match Report",
        "Weekend Wrap",
        "Team Selections"
    ]

    mode_descriptions = {
        "Match Report": (
            "One match, one complete article"
        ),
        "Weekend Wrap": (
            "All the weekend action"
        ),
        "Team Selections": (
            "Published players across grades"
        )
    }

    mode_columns = st.columns(3)

    for column, option in zip(
        mode_columns,
        modes
    ):
        with column:
            with st.container(
                border=True
            ):
                st.markdown(
                    f"**{option}**"
                )

                st.caption(
                    mode_descriptions[option]
                )

                selected = (
                    st.session_state.content_mode
                    == option
                )

                if st.button(
                    (
                        "Selected"
                        if selected
                        else "Choose"
                    ),
                    key="mode_" + option,
                    type=(
                        "primary"
                        if selected
                        else "secondary"
                    ),
                    use_container_width=True
                ):
                    st.session_state.content_mode = (
                        option
                    )

                    st.rerun()

    mode = st.session_state.content_mode

    st.divider()

    left, right = st.columns(
        2,
        gap="large"
    )

    with left:
        st.markdown(
            "#### When are the matches?"
        )

        default_date = (
            "This Weekend"
            if mode == "Team Selections"
            else "Last Weekend"
        )

        date_choice = st.segmented_control(
            "Match dates",
            [
                "This Weekend",
                "Last Weekend",
                "Custom"
            ],
            default=default_date,
            key="date_choice_" + mode
        )

        today = date.today()

        days_until_saturday = (
            5 - today.weekday()
        ) % 7

        this_saturday = (
            today
            + timedelta(
                days=days_until_saturday
            )
        )

        dates = set()

        if date_choice == "This Weekend":
            dates = {
                this_saturday,
                this_saturday + timedelta(
                    days=1
                )
            }

        elif date_choice == "Last Weekend":
            saturday = (
                this_saturday
                - timedelta(days=7)
            )

            dates = {
                saturday,
                saturday + timedelta(
                    days=1
                )
            }

        else:
            method = st.radio(
                "Choose date method",
                [
                    "Date range",
                    "Individual dates"
                ],
                horizontal=True
            )

            if method == "Date range":
                selected = st.date_input(
                    "Date range",
                    value=(
                        today,
                        today + timedelta(
                            days=1
                        )
                    )
                )

                if (
                    isinstance(
                        selected,
                        (tuple, list)
                    )
                    and len(selected) == 2
                ):
                    start, end = selected
                    days = (
                        end - start
                    ).days

                    if 0 <= days <= 45:
                        dates = {
                            start + timedelta(
                                days=i
                            )
                            for i in range(
                                days + 1
                            )
                        }

                    else:
                        st.warning(
                            "Choose a range "
                            "of 46 days or less."
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
                            value=(
                                today
                                + timedelta(
                                    days=i
                                )
                            ),
                            key=(
                                f"individual_date_{i}"
                            )
                        )
                    )

        if dates:
            first = min(dates)
            last = max(dates)

            st.info(
                first.strftime(
                    "%d %B %Y"
                )
                + (
                    " – "
                    + last.strftime(
                        "%d %B %Y"
                    )
                    if last != first
                    else ""
                )
            )

    with right:
        st.markdown(
            "#### Which teams?"
        )

        grade_choice = st.segmented_control(
            "Grade filter",
            [
                "All",
                "Men's",
                "Women's",
                "All Abilities",
                "Custom"
            ],
            default="All"
        )

        if grade_choice == "All":
            selected_grades = list(
                all_grades
            )

        elif grade_choice == "Men's":
            selected_grades = [
                grade
                for grade in all_grades
                if "men's" in grade.lower()
                and "women's" not in grade.lower()
            ]

        elif grade_choice == "Women's":
            selected_grades = [
                grade
                for grade in all_grades
                if "women's" in grade.lower()
            ]

        elif grade_choice == "All Abilities":
            selected_grades = [
                grade
                for grade in all_grades
                if "all abilities" in grade.lower()
            ]

        else:
            selected_grades = st.multiselect(
                "Select individual grades",
                list(all_grades),
                default=list(all_grades)
            )

        st.info(
            f"{len(selected_grades)} "
            "teams selected"
        )

        with st.expander(
            "View selected teams"
        ):
            for grade in selected_grades:
                st.write(
                    grade
                )

    st.divider()

    if st.button(
        "Find Matches",
        type="primary",
        use_container_width=True,
        disabled=(
            not dates
            or not selected_grades
        )
    ):
        found = {}
        diagnostics = []
        samples = []

        with st.spinner(
            "Searching PlayCricket "
            "for HBCC fixtures..."
        ):
            for grade in selected_grades:
                grade_id, team_id = (
                    all_grades[grade]
                )

                try:
                    payload = grade_matches(
                        grade_id
                    )

                    records = collect_matches(
                        payload
                    )

                    if records:
                        samples.append({
                            "grade": grade,
                            "matchSchedule": (
                                records[0].get(
                                    "matchSchedule"
                                )
                            )
                        })

                    stats = {
                        "grade": grade,
                        "records": len(records),
                        "without_date": 0,
                        "outside_dates": 0,
                        "matched": 0
                    }

                    for record in records:
                        mid = match_id(
                            record
                        )

                        if not mid:
                            continue

                        when = (
                            parse_date(
                                record.get(
                                    "matchSchedule"
                                )
                            )
                            or parse_date(
                                record
                            )
                        )

                        detail = None

                        if not when:
                            try:
                                detail = (
                                    match_detail(
                                        mid
                                    )
                                )

                                when = (
                                    parse_date(
                                        detail.get(
                                            "matchSummary"
                                        )
                                    )
                                    or parse_date(
                                        detail
                                    )
                                )

                            except Exception:
                                pass

                        if not when:
                            stats[
                                "without_date"
                            ] += 1
                            continue

                        if when not in dates:
                            stats[
                                "outside_dates"
                            ] += 1
                            continue

                        teams = (
                            teams_of(
                                record
                            )
                            or teams_of(
                                (
                                    detail
                                    or {}
                                ).get(
                                    "matchSummary"
                                )
                            )
                        )

                        if teams and not any(
                            hbcc_team(
                                team,
                                team_id
                            )
                            for team in teams
                        ):
                            continue

                        if mid not in found:
                            found[mid] = (
                                fixture_from(
                                    record,
                                    grade,
                                    team_id,
                                    detail
                                )
                            )

                            stats[
                                "matched"
                            ] += 1

                    diagnostics.append(
                        stats
                    )

                except Exception as error:
                    diagnostics.append({
                        "grade": grade,
                        "error": str(error)
                    })

            for mid in manual_ids(
                manual_urls
            ):
                if mid in found:
                    continue

                try:
                    detail = match_detail(
                        mid
                    )

                    summary = (
                        detail.get(
                            "matchSummary"
                        )
                        or {}
                    )

                    teams = (
                        teams_of(
                            summary
                        )
                        or teams_of(
                            detail
                        )
                    )

                    grade = next(
                        (
                            g
                            for g in selected_grades
                            if any(
                                hbcc_team(
                                    team,
                                    all_grades[g][1]
                                )
                                for team in teams
                            )
                        ),
                        selected_grades[0]
                    )

                    record = dict(
                        summary or detail
                    )

                    record["id"] = mid

                    found[mid] = (
                        fixture_from(
                            record,
                            grade,
                            all_grades[grade][1],
                            detail
                        )
                    )

                except Exception as error:
                    diagnostics.append({
                        "manual_match": mid,
                        "error": str(error)
                    })

        st.session_state.fixtures = sorted(
            found.values(),
            key=lambda x: (
                x["date"],
                x["grade"]
            )
        )

        st.session_state.selected_fixture_ids = [
            fixture["id"]
            for fixture in (
                st.session_state.fixtures
            )
        ]

        st.session_state.diagnostics = (
            diagnostics
        )

        st.session_state.schedule_samples = (
            samples
        )

        st.session_state.selection_rows = []
        st.session_state.article = ""
        st.session_state.workflow = "review"

        for key in list(
            st.session_state
        ):
            if key.startswith(
                "match_checkbox_"
            ):
                del st.session_state[key]

        st.rerun()


# ============================================================
# STEP 2 — REVIEW FIXTURES
# ============================================================

elif stage == "review":
    st.markdown("### Review your matches")

    fixtures = (
        st.session_state.fixtures
    )

    if not fixtures:
        st.warning(
            "No matches found for "
            "those dates and teams."
        )

        st.caption(
            "Try another date range "
            "or add a manual PlayCricket "
            "match link in Advanced settings."
        )

    else:
        metric1, metric2, metric3 = (
            st.columns(3)
        )

        metric1.metric(
            "Fixtures found",
            len(fixtures)
        )

        metric2.metric(
            "Grades",
            len({
                f["grade"]
                for f in fixtures
            })
        )

        metric3.metric(
            "Selected",
            len(
                st.session_state.selected_fixture_ids
            )
        )

        col1, col2, _ = st.columns(
            [1, 1, 3]
        )

        with col1:
            if st.button(
                "Select all",
                use_container_width=True
            ):
                st.session_state.selected_fixture_ids = [
                    f["id"]
                    for f in fixtures
                ]

                for f in fixtures:
                    st.session_state[
                        "match_checkbox_"
                        + f["id"]
                    ] = True

                st.rerun()

        with col2:
            if st.button(
                "Deselect all",
                use_container_width=True
            ):
                st.session_state.selected_fixture_ids = []

                for f in fixtures:
                    st.session_state[
                        "match_checkbox_"
                        + f["id"]
                    ] = False

                st.rerun()

        for grade in dict.fromkeys(
            f["grade"]
            for f in fixtures
        ):
            st.markdown(
                f"#### {grade}"
            )

            grade_fixtures = [
                f
                for f in fixtures
                if f["grade"] == grade
            ]

            for fixture in grade_fixtures:
                mid = fixture["id"]
                key = (
                    "match_checkbox_"
                    + mid
                )

                with st.container(
                    border=True
                ):
                    col, status_col = (
                        st.columns(
                            [5, 1]
                        )
                    )

                    with col:
                        checked = (
                            st.checkbox(
                                fixture["name"],
                                value=(
                                    mid in
                                    st.session_state.selected_fixture_ids
                                ),
                                key=key
                            )
                        )

                        st.caption(
                            f"{fixture['date']} · "
                            f"{fixture['venue'] or 'Venue unavailable'}"
                        )

                        st.markdown(
                            f"[Open PlayCricket]"
                            f"({fixture['url']})"
                        )

                    with status_col:
                        st.caption(
                            fixture["status"]
                            or "Fixture"
                        )

                    if (
                        checked
                        and mid not in
                        st.session_state.selected_fixture_ids
                    ):
                        st.session_state.selected_fixture_ids.append(
                            mid
                        )

                    elif (
                        not checked
                        and mid in
                        st.session_state.selected_fixture_ids
                    ):
                        st.session_state.selected_fixture_ids.remove(
                            mid
                        )

        st.divider()

        if st.button(
            "Continue to Create Content",
            type="primary",
            use_container_width=True,
            disabled=(
                not st.session_state.selected_fixture_ids
            )
        ):
            st.session_state.workflow = (
                "export"
            )

            st.rerun()


# ============================================================
# STEP 3 — CREATE & EXPORT
# ============================================================

else:
    if st.button(
        "Back to fixtures"
    ):
        st.session_state.workflow = (
            "review"
        )

        st.rerun()

    chosen = [
        fixture
        for fixture in (
            st.session_state.fixtures
        )
        if fixture["id"]
        in st.session_state.selected_fixture_ids
    ]

    mode = (
        st.session_state.content_mode
    )

    st.markdown(
        f"### {mode}"
    )

    st.caption(
        f"{len(chosen)} match(es) selected"
    )

    if mode in (
        "Match Report",
        "Weekend Wrap"
    ):
        if (
            mode == "Match Report"
            and chosen
        ):
            picked = st.selectbox(
                "Match to write about",
                chosen,
                format_func=lambda f: (
                    f"{f['grade']} · "
                    f"{f['name']}"
                )
            )

            report_fixtures = [
                picked
            ]

        else:
            report_fixtures = (
                chosen
            )

        with st.expander(
            "Writing preferences",
            expanded=True
        ):
            context = st.text_area(
                "Additional context",
                placeholder=(
                    "Milestones, debuts, "
                    "achievements, club events..."
                )
            )

            avoid = st.text_area(
                "Anything to avoid?",
                height=80
            )

            length = st.select_slider(
                "Approximate article length",
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
            use_container_width=True,
            disabled=(
                not report_fixtures
            )
        ):
            try:
                with st.spinner(
                    "Reading scorecards "
                    "and writing..."
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
                st.error(
                    "Unable to generate article: "
                    f"{error}"
                )

        if st.session_state.article:
            st.success(
                "Article ready for review"
            )

            st.markdown(
                st.session_state.article
            )

            with st.expander(
                "Edit article before exporting"
            ):
                st.text_area(
                    "Article text",
                    key="article",
                    height=450
                )

            st.download_button(
                "Download article",
                st.session_state.article,
                "hbcc_report.txt",
                "text/plain",
                use_container_width=True
            )

            st.caption(
                "Verify scores and match facts "
                "before publishing."
            )

    else:
        if st.button(
            "Retrieve Selected Teams",
            type="primary",
            use_container_width=True,
            disabled=not chosen
        ):
            rows = []

            with st.spinner(
                "Retrieving published team lists..."
            ):
                for fixture in chosen:
                    row = dict(
                        fixture
                    )

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
                                detail.get(
                                    "matchSummary"
                                )
                                or {}
                            )
                        )

                    except Exception as error:
                        row["error"] = (
                            str(error)
                        )

                    rows.append(
                        row
                    )

            st.session_state.selection_rows = (
                rows
            )

        if st.session_state.selection_rows:
            edited_rows = []
            total_players = 0

            for row in (
                st.session_state.selection_rows
            ):
                mid = row["id"]
                candidates = (
                    row["candidates"]
                )

                with st.expander(
                    f"{row['grade']} · "
                    f"{row['name']}",
                    expanded=(
                        len(
                            st.session_state.selection_rows
                        ) <= 3
                    )
                ):
                    st.caption(
                        f"{row['date']} · "
                        f"{row['venue'] or 'Venue unavailable'}"
                    )

                    if row["error"]:
                        st.error(
                            row["error"]
                        )

                    if candidates:
                        selected = (
                            st.selectbox(
                                "Team list source",
                                list(
                                    range(
                                        len(candidates)
                                    )
                                ),
                                format_func=lambda i: (
                                    f"{len(candidates[i]['players'])} "
                                    f"players · "
                                    f"{candidates[i]['source']}"
                                ),
                                key=(
                                    "source_"
                                    + mid
                                )
                            )
                        )

                        candidate = (
                            candidates[selected]
                        )

                        source_players = (
                            candidate["players"]
                        )

                        if not candidate["explicit"]:
                            st.caption(
                                "General player list — "
                                "verify against the published team."
                            )

                        if not any(
                            "(c)" in player
                            for player in source_players
                        ):
                            st.caption(
                                "Captain not detected. "
                                "Add (c) after the captain's "
                                "name if required."
                            )

                    else:
                        source_players = []

                        st.info(
                            "Selected team coming soon. "
                            "You can add players manually "
                            "when published."
                        )

                    edit_key = (
                        "player_edit_"
                        + mid
                    )

                    source_key = (
                        "player_source_"
                        + mid
                    )

                    signature = tuple(
                        source_players
                    )

                    if (
                        st.session_state.get(
                            source_key
                        )
                        != signature
                    ):
                        st.session_state[
                            edit_key
                        ] = (
                            "\n".join(
                                source_players
                            )
                        )

                        st.session_state[
                            source_key
                        ] = signature

                    raw = st.text_area(
                        "Published players "
                        "(one per line; mark captain with (c))",
                        key=edit_key,
                        height=max(
                            260,
                            len(
                                source_players
                            ) * 28 + 55
                        )
                    )

                    players = [
                        player.strip()
                        for player in (
                            raw.splitlines()
                        )
                        if player.strip()
                    ]

                    total_players += (
                        len(players)
                    )

                    st.caption(
                        f"{len(players)} players · "
                        f"[Verify on PlayCricket]"
                        f"({row['url']})"
                    )

                    with st.expander(
                        "Technical player list sources"
                    ):
                        st.json([
                            {
                                "source": c["source"],
                                "players": c["players"]
                            }
                            for c in candidates
                        ])

                    edited_rows.append({
                        **row,
                        "players": players
                    })

            output = (
                selection_output(
                    edited_rows
                )
            )

            st.divider()

            st.markdown(
                "### Combined Team Announcement"
            )

            st.caption(
                f"{len(edited_rows)} teams · "
                f"{total_players} players"
            )

            st.code(
                output,
                language=None,
                wrap_lines=True
            )

            with st.expander(
                "Edit or copy announcement"
            ):
                if (
                    st.session_state.get(
                        "combined_source"
                    )
                    != output
                ):
                    st.session_state[
                        "combined_edit"
                    ] = output

                    st.session_state[
                        "combined_source"
                    ] = output

                st.text_area(
                    "Copy-ready selections",
                    key="combined_edit",
                    height=450
                )

            st.download_button(
                "Download Team Selections",
                st.session_state.get(
                    "combined_edit",
                    output
                ),
                "hbcc_team_selections.txt",
                "text/plain",
                use_container_width=True
            )

            st.caption(
                "Verify player lists and captains "
                "before publishing."
            )


# ============================================================
# DIAGNOSTICS AND FOOTER
# ============================================================

if st.session_state.diagnostics:
    with st.sidebar.expander(
        "PlayCricket search diagnostics"
    ):
        st.json(
            st.session_state.diagnostics
        )

        st.json(
            st.session_state.schedule_samples
        )

st.divider()

st.caption(
    "HBCC Content Studio · Built for the Hawks · "
    "PlayCricket data should be verified before publishing."
)
