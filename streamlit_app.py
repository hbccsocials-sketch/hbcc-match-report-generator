
import re
import json
import time
from datetime import date, timedelta, datetime
import requests
import streamlit as st
from google import genai
from google.genai import types


# =========================================================
# APP CONFIGURATION
# =========================================================

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

DEFAULT_GRADES = {
    "Women's 1st XI": {
        "grade": "9b70ae63-142e-492b-b266-f42590204e93",
        "team": "121436ac-b8db-40c5-9fdc-2bac4439419a"
    },
    "Men's 1st XI": {
        "grade": "f5b98728-e7b2-40e6-af73-ac2eae56cedf",
        "team": "94ced5f2-a9f0-4ab3-b8aa-c3d0a643deab"
    },
    "Men's 2nd XI": {
        "grade": "d67df9f0-a982-461f-b0ee-4aa6bd89b6a6",
        "team": "74392aeb-a39d-4f11-9d34-8bff6ca4690d"
    }
}


# =========================================================
# HBCC STYLING
# =========================================================

st.markdown(
    """
<style>
.stApp {
    background: #f5f7fa;
    color: #0f192d;
}

.block-container {
    max-width: 1150px;
    padding-top: 2rem;
}

.hbhero {
    background: linear-gradient(
        120deg,
        #0f192d,
        #172c4b
    );
    color: white;
    padding: 32px 36px;
    border-radius: 18px;
    border-bottom: 5px solid #c8102e;
    margin-bottom: 24px;
}

.hbhero h1 {
    color: white !important;
    font-size: 2.35rem;
    margin: 4px 0 10px;
}

.hbhero p {
    color: #e1e7f0;
    margin: 0;
}

.hbeyebrow {
    font-size: 12px;
    letter-spacing: 2px;
    color: #f5b82e;
    font-weight: 800;
}

[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 14px;
}

.stButton > button[kind="primary"] {
    background: #c8102e;
    border-color: #c8102e;
    color: white;
    border-radius: 10px;
    font-weight: 700;
}

[data-testid="stMetricValue"] {
    color: #0f192d;
}

.hbfoot {
    color: #667085;
    font-size: 12px;
    margin-top: 38px;
    border-top: 1px solid #d9dee7;
    padding-top: 15px;
}
</style>
""",
    unsafe_allow_html=True
)

# Keep HTML on one line to avoid Streamlit
# treating indented HTML as a code block.

st.markdown(
    '<div class="hbhero">'
    '<div class="hbeyebrow">'
    'HAWTHORN BOROONDARA CRICKET CLUB'
    '</div>'
    '<h1>Content Studio</h1>'
    '<p>Find your fixtures by date and grade. '
    'Generate match reports, weekend wrap-ups '
    'and team selections.</p>'
    '</div>',
    unsafe_allow_html=True
)


# =========================================================
# PLAYCRICKET API
# =========================================================

@st.cache_data(ttl=900, show_spinner=False)
def get_json(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=900, show_spinner=False)
def grade_fixtures(grade_id):
    return get_json(
        f"{BASE}/grades/{grade_id}/matches"
        "?jsconfig=eccn%3Atrue"
    )


