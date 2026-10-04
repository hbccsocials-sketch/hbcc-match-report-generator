import streamlit as st
import requests
import re

st.set_page_config(
    page_title="HB Hawks Match Report Generator",
    page_icon="🏏",
    layout="centered"
)


# =========================================================
# PLAYCRICKET DATA
# =========================================================

def extract_match_id(url):
    pattern = r"/match/([a-fA-F0-9-]+)"
    match = re.search(pattern, url)
    return match.group(1) if match else None


def get_scorecard(match_id):
    url = (
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )

    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    response.raise_for_status()
    return response.json()


def get_ball_by_ball(match_id):
    url = (
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}/balls"
        "?jsconfig=eccn%3Atrue"
    )

    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    response.raise_for_status()
    return response.json()


# =========================================================
# HELPERS
# =========================================================

def safe_name(name):
    """
    Protect private/hidden PlayCricket players.
    """

    if not name:
        return "Private Player"

    cleaned = str(name).strip()

    if "*" in cleaned:
        return "Private Player"

    return cleaned


def find_innings(data):
    """
    Recursively find dictionaries that look like
    scorecard innings.
    """

    found = []

    def walk(item):
        if isinstance(item, dict):

            if (
                "batting" in item
                and isinstance(item.get("batting"), list)
                and (
                    "runsScored" in item
                    or "numberOfWicketsFallen" in item
                )
            ):
                found.append(item)

            for value in item.values():
                walk(value)

        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(data)

    # Remove duplicate objects
    unique = []
    seen = set()

    for innings in found:
        marker = id(innings)

        if marker not in seen:
            seen.add(marker)
            unique.append(innings)

    return unique


def get_team_name(innings, teams):
    """
    Try several possible fields to determine which
    team the innings belongs to.
    """

    possible_names = [
        innings.get("teamName"),
        innings.get("battingTeamName"),
        innings.get("displayName"),
        innings.get("inningsName"),
    ]

    for name in possible_names:
        if name:
            return name

    batting_team_id = (
        innings.get("battingTeamId")
        or innings.get("teamId")
    )

    if batting_team_id:
        for team in teams:
            if team.get("id") == batting_team_id:
                return team.get("displayName", "Unknown Team")

    return "Unknown Team"


def format_score(runs, wickets):
    if wickets is None:
        return str(runs)

    return f"{runs}/{wickets}"


# =========================================================
# ANALYSIS
# =========================================================

def analyse_match(scorecard_data):

    summary = scorecard_data.get("matchSummary", {})

    result_text = summary.get(
        "resultText",
        "Result unavailable"
    )

    teams = summary.get("teams", [])

    innings_list = find_innings(scorecard_data)

    analysis = {
        "result": result_text,
        "teams": teams,
        "innings": []
    }

    for innings in innings_list:

        team_name = get_team_name(
            innings,
            teams
        )

        runs = innings.get("runsScored", 0)

        wickets = innings.get(
            "numberOfWicketsFallen",
            0
        )

        batting = []

        for batter in innings.get("batting", []):

            runs_scored = batter.get(
                "runsScored",
                0
            )

            balls = batter.get(
                "ballsFaced",
                0
            )

            batting.append({
                "name": safe_name(
                    batter.get("playerShortName")
                ),
                "runs": runs_scored,
                "balls": balls,
                "fours": batter.get(
                    "foursScored",
                    0
                ),
                "sixes": batter.get(
                    "sixesScored",
                    0
                ),
                "strike_rate": batter.get(
                    "strikeRate",
                    ""
                ),
                "dismissal": batter.get(
                    "dismissalText",
                    ""
                ),
                "order": batter.get(
                    "batOrder",
                    99
                )
            })

        bowling = []

        for bowler in innings.get("bowling", []):

            bowling.append({
                "name": safe_name(
                    bowler.get("playerShortName")
                ),
                "overs": bowler.get(
                    "oversBowled",
                    0
                ),
                "maidens": bowler.get(
                    "maidensBowled",
                    0
                ),
                "runs": bowler.get(
                    "runsConceded",
                    0
                ),
                "wickets": bowler.get(
                    "wicketsTaken",
                    0
                ),
                "economy": bowler.get(
                    "economy",
                    ""
                )
            })

        batting.sort(
            key=lambda x: x["runs"],
            reverse=True
        )

        bowling.sort(
            key=lambda x: (
                x["wickets"],
                -x["runs"]
            ),
            reverse=True
        )

        analysis["innings"].append({
            "team": team_name,
            "runs": runs,
            "wickets": wickets,
            "batting": batting,
            "bowling": bowling
        })

    return analysis


# =========================================================
# DISPLAY
# =========================================================

