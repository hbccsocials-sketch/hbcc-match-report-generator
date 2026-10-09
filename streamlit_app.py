
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
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="collapsed"
)

BASE = "https://grassrootsapiproxy.cricket.com.au/scores"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}


GRADES = {
    # WOMEN'S TEAMS
    "Women's 1st XI": (
        "9b70ae63-142e-492b-b266-f42590204e93",
        "121436ac-b8db-40c5-9fdc-2bac4439419a"
    ),
    "Women's 2nd XI": (
        "71054210-a0e9-46ef-aa44-44c5b7bdff97",
        "9fc4af1a-c13a-4f23-9a0e-8cf10915843d"
    ),

    # MEN'S SATURDAY TEAMS
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

    # VETERANS TEAMS
    "Men's Over 40s 1st XI": (
        "30f3da7c-1e66-41ea-9ae1-9fe5691eecc1",
        "25587b62-d7c6-4916-a2ed-6b42fed0434a"
    ),
    "Men's Over 50s 1st XI": (
        "9a0cd4bf-0ad5-4a15-acd3-e7d781c6e76a",
        "1b9fb198-ddc4-4117-a183-3bee9fae7d94"
    ),

    # SUNDAY TEAMS
    "Men's Sunday 1st XI": (
        "b5df35ef-35b6-41c0-b736-40c51839acdb",
        "2ba73357-c3fd-41af-a9df-6648e8d7112b"
    ),
    "Men's Sunday 2nd XI": (
        "4a84a74b-f201-481a-93a9-cc61dd14cad0",
        "018cab89-ff43-4cac-8d1a-c96ee5579b39"
    ),

    # ALL ABILITIES
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
# CLUB BRANDING
# ============================================================

st.markdown(
    """
<style>
.stApp {
    background: #f5f7fa;
    color: #18263a;
}

.block-container {
    max-width: 1230px;
    padding-top: 1.4rem;
    padding-bottom: 3rem;
}

.hbcc-hero {
    background: linear-gradient(115deg, #0f192d, #203a5b);
    padding: 32px 36px;
    border-radius: 18px;
    border-bottom: 5px solid #c8102e;
    margin-bottom: 23px;
}

.hbcc-eyebrow {
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1.8px;
    color: #f5b82e;
}

.hbcc-hero h1 {
    font-size: 2.45rem;
    margin: 8px 0 10px;
    color: white !important;
}

.hbcc-hero p {
    color: #d3dce9;
    margin: 0;
}

.hbcc-steps {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 22px;
}

.hbcc-step {
    background: white;
    border: 1px solid #e0e6ee;
    border-radius: 12px;
    padding: 12px 16px;
    font-size: 13px;
    color: #40516b;
    flex: 1;
    min-width: 165px;
}

.hbcc-step strong {
    color: #c8102e;
    margin-right: 8px;
}

.hbcc-section {
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1.4px;
    color: #c8102e;
    margin: 14px 0;
}

.stButton > button[kind="primary"] {
    background: #c8102e;
    border-color: #c8102e;
    color: white;
    font-weight: 700;
    border-radius: 10px;
}

.stButton > button[kind="primary"]:hover {
    background: #a30e27;
    border-color: #a30e27;
}

.stDownloadButton > button {
    border: 1px solid #0f192d;
    color: #0f192d;
    border-radius: 10px;
    font-weight: 650;
}

div[data-testid="stMetric"] {
    background: white;
    border: 1px solid #e4e8ef;
    padding: 13px;
    border-radius: 12px;
}
</style>
""",
    unsafe_allow_html=True
)

st.markdown(
    '<div class="hbcc-hero">'
    '<div class="hbcc-eyebrow">'
    'HAWTHORN BOROONDARA CRICKET CLUB'
    '</div>'
    '<h1>Content Studio</h1>'
    '<p>From PlayCricket to publish-ready club content.</p>'
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
        keys = (
            "displayName",
            "fullName",
            "name",
            "playerName",
            "participantName",
            "teamName",
            "shortName",
            "playerShortName",
            "title"
        )

        for key in keys:
            result = value.get(key)

            if isinstance(result, str) and result.strip():
                return result.strip()

        for key in ("player", "participant", "team", "person"):
            nested = value.get(key)

            if isinstance(nested, dict):
                result = name_of(nested)

                if result:
                    return result

    return ""


def parse_date(value):
    """
    Read fixture dates from PlayCricket matchSchedule.
    Supports nested dictionaries, lists and timestamps.
    """

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
                except (TypeError, ValueError):
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
        value = obj.get(key)

        if isinstance(value, list):
            return [
                team
                for team in value
                if isinstance(team, dict)
            ]

    return [
        obj[key]
        for key in ("homeTeam", "awayTeam")
        if isinstance(obj.get(key), dict)
    ]


def hbcc_team(obj, expected_id):
    if not isinstance(obj, dict):
        return False

    identifiers = [
        str(obj.get(key) or "")
        for key in ("id", "teamId", "clubTeamId")
    ]

    name = name_of(obj).lower()

    return (
        (bool(expected_id) and expected_id in identifiers)
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

            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)

    return list(found.values())


def fixture_from(record, grade, team_id, detail=None):
    summary = (detail or {}).get("matchSummary") or {}

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

        mid = found.group(1) if found else token

        if UUID.fullmatch(mid) and mid not in identifiers:
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

PLAYER_ID_KEYS = (
    "playerId",
    "participantId",
    "id"
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

    players = []
    seen = set()

    for item in items:
        name = name_of(item)

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

        player_refs = {
            name.lower(),
            clean_name.lower()
        }

        if isinstance(item, dict):
            for key in PLAYER_ID_KEYS:
                if item.get(key):
                    player_refs.add(
                        str(item[key]).lower()
                    )

            for nested_key in ("player", "participant"):
                nested = item.get(nested_key)

                if isinstance(nested, dict):
                    for key in PLAYER_ID_KEYS:
                        if nested.get(key):
                            player_refs.add(
                                str(nested[key]).lower()
                            )

        is_captain = (
            captain_flag(item)
            or bool(player_refs & captain_refs)
            or bool(re.search(r"\(c\)", name, re.I))
        )

        if clean_name.lower() in seen:
            continue

        seen.add(clean_name.lower())

        players.append(
            clean_name + (" (c)" if is_captain else "")
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
            for index, value in enumerate(obj):
                if isinstance(value, (dict, list)):
                    walk(
                        value,
                        f"{path}[{index}]",
                        depth + 1
                    )

    walk(detail)

    return sorted(
        candidates,
        key=lambda item: (
            item["score"],
            len(item["players"])
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
                f"{index}. {name}"
                for index, name in enumerate(
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
# MATCH REPORT DATA
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
                            "runs": player.get("runsScored"),
                            "balls": player.get("ballsFaced"),
                            "dismissal": player.get("dismissalText")
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
                            "wickets": player.get("wicketsTaken"),
                            "runs": player.get("runsConceded"),
                            "overs": player.get("oversBowled")
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
                        "over": value.get("overNumber"),
                        "ball": value.get("ballNumber"),
                        "score": value.get("progressRuns"),
                        "wickets": value.get(
                            "progressWickets"
                        ),
                        "runsBat": value.get("runsBat"),
                        "wicket": bool(
                            value.get("dismissedParticipantId")
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
    detail = match_detail(fixture["id"])

    summary = detail.get("matchSummary") or {}

    if not isinstance(summary, dict):
        summary = {}

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

Use verified scores, batting, bowling and
ball-by-ball moments to tell the match story.

For a weekend report:
- Cover every supplied fixture.
- Include sections for each grade.
- Write a cohesive club-wide narrative.

Use only supplied facts.

Never invent results, partnerships, quotes,
weather, pitch conditions, tactics,
player identities or injuries.

Treat official result text as authoritative.
Never infer a result from an unfinished match.

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
        f"Gemini could not generate the article: {last_error}"
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
# ADVANCED SETTINGS
# ============================================================

with st.sidebar:
    st.markdown("### ⚙️ Advanced settings")

    st.caption(
        "Use these settings to add grades or "
        "troubleshoot PlayCricket."
    )

    additional = st.text_area(
        "Additional grades",
        placeholder="Men's 3rd XI | GRADE_ID | TEAM_ID",
        height=110
    )

    st.caption(
        "One grade per line: "
        "Grade name | Grade ID | Team ID"
    )

    if st.button("Clear PlayCricket cache"):
        st.cache_data.clear()
        st.success("Cache cleared")


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
# STEP 1 — CHOOSE CONTENT
# ============================================================

st.markdown(
    '<div class="hbcc-steps">'
    '<div class="hbcc-step"><strong>01</strong> Choose content</div>'
    '<div class="hbcc-step"><strong>02</strong> Find & review matches</div>'
    '<div class="hbcc-step"><strong>03</strong> Create & export</div>'
    '</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="hbcc-section">'
    'STEP 01 · CHOOSE YOUR CONTENT'
    '</div>',
    unsafe_allow_html=True
)

mode = st.radio(
    "Content type",
    [
        "Match Report",
        "Weekend Report",
        "Team Selections"
    ],
    horizontal=True,
    label_visibility="collapsed"
)

descriptions = {
    "Match Report": (
        "Create an article about one match using "
        "PlayCricket scores and match events."
    ),
    "Weekend Report": (
        "Bring multiple match results together "
        "into one club-wide weekend wrap."
    ),
    "Team Selections": (
        "Retrieve published players, review captains "
        "and prepare a combined team announcement."
    )
}

st.caption(descriptions[mode])


# ============================================================
# STEP 2 — FIND MATCHES
# ============================================================

st.markdown(
    '<div class="hbcc-section">'
    'STEP 02 · FIND & REVIEW MATCHES'
    '</div>',
    unsafe_allow_html=True
)

left, right = st.columns(2)

with left:
    st.markdown("#### Match dates")

    date_choice = st.radio(
        "When are the matches?",
        [
            "This Weekend",
            "Last Weekend",
            "Choose Dates"
        ],
        horizontal=True
    )

    today = date.today()

    # Monday=0, Saturday=5, Sunday=6
    days_until_saturday = (5 - today.weekday()) % 7
    this_saturday = today + timedelta(
        days=days_until_saturday
    )

    dates = set()

    if date_choice == "This Weekend":
        dates = {
            this_saturday,
            this_saturday + timedelta(days=1)
        }

    elif date_choice == "Last Weekend":
        last_saturday = (
            this_saturday - timedelta(days=7)
        )

        dates = {
            last_saturday,
            last_saturday + timedelta(days=1)
        }

    else:
        date_method = st.radio(
            "Custom date method",
            [
                "Date range",
                "Individual dates"
            ],
            horizontal=True
        )

        if date_method == "Date range":
            chosen_dates = st.date_input(
                "Match date range",
                value=(
                    today,
                    today + timedelta(days=1)
                )
            )

            if (
                isinstance(chosen_dates, (tuple, list))
                and len(chosen_dates) == 2
            ):
                start, end = chosen_dates

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
                selected_date = st.date_input(
                    f"Date {i + 1}",
                    value=today + timedelta(days=i),
                    key=f"custom_date_{i}"
                )

                dates.add(selected_date)

    if dates:
        st.caption(
            "Searching: "
            + ", ".join(
                day.strftime("%d %b %Y")
                for day in sorted(dates)
            )
        )


with right:
    st.markdown("#### Select grades")

    grade_choice = st.radio(
        "Which teams?",
        [
            "All Grades",
            "Men's Teams",
            "Women's Teams",
            "All Abilities",
            "Custom"
        ],
        horizontal=True
    )

    if grade_choice == "All Grades":
        selected_grades = list(all_grades)

    elif grade_choice == "Men's Teams":
        selected_grades = [
            grade
            for grade in all_grades
            if "men's" in grade.lower()
            and "women's" not in grade.lower()
        ]

    elif grade_choice == "Women's Teams":
        selected_grades = [
            grade
            for grade in all_grades
            if "women's" in grade.lower()
        ]

    else:
        elif grade_choice == "All Abilities":
    selected_grades = [
        grade
        for grade in all_grades
        if "all abilities" in grade.lower()
    ],
        
        selected_grades = st.multiselect(
            "Choose individual grades",
            list(all_grades),
            default=list(GRADES)
        )

    st.caption(
        f"{len(selected_grades)} grade(s) selected"
    )

    with st.expander("View selected grades"):
        for grade in selected_grades:
            st.write("✓", grade)


with st.expander(
    "Advanced · Add match links manually"
):
    manual_urls = st.text_area(
        "PlayCricket match URLs",
        placeholder=(
            "https://play.cricket.com.au/match/..."
        ),
        height=90
    )

    st.caption(
        "Use this if a fixture cannot be found automatically."
    )


# ============================================================
# SEARCH FIXTURES
# ============================================================

search_clicked = st.button(
    "🔎 Find HBCC Matches",
    type="primary",
    use_container_width=True,
    disabled=not dates or not selected_grades
)

if search_clicked:
    found = {}
    diagnostics = []
    samples = []

    with st.spinner("Searching PlayCricket fixtures..."):
        for grade in selected_grades:
            grade_id, team_id = all_grades[grade]

            try:
                payload = grade_matches(grade_id)
                records = collect_matches(payload)

                if records:
                    samples.append({
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

                    # IMPORTANT:
                    # MatchSchedule is checked first.
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
                        hbcc_team(team, team_id)
                        for team in teams
                    ):
                        stats["other_team"] += 1
                        continue

                    if mid not in found:
                        found[mid] = fixture_from(
                            record,
                            grade,
                            team_id,
                            detail
                        )

                        stats["matched"] += 1

                diagnostics.append(stats)

            except Exception as error:
                diagnostics.append({
                    "grade": grade,
                    "error": str(error)
                })

        # Optional manual match links
        for mid in manual_ids(manual_urls):
            if mid in found:
                continue

            try:
                detail = match_detail(mid)

                summary = (
                    detail.get("matchSummary") or {}
                )

                teams = (
                    teams_of(summary)
                    or teams_of(detail)
                )

                grade = next(
                    (
                        candidate
                        for candidate in selected_grades
                        if any(
                            hbcc_team(
                                team,
                                all_grades[candidate][1]
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
    st.session_state.schedule_samples = samples
    st.session_state.selection_rows = []
    st.session_state.article = ""


# ============================================================
# FIXTURE REVIEW
# ============================================================

fixtures = st.session_state.fixtures
chosen = []

if fixtures:
    metric1, metric2 = st.columns(2)

    metric1.metric(
        "Fixtures found",
        len(fixtures)
    )

    metric2.metric(
        "Grades represented",
        len({
            fixture["grade"]
            for fixture in fixtures
        })
    )

    st.markdown("#### Review matches")

    st.caption(
        "Untick any fixtures you don't want included."
    )

    select_all = st.checkbox(
        "Select all fixtures",
        value=True,
        key="select_all_fixtures"
    )

    for fixture in fixtures:
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])

            with col1:
                checked = st.checkbox(
                    f"{fixture['grade']} · {fixture['name']}",
                    value=select_all,
                    key=(
                        f"fixture_{fixture['id']}_{select_all}"
                    )
                )

                st.caption(
                    f"📅 {fixture['date'] or 'Date unavailable'}"
                    f" · 📍 {fixture['venue'] or 'Venue unavailable'}"
                    f" · [PlayCricket]({fixture['url']})"
                )

            with col2:
                st.caption(
                    fixture["status"] or "Fixture"
                )

            if checked:
                chosen.append(fixture)

    st.caption(
        f"{len(chosen)} match(es) selected "
        f"for {mode.lower()}."
    )

elif search_clicked:
    st.warning(
        "No fixtures found for those dates and grades. "
        "Check Advanced diagnostics or enter a match link."
    )

else:
    st.info(
        "Choose your dates and grades, "
        "then click **Find HBCC Matches**."
    )


# ============================================================
# TECHNICAL DIAGNOSTICS
# ============================================================

if st.session_state.diagnostics:
    with st.expander(
        "🔧 Advanced · PlayCricket diagnostics"
    ):
        st.write("Fixture search results")

        st.json(
            st.session_state.diagnostics
        )

        st.write("Actual matchSchedule data")

        st.json(
            st.session_state.schedule_samples
        )


# ============================================================
# STEP 3 — CREATE CONTENT
# ============================================================

st.divider()

st.markdown(
    '<div class="hbcc-section">'
    'STEP 03 · CREATE & EXPORT'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# MATCH REPORT / WEEKEND WRAP
# ============================================================

if mode in (
    "Match Report",
    "Weekend Report"
):
    if mode == "Match Report" and chosen:
        picked = st.selectbox(
            "Match to write about",
            chosen,
            format_func=lambda fixture: (
                f"{fixture['grade']} · {fixture['name']}"
            )
        )

        report_fixtures = [picked]

    else:
        report_fixtures = chosen

    with st.expander(
        "✍️ Writing preferences",
        expanded=True
    ):
        context = st.text_area(
            "Extra context (optional)",
            placeholder=(
                "Club milestones, debuts, achievements, "
                "special events..."
            )
        )

        avoid = st.text_area(
            "Anything to avoid? (optional)",
            height=80
        )

        length = st.select_slider(
            "Article length (approximate words)",
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

    button_label = (
        "✨ Generate Match Report"
        if mode == "Match Report"
        else "✨ Generate Weekend Wrap"
    )

    if st.button(
        button_label,
        type="primary",
        use_container_width=True,
        disabled=not report_fixtures
    ):
        try:
            with st.spinner(
                "Reading scorecards and writing your article..."
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
                f"Unable to generate the article: {error}"
            )

    if st.session_state.article:
        st.success(
            "Your article is ready to review."
        )

        st.subheader("Article preview")

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
            "⬇ Download article",
            st.session_state.article,
            "hbcc_report.txt",
            "text/plain",
            use_container_width=True
        )

        st.caption(
            "Check all match facts against "
            "PlayCricket before publishing."
        )


# ============================================================
# TEAM SELECTIONS
# ============================================================

else:
    st.caption(
        "Retrieve selections, check the published names "
        "and captain, then export all chosen grades together."
    )

    if st.button(
        "👥 Retrieve Selected Teams",
        type="primary",
        use_container_width=True,
        disabled=not chosen
    ):
        rows = []

        with st.spinner(
            "Retrieving HBCC team lists..."
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
        st.success(
            f"Retrieved "
            f"{len(st.session_state.selection_rows)} team(s). "
            "Review names before publishing."
        )

        edited_rows = []

        for row in st.session_state.selection_rows:
            mid = row["id"]
            candidates = row["candidates"]

            with st.expander(
                f"{row['grade']} · {row['name']}",
                expanded=(
                    len(st.session_state.selection_rows) <= 3
                )
            ):
                st.caption(
                    f"📅 {row['date']} · "
                    f"📍 {row['venue'] or 'Venue unavailable'}"
                )

                if row["error"]:
                    st.error(
                        "Unable to retrieve selection: "
                        + row["error"]
                    )

                if candidates:
                    selected = st.selectbox(
                        "Team list source",
                        list(range(len(candidates))),
                        format_func=lambda i: (
                            f"{len(candidates[i]['players'])} "
                            f"players · {candidates[i]['source']}"
                        ),
                        key="source_" + mid
                    )

                    candidate = candidates[selected]
                    source_players = candidate["players"]

                    if not candidate["explicit"]:
                        st.caption(
                            "This is a general team list. "
                            "Confirm it matches the published "
                            "PlayCricket selection."
                        )

                    if not any(
                        "(c)" in player
                        for player in source_players
                    ):
                        st.caption(
                            "Captain not identified in this "
                            "API field. Add **(c)** after "
                            "the captain's name if needed."
                        )

                else:
                    source_players = []

                    st.warning(
                        "No HBCC player list found. "
                        "You can enter the published "
                        "selection manually."
                    )

                edit_key = "player_edit_" + mid
                source_key = "player_source_" + mid

                signature = tuple(source_players)

                if (
                    st.session_state.get(source_key)
                    != signature
                ):
                    st.session_state[edit_key] = (
                        "\n".join(source_players)
                    )

                    st.session_state[source_key] = (
                        signature
                    )

                # Auto-size player list.
                raw = st.text_area(
                    "Selected players "
                    "(one per line; add (c) for captain)",
                    key=edit_key,
                    height=max(
                        260,
                        len(source_players) * 28 + 55
                    )
                )

                players = [
                    player.strip()
                    for player in raw.splitlines()
                    if player.strip()
                ]

                st.caption(
                    f"{len(players)} players · "
                    f"[Verify on PlayCricket]({row['url']})"
                )

                with st.expander(
                    "Technical details · Team selection sources"
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

        # ====================================================
        # COMBINED ANNOUNCEMENT
        # ====================================================

        output = selection_output(
            edited_rows
        )

        st.subheader(
            "Combined Team Announcement"
        )

        st.caption(
            f"{len(edited_rows)} teams · "
            f"{sum(len(row['players']) for row in edited_rows)} "
            "players · Full preview below"
        )

        # Full announcement, no internal scrolling.
        st.code(
            output,
            language=None,
            wrap_lines=True
        )

        with st.expander(
            "Edit or copy combined announcement"
        ):
            signature = output

            if (
                st.session_state.get("combined_source")
                != signature
            ):
                st.session_state["combined_edit"] = output
                st.session_state["combined_source"] = (
                    signature
                )

            st.text_area(
                "Copy-ready announcement",
                key="combined_edit",
                height=450
            )

        st.download_button(
            "⬇ Download Team Selections",
            st.session_state.get(
                "combined_edit",
                output
            ),
            "hbcc_team_selections.txt",
            "text/plain",
            use_container_width=True
        )

        st.caption(
            "Player lists and captains may change. "
            "Verify against PlayCricket before posting."
        )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "HBCC Content Studio · Built for the Hawks · "
    "Data sourced from PlayCricket. "
    "Always verify before publishing."
)