@st.cache_data(ttl=900, show_spinner=False)
def match_detail(match_id):
    return get_json(
        f"{BASE}/matches/{match_id}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )


@st.cache_data(ttl=900, show_spinner=False)
def ball_detail(match_id):
    return get_json(
        f"{BASE}/matches/{match_id}/balls"
        "?jsconfig=eccn%3Atrue"
    )


# =========================================================
# GENERAL HELPERS
# =========================================================

def textval(value):
    if isinstance(value, dict):
        return str(
            value.get("displayName")
            or value.get("name")
            or value.get("shortName")
            or value.get("value")
            or ""
        )
    return str(value or "")


def first(data, *keys):
    if not isinstance(data, dict):
        return None

    for key in keys:
        if data.get(key) is not None:
            return data[key]

    return None


def iso_date(value):
    if isinstance(value, dict):
        for key in (
            "startDate",
            "startDateTime",
            "date",
            "scheduledStart",
            "matchDate"
        ):
            if key in value:
                result = iso_date(value[key])
                if result:
                    return result
        return None

    if isinstance(value, (int, float)):
        try:
            timestamp = (
                value / 1000
                if value > 1e11
                else value
            )
            return datetime.fromtimestamp(
                timestamp
            ).date()
        except Exception:
            return None

    if isinstance(value, str):
        match = re.search(
            r"(\d{4}-\d{2}-\d{2})",
            value
        )
        if match:
            try:
                return date.fromisoformat(
                    match.group(1)
                )
            except ValueError:
                pass

    return None


def find_match_objects(payload):
    found = []
    seen = set()

    def walk(obj):
        if isinstance(obj, dict):
            mid = first(
                obj,
                "matchId",
                "matchID"
            )

            if (
                not mid
                and isinstance(obj.get("id"), str)
                and (
                    "homeTeam" in obj
                    or "awayTeam" in obj
                    or "teams" in obj
                    or "matchDate" in obj
                )
            ):
                mid = obj["id"]

            if (
                isinstance(mid, str)
                and re.fullmatch(
                    r"[0-9a-fA-F-]{36}",
                    mid
                )
                and mid not in seen
            ):
                found.append(obj)
                seen.add(mid)
                return

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(payload)
    return found


def extract_teams(match):
    teams = first(
        match,
        "teams",
        "teamSummaries"
    )

    if isinstance(teams, list):
        return teams

    return [
        team
        for team in [
            first(
                match,
                "homeTeam",
                "homeTeamSummary"
            ),
            first(
                match,
                "awayTeam",
                "awayTeamSummary"
            )
        ]
        if isinstance(team, dict)
    ]


def match_label(match):
    teams = extract_teams(match)
    names = [textval(team) for team in teams]

    return (
        " vs ".join(
            name for name in names if name
        )
        or textval(
            first(match, "name", "title")
        )
        or "Fixture"
    )


def match_date(match):
    for key in (
        "startDateTime",
        "startDate",
        "matchDate",
        "date",
        "scheduledStart",
        "schedule",
        "matchSchedule"
    ):
        result = iso_date(match.get(key))
        if result:
            return result

    return None


def match_id(match):
    return first(
        match,
        "matchId",
        "matchID",
        "id"
    )


def fixture_url(mid):
    return (
        "https://play.cricket.com.au/"
        f"match/{mid}"
    )


def hb_team(teams, team_id=""):
    for team in teams:
        if (
            team_id
            and str(
                first(team, "id", "teamId")
            ) == team_id
        ):
            return team

    for team in teams:
        if (
            "hawthorn boroondara"
            in textval(team).lower()
        ):
            return team

    return None


# =========================================================
# TEAM SELECTION EXTRACTION
# =========================================================

def extract_players(detail, team_id):
    """
    Extract players only from an identifiable
    HBCC team selection.

    Never infer a selected team from scorecards.
    """

    candidates = []

    def walk(obj, depth=0):
        if depth > 10:
            return

        if isinstance(obj, dict):
            teams = extract_teams(obj)

            for team in teams:
                if team is hb_team(
                    [team],
                    team_id
                ):
                    for key in (
                        "players",
                        "selectedPlayers",
                        "lineup",
                        "lineUp",
                        "teamSelection",
                        "participants",
                        "selectedParticipants"
                    ):
                        value = team.get(key)

                        if isinstance(value, dict):
                            value = first(
                                value,
                                "players",
                                "participants",
                                "selectedPlayers"
                            )

                        if isinstance(value, list):
                            candidates.append(value)

            for key, value in obj.items():
                if key in (
                    "batting",
                    "bowling",
                    "scorecard",
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

    for player_list in candidates:
        names = []

        for player in player_list:
            if isinstance(player, str):
                name = player
                captain = False

            elif isinstance(player, dict):
                name = textval(
                    first(
                        player,
                        "player",
                        "participant",
                        "playerName",
                        "participantName",
                        "displayName",
                        "name",
                        "fullName",
                        "playerShortName"
                    )
                )

                captain = bool(
                    first(
                        player,
                        "isCaptain",
                        "captain"
                    )
                )

                if not captain:
                    role = str(
                        first(
                            player,
                            "role",
                            "teamRole"
                        ) or ""
                    ).lower()

                    captain = role == "captain"
            else:
                continue

            if (
                name
                and "*" not in name
                and name.lower() not in (
                    "none",
                    "private player"
                )
            ):
                if (
                    captain
                    and "(c)" not in name
                ):
                    name += " (c)"

                names.append(name)

        names = list(dict.fromkeys(names))

        if names:
            return names

    return []


# =========================================================
# SCORECARD ANALYSIS
# =========================================================

def score_summary(detail):
    summary = detail.get(
        "matchSummary",
        {}
    )

    teams = (
        summary.get("teams", [])
        or extract_teams(detail)
    )

    return {
        "result": textval(
            summary.get("resultText")
        ),
        "teams": [
            {
                "name": textval(team),
                "score": textval(
                    first(
                        team,
                        "scoreText",
                        "score"
                    )
                )
            }
            for team in teams
        ]
    }


def score_innings(detail):
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            if (
                isinstance(
                    obj.get("batting"),
                    list
                )
                and "runsScored" in obj
            ):
                found.append(obj)
                return

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(detail)

    result = []

    for innings in found:
        batting = []
        bowling = []

        for player in innings.get(
            "batting",
            []
        ):
            name = textval(
                first(
                    player,
                    "playerShortName",
                    "playerName"
                )
            )

            if name and "*" not in name:
                batting.append({
                    "name": name,
                    "runs": player.get(
                        "runsScored",
                        0
                    ),
                    "balls": player.get(
                        "ballsFaced",
                        0
                    ),
                    "dismissal": textval(
                        player.get(
                            "dismissalText"
                        )
                    )
                })

        for player in innings.get(
            "bowling",
            []
        ):
            name = textval(
                first(
                    player,
                    "playerShortName",
                    "playerName"
                )
            )

            if name and "*" not in name:
                bowling.append({
                    "name": name,
                    "wickets": player.get(
                        "wicketsTaken",
                        0
                    ),
                    "runs": player.get(
                        "runsConceded",
                        0
                    ),
                    "overs": player.get(
                        "oversBowled",
                        0
                    )
                })

        result.append({
            "runs": innings.get(
                "runsScored"
            ),
            "wickets": innings.get(
                "numberOfWicketsFallen"
            ),
            "top_batting": sorted(
                batting,
                key=lambda x: x["runs"] or 0,
                reverse=True
            )[:6],
            "top_bowling": sorted(
                bowling,
                key=lambda x: x["wickets"] or 0,
                reverse=True
            )[:6]
        })

    return result


# =========================================================
# BALL-BY-BALL ANALYSIS
# =========================================================

def ball_events(payload):
    if not isinstance(payload, dict):
        return []

    result = []

    for innings in payload.get(
        "innings",
        []
    ):
        events = []

        def walk(obj):
            if isinstance(obj, dict):
                if (
                    "overNumber" in obj
                    and "ballNumber" in obj
                    and "progressRuns" in obj
                ):
                    if (
                        obj.get(
                            "dismissedParticipantId"
                        )
                        or (
                            obj.get("runsBat") or 0
                        ) >= 4
                    ):
                        events.append({
                            "over": obj.get(
                                "overNumber"
                            ),
                            "ball": obj.get(
                                "ballNumber"
                            ),
                            "score": obj.get(
                                "progressRuns"
                            ),
                            "wickets": obj.get(
                                "progressWickets"
                            ),
                            "runs_bat": obj.get(
                                "runsBat"
                            ),
                            "wicket": bool(
                                obj.get(
                                    "dismissedParticipantId"
                                )
                            )
                        })
                    return

                for value in obj.values():
                    walk(value)

            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(
            innings.get("balls", [])
        )

        result.append({
            "innings": textval(
                innings.get("inningsName")
            ),
            "key_deliveries": events[:80]
        })

    return result


def report_data(
    match,
    detail,
    include_balls=True
):
    data = {
        "fixture": match_label(match),
        "date": str(
            match_date(match) or ""
        ),
        "grade": match.get(
            "_grade",
            ""
        ),
        "official_score": score_summary(
            detail
        ),
        "innings": score_innings(
            detail
        )
    }

    if include_balls:
        try:
            data["ball_by_ball"] = (
                ball_events(
                    ball_detail(
                        match_id(match)
                    )
                )
            )
        except Exception:
            data["ball_by_ball_status"] = (
                "Unavailable; use scorecard only"
            )

    return data


# =========================================================
# GEMINI ARTICLE GENERATION
# =========================================================

def gemini_report(
    data,
    mode,
    length,
    context,
    avoid
):
    api_key = st.secrets.get(
        "GEMINI_API_KEY",
        ""
    )

    if not api_key:
        raise RuntimeError(
            "Add GEMINI_API_KEY to Streamlit "
            "Secrets before generating articles."
        )

    client = genai.Client(
        api_key=api_key
    )

    if mode == "Match Report":
        task = "ONE cricket match report"
    else:
        task = (
            "ONE cohesive weekend report "
            "covering every supplied fixture"
        )

    prompt = f"""
You are writing for Hawthorn Boroondara
Cricket Club, known as the HB Hawks.

Write {task} of approximately {length} words.

Use Australian English and a professional,
engaging local cricket journalism style.

For a weekend report, organise the story
around the club's overall weekend, with
clear coverage of every supplied match.

For a single match report, tell the story
of the match chronologically where possible.

Start with a strong headline.

Include important batting and bowling
performances, results and turning points.

Use the official scorecard result as
authoritative.

Only use supplied match facts.

Never invent weather, pitch conditions,
quotes, player backgrounds, partnerships,
injuries or tactical decisions.

If a result is unavailable, do not infer
a win or loss.

Do not guess private player identities.

Do not interpret retired not out as
a dismissal.

Respect the opposition.

Extra context:
{context}

Things to avoid:
{avoid}

MATCH DATA:
{json.dumps(data, ensure_ascii=False, default=str)}

Return only the finished headline and
complete article.
"""

    last_error = None

    models = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ]

    for model in models:
        for attempt in range(3):
            try:
                response = (
                    client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                            max_output_tokens=6000
                        )
                    )
                )

                article = (
                    response.text or ""
                ).strip()

                if article:
                    return article

            except Exception as error:
                last_error = error
                error_text = str(error).lower()

                if any(
                    item in error_text
                    for item in [
                        "503",
                        "429",
                        "unavailable",
                        "high demand",
                        "resource_exhausted"
                    ]
                ):
                    time.sleep(
                        2 + attempt * 2
                    )
                    continue

                if any(
                    item in error_text
                    for item in [
                        "404",
                        "not found",
                        "not supported"
                    ]
                ):
                    break

                raise

    raise RuntimeError(
        f"Article generation failed: "
        f"{last_error}"
    )