def display_analysis(analysis):

    st.header("Match Analysis")

    st.success(analysis["result"])

    # Team summary

    if analysis["teams"]:

        st.subheader("Match")

        for team in analysis["teams"]:

            name = team.get(
                "displayName",
                "Unknown Team"
            )

            score = team.get(
                "scoreText",
                ""
            )

            if score:
                st.write(f"**{name}:** {score}")
            else:
                st.write(f"**{name}**")

    # Innings

    for number, innings in enumerate(
        analysis["innings"],
        start=1
    ):

        st.divider()

        st.subheader(
            f"Innings {number} — {innings['team']}"
        )

        st.metric(
            "Score",
            format_score(
                innings["runs"],
                innings["wickets"]
            )
        )

        # Batting

        st.markdown("#### Batting")

        if innings["batting"]:

            for batter in innings["batting"]:

                name = batter["name"]

                runs = batter["runs"]
                balls = batter["balls"]

                dismissal = batter["dismissal"]

                line = (
                    f"**{name}** — "
                    f"{runs} from {balls} balls"
                )

                boundaries = []

                if batter["fours"]:
                    boundaries.append(
                        f"{batter['fours']} fours"
                    )

                if batter["sixes"]:
                    boundaries.append(
                        f"{batter['sixes']} sixes"
                    )

                if boundaries:
                    line += (
                        " (" +
                        ", ".join(boundaries) +
                        ")"
                    )

                if dismissal:
                    line += f" — {dismissal}"

                st.write(line)

        else:
            st.write(
                "No batting data available."
            )

        # Bowling

        st.markdown("#### Bowling")

        if innings["bowling"]:

            for bowler in innings["bowling"]:

                st.write(
                    f"**{bowler['name']}** — "
                    f"{bowler['overs']} overs, "
                    f"{bowler['runs']} runs, "
                    f"{bowler['wickets']} wickets "
                    f"(Econ {bowler['economy']})"
                )

        else:
            st.write(
                "No bowling data available."
            )


# =========================================================
# APP
# =========================================================

st.title("🏏 HB Hawks Match Report Generator")

st.write(
    "Paste a PlayCricket match URL to analyse the "
    "scorecard and ball-by-ball data."
)

match_url = st.text_input(
    "PlayCricket Match URL",
    placeholder="https://play.cricket.com.au/match/..."
)

st.subheader("Match Context")

match_context = st.text_area(
    "Anything you'd like included?",
    placeholder=(
        "Example:\n"
        "First match of the season.\n"
        "Club debut for Jane Smith.\n"
        "Only match played by the club this weekend."
    ),
    height=140
)

avoid_context = st.text_area(
    "Anything you'd like avoided?",
    placeholder=(
        "Example: Don't mention last season's result."
    ),
    height=90
)

article_length = st.selectbox(
    "Article length",
    [
        "Short — 300 words",
        "Standard — 500 words",
        "Detailed — 700 words"
    ],
    index=1
)


# =========================================================
# LOAD MATCH
# =========================================================

if st.button(
    "Analyse Match",
    type="primary"
):

    if not match_url:

        st.error(
            "Please enter a PlayCricket match URL."
        )

    else:

        match_id = extract_match_id(
            match_url
        )

        if not match_id:

            st.error(
                "Could not identify a valid "
                "PlayCricket Match ID."
            )

        else:

            try:

                with st.spinner(
                    "Analysing PlayCricket match..."
                ):

                    scorecard_data = get_scorecard(
                        match_id
                    )

                    ball_data = get_ball_by_ball(
                        match_id
                    )

                    analysis = analyse_match(
                        scorecard_data
                    )

                st.success(
                    "Match analysed successfully!"
                )

                display_analysis(
                    analysis
                )

                st.divider()

                st.subheader(
                    "Match Report Instructions"
                )

                if match_context:

                    st.write(
                        "**Additional context:**"
                    )

                    st.write(
                        match_context
                    )

                if avoid_context:

                    st.write(
                        "**Avoid:**"
                    )

                    st.write(
                        avoid_context
                    )

                st.write(
                    f"**Article length:** "
                    f"{article_length}"
                )

                # Keep raw data available for debugging

                with st.expander(
                    "Developer: Raw PlayCricket Data"
                ):

                    st.write(
                        "Scorecard"
                    )

                    st.json(
                        scorecard_data
                    )

                    st.write(
                        "Ball-by-ball"
                    )

                    st.json(
                        ball_data
                    )

            except requests.exceptions.RequestException as e:

                st.error(
                    "PlayCricket data could not "
                    "be retrieved."
                )

                st.exception(e)

            except Exception as e:

                st.error(
                    "The match was retrieved, but "
                    "there was a problem analysing it."
                )

                st.exception(e)