# =========================================================
# EXTRA GRADE CONFIGURATION
# =========================================================

def parse_extra_grades(raw):
    result = {}

    for line in raw.splitlines():
        if not line.strip():
            continue

        parts = [
            part.strip()
            for part in line.split("|")
        ]

        if (
            len(parts) == 3
            and re.fullmatch(
                r"[0-9a-fA-F-]{36}",
                parts[1]
            )
        ):
            result[parts[0]] = {
                "grade": parts[1],
                "team": parts[2]
            }

    return result


# =========================================================
# TEAM SELECTION OUTPUT
# =========================================================

def selection_text(items):
    output = [
        "HAWTHORN BOROONDARA CRICKET CLUB",
        "TEAM SELECTIONS",
        ""
    ]

    for item in items:
        output.extend([
            item["grade"].upper(),
            item["fixture"],
            (
                f"Date: {item['date']} | "
                f"Venue: {item['venue']}"
            )
        ])

        if item["players"]:
            output.extend(
                f"{index}. {player}"
                for index, player in enumerate(
                    item["players"],
                    1
                )
            )

            output.append(
                "Published players: "
                f"{len(item['players'])}"
            )
        else:
            output.append(
                "Selection not available — "
                "verify on PlayCricket"
            )

        output.append("")

    return "\n".join(output)


# =========================================================
# SESSION STATE
# =========================================================

if "matches" not in st.session_state:
    st.session_state.matches = []

if "article" not in st.session_state:
    st.session_state.article = ""

if "selection_output" not in st.session_state:
    st.session_state.selection_output = ""


# =========================================================
# SIDEBAR — GRADE MANAGEMENT
# =========================================================

with st.sidebar:
    st.subheader("Grades")

    st.caption(
        "Three HBCC grades are preconfigured. "
        "Add more below as you obtain their "
        "grade and team IDs."
    )

    extra_grades = st.text_area(
        "Additional grades (optional)",
        placeholder=(
            "Men's 3rd XI | grade-uuid | team-uuid"
        ),
        height=105,
        help=(
            "One per line: "
            "Grade name | grade ID | team ID"
        )
    )

    st.caption(
        "To find IDs, use the grade URL: "
        "/grade/GRADE_ID?teamId=TEAM_ID"
    )

    if st.button(
        "Clear cached PlayCricket data"
    ):
        st.cache_data.clear()
        st.success("Cache cleared")


all_grades = {
    **DEFAULT_GRADES,
    **parse_extra_grades(extra_grades)
}


# =========================================================
# CHOOSE GENERATOR
# =========================================================

mode = st.radio(
    "What would you like to create?",
    [
        "Match Report",
        "Weekend Report",
        "Team Selections"
    ],
    horizontal=True
)


# =========================================================
# STEP 1 — SELECT DATES
# =========================================================

left, right = st.columns(
    [1, 1.2]
)

with left:
    st.markdown(
        "#### 1. Choose dates"
    )

    date_mode = st.radio(
        "Date selection",
        [
            "Date range",
            "Individual dates"
        ],
        horizontal=True,
        label_visibility="collapsed"
    )

    if date_mode == "Date range":
        start = date.today()
        end = start + timedelta(days=1)

        selected_range = st.date_input(
            "Match dates",
            value=(start, end)
        )

        if (
            isinstance(
                selected_range,
                tuple
            )
            and len(selected_range) == 2
        ):
            dates = {
                date.fromordinal(day)
                for day in range(
                    selected_range[0].toordinal(),
                    selected_range[-1].toordinal() + 1
                )
            }
        else:
            dates = set()

    else:
        available_dates = [
            date.today() + timedelta(days=i)
            for i in range(-90, 120)
        ]

        chosen_dates = st.multiselect(
            "Select dates",
            available_dates,
            default=[date.today()],
            format_func=lambda value: (
                value.strftime(
                    "%a %d %b %Y"
                )
            )
        )

        dates = set(chosen_dates)


# =========================================================
# STEP 2 — SELECT GRADES
# =========================================================

with right:
    st.markdown(
        "#### 2. Choose grades"
    )

    selected_grades = st.multiselect(
        "HBCC grades",
        list(all_grades),
        default=list(DEFAULT_GRADES)
    )

    st.caption(
        "Select one or multiple grades. "
        "Only matching fixtures will "
        "be included."
    )


# =========================================================
# FIND FIXTURES
# =========================================================

if st.button(
    "🔎 Find HBCC fixtures",
    type="primary",
    use_container_width=True,
    disabled=(
        not dates
        or not selected_grades
    )
):
    results = []
    errors = []
    seen = set()

    with st.spinner(
        "Searching PlayCricket fixtures..."
    ):
        for grade in selected_grades:
            config = all_grades[grade]

            try:
                payload = grade_fixtures(
                    config["grade"]
                )

                fixtures = find_match_objects(
                    payload
                )

                if not fixtures:
                    errors.append(
                        f"{grade}: no recognisable "
                        "match records in API response."
                    )

                for match in fixtures:
                    mid = match_id(match)

                    if mid in seen:
                        continue

                    fixture_date = match_date(
                        match
                    )

                    if fixture_date not in dates:
                        continue

                    teams = extract_teams(
                        match
                    )

                    if (
                        teams
                        and not hb_team(
                            teams,
                            config["team"]
                        )
                    ):
                        continue

                    seen.add(mid)

                    copy = dict(match)

                    copy["_grade"] = grade
                    copy["_team_id"] = (
                        config["team"]
                    )

                    results.append(copy)

            except Exception as error:
                errors.append(
                    f"{grade}: {error}"
                )

    st.session_state.matches = sorted(
        results,
        key=lambda match: (
            str(match_date(match)),
            match["_grade"]
        )
    )

    st.session_state.article = ""
    st.session_state.selection_output = ""
    st.session_state.selection_items = []
    st.session_state.search_errors = errors


# =========================================================
# STEP 3 — REVIEW FIXTURES
# =========================================================

matches = st.session_state.matches

if st.session_state.get(
    "search_errors"
):
    with st.expander(
        "Fixture search notes"
    ):
        for error in st.session_state.search_errors:
            st.warning(error)


st.markdown(
    "#### 3. Review fixtures"
)

chosen_matches = []

if matches:
    st.success(
        f"{len(matches)} fixture(s) found. "
        "Select which to include."
    )

    for match in matches:
        mid = match_id(match)

        label = (
            f"{match_date(match)} · "
            f"{match['_grade']} · "
            f"{match_label(match)}"
        )

        if st.checkbox(
            label,
            value=True,
            key="match_" + mid
        ):
            chosen_matches.append(match)

        st.caption(
            f"[View on PlayCricket]"
            f"({fixture_url(mid)})"
        )

else:
    st.info(
        "Choose dates and grades, then select "
        "**Find HBCC fixtures**. "
        "If none appear, check the date range "
        "and configured grade IDs."
    )


st.divider()


# =========================================================
# MATCH REPORT / WEEKEND REPORT
# =========================================================

if mode in (
    "Match Report",
    "Weekend Report"
):
    st.markdown(
        "#### 4. Generate article"
    )

    if (
        mode == "Match Report"
        and len(chosen_matches) > 1
    ):
        options = {
            (
                f"{match['_grade']} · "
                f"{match_label(match)} · "
                f"{match_date(match)}"
            ): match
            for match in chosen_matches
        }

        picked = st.selectbox(
            "Choose the match to write about",
            list(options)
        )

        report_matches = [
            options[picked]
        ]

    else:
        report_matches = chosen_matches

    context = st.text_area(
        "Extra context (optional)",
        placeholder=(
            "Club debut, first game of "
            "the season, special milestone..."
        )
    )

    avoid = st.text_area(
        "Anything to avoid (optional)",
        height=70
    )

    length = st.select_slider(
        "Report length (approximate words)",
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
        disabled=not report_matches
    ):
        data = []
        failures = []

        with st.spinner(
            "Reading selected scorecards "
            "and ball-by-ball..."
        ):
            for match in report_matches:
                try:
                    detail = match_detail(
                        match_id(match)
                    )

                    data.append(
                        report_data(
                            match,
                            detail,
                            True
                        )
                    )

                except Exception as error:
                    failures.append(
                        f"{match['_grade']}: "
                        f"{error}"
                    )

        if failures:
            for failure in failures:
                st.error(failure)

        if data:
            try:
                with st.spinner(
                    "Writing your article..."
                ):
                    st.session_state.article = (
                        gemini_report(
                            data,
                            mode,
                            length,
                            context,
                            avoid
                        )
                    )

            except Exception as error:
                st.error(str(error))

    if st.session_state.article:
        st.markdown(
            "### Your report"
        )

        st.markdown(
            st.session_state.article
        )

        st.text_area(
            "Copy or edit article",
            value=st.session_state.article,
            height=360
        )

        st.download_button(
            "Download article (.txt)",
            st.session_state.article,
            file_name="hbcc_report.txt",
            mime="text/plain"
        )

        st.caption(
            "Check scores, names and context "
            "against PlayCricket before publishing."
        )


# =========================================================
# TEAM SELECTIONS GENERATOR
# =========================================================

else:
    st.markdown(
        "#### 4. Review published selections"
    )

    if st.button(
        "Retrieve selected teams",
        type="primary",
        disabled=not chosen_matches
    ):
        items = []

        with st.spinner(
            "Checking published HBCC team lists..."
        ):
            for match in chosen_matches:
                mid = match_id(match)

                try:
                    detail = match_detail(mid)

                    players = extract_players(
                        detail,
                        match["_team_id"]
                    )

                    venue = (
                        textval(
                            first(
                                match,
                                "venue",
                                "ground",
                                "venueName"
                            )
                        )
                        or textval(
                            first(
                                detail,
                                "venue",
                                "ground",
                                "venueName"
                            )
                        )
                        or "Venue not available"
                    )

                    items.append({
                        "id": mid,
                        "grade": match["_grade"],
                        "fixture": match_label(
                            match
                        ),
                        "date": str(
                            match_date(match)
                        ),
                        "venue": venue,
                        "players": players
                    })

                except Exception as error:
                    items.append({
                        "id": mid,
                        "grade": match["_grade"],
                        "fixture": match_label(
                            match
                        ),
                        "date": str(
                            match_date(match)
                        ),
                        "venue": (
                            "Venue not available"
                        ),
                        "players": [],
                        "error": str(error)
                    })

        st.session_state.selection_items = items

    if st.session_state.get(
        "selection_items"
    ):
        edited = []

        for item in st.session_state.selection_items:
            with st.container(
                border=True
            ):
                st.markdown(
                    f"**{item['grade']} — "
                    f"{item['fixture']}**"
                )

                st.caption(
                    f"{item['date']} · "
                    f"{item['venue']}"
                )

                if item.get("error"):
                    st.warning(
                        "Could not retrieve match: "
                        + item["error"]
                    )

                elif not item["players"]:
                    st.warning(
                        "No published HBCC selection "
                        "found in the supported API "
                        "fields. Check the PlayCricket "
                        "page and enter players manually "
                        "if required."
                    )

                raw = st.text_area(
                    "Selected players "
                    "(one per line)",
                    value="\n".join(
                        item["players"]
                    ),
                    height=150,
                    key="players_" + item["id"]
                )

                copy = dict(item)

                copy["players"] = [
                    player.strip()
                    for player in raw.splitlines()
                    if player.strip()
                ]

                edited.append(copy)

                st.markdown(
                    f"[Verify on PlayCricket]"
                    f"({fixture_url(item['id'])})"
                )

        output = selection_text(
            edited
        )

        st.markdown(
            "### Combined team announcement"
        )

        st.text_area(
            "Copy-ready team selections",
            value=output,
            height=350
        )

        st.download_button(
            "Download selections (.txt)",
            output,
            file_name=(
                "hbcc_team_selections.txt"
            ),
            mime="text/plain"
        )

        st.caption(
            "Published team lists may be "
            "incomplete or change before play. "
            "Review every grade before sharing."
        )


# =========================================================
# FOOTER
# =========================================================

st.markdown(
    '<div class="hbfoot">'
    'HBCC CONTENT STUDIO · '
    'Match reports, weekend reports and '
    'team selections · '
    'Verify all outputs before publishing.'
    '</div>',
    unsafe_allow_html=True
)
