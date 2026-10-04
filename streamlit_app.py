import streamlit as st
import requests
import re

st.set_page_config(
    page_title="HB Hawks Match Report Generator",
    page_icon="🏏",
    layout="centered"
)


# =========================================================
# PLAYCRICKET
# =========================================================

def extract_match_id(url):
    match = re.search(r"/match/([a-fA-F0-9-]+)", url)
    return match.group(1) if match else None


def fetch_json(url):
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    return response.json()


def get_scorecard(match_id):
    return fetch_json(
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )


def get_ball_by_ball(match_id):
    return fetch_json(
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}/balls"
        "?jsconfig=eccn%3Atrue"
    )


# =========================================================
# HELPERS
# =========================================================

def safe_name(name):
    if not name:
        return "Private Player"

    name = str(name).strip()

    if "*" in name:
        return "Private Player"

    return name


def find_scorecard_innings(data):
    found = []

    def walk(item):
        if isinstance(item, dict):
            if (
                isinstance(item.get("batting"), list)
                and "runsScored" in item
            ):
                found.append(item)

            for value in item.values():
                walk(value)

        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(data)
    return found


def flatten_balls(ball_container):
    """
    PlayCricket may group balls inside nested lists/dicts.
    Recursively extract actual delivery objects.
    """

    deliveries = []

    def walk(item):
        if isinstance(item, dict):

            if (
                "overNumber" in item
                and "ballNumber" in item
                and "progressRuns" in item
            ):
                deliveries.append(item)
                return

            for value in item.values():
                walk(value)

        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(ball_container)

    return deliveries


def get_ball_innings(ball_data):
    return ball_data.get("innings", [])


def get_team_lookup(ball_data):
    lookup = {}

    for team in ball_data.get("teams", []):
        lookup[team.get("id")] = team.get(
            "displayName",
            "Unknown Team"
        )

    return lookup


# =========================================================
# SCORECARD ANALYSIS
# =========================================================

def analyse_scorecard(scorecard):

    summary = scorecard.get("matchSummary", {})

    result = summary.get(
        "resultText",
        "Result unavailable"
    )

    teams = summary.get("teams", [])

    innings_found = find_scorecard_innings(scorecard)

    innings_analysis = []

    for innings in innings_found:

        batting = []

        for player in innings.get("batting", []):

            batting.append({
                "name": safe_name(
                    player.get("playerShortName")
                ),
                "runs": player.get("runsScored", 0),
                "balls": player.get("ballsFaced", 0),
                "fours": player.get("foursScored", 0),
                "sixes": player.get("sixesScored", 0),
                "dismissal": player.get(
                    "dismissalText",
                    ""
                )
            })

        batting.sort(
            key=lambda x: x["runs"],
            reverse=True
        )

        bowling = []

        for player in innings.get("bowling", []):

            bowling.append({
                "name": safe_name(
                    player.get("playerShortName")
                ),
                "overs": player.get("oversBowled", 0),
                "maidens": player.get(
                    "maidensBowled",
                    0
                ),
                "runs": player.get(
                    "runsConceded",
                    0
                ),
                "wickets": player.get(
                    "wicketsTaken",
                    0
                ),
                "economy": player.get(
                    "economy",
                    ""
                )
            })

        bowling.sort(
            key=lambda x: (
                x["wickets"],
                -x["runs"]
            ),
            reverse=True
        )

        innings_analysis.append({
            "runs": innings.get("runsScored", 0),
            "wickets": innings.get(
                "numberOfWicketsFallen",
                0
            ),
            "batting": batting,
            "bowling": bowling
        })

    return {
        "result": result,
        "teams": teams,
        "innings": innings_analysis
    }


# =========================================================
# BALL-BY-BALL STORY ENGINE
# =========================================================

def analyse_ball_innings(innings, team_lookup):

    batting_team_id = innings.get("battingTeamId")

    team_name = team_lookup.get(
        batting_team_id,
        innings.get(
            "inningsName",
            "Unknown Team"
        )
    )

    balls = flatten_balls(
        innings.get("balls", [])
    )

    wickets = []
    boundaries = []
    overs = {}

    for ball in balls:

        over = ball.get("overNumber", 0)
        ball_number = ball.get("ballNumber", 0)

        # PlayCricket uses zero-based overNumber.
        display_over = over + 1

        progress_runs = ball.get(
            "progressRuns",
            0
        )

        progress_wickets = ball.get(
            "progressWickets",
            0
        )

        runs_bat = ball.get(
            "runsBat",
            0
        )

        wides = ball.get("wides", 0)
        no_balls = ball.get("noBalls", 0)
        byes = ball.get("byes", 0)
        leg_byes = ball.get("legByes", 0)
        penalties = ball.get("penaltyRuns", 0)

        total_delivery_runs = (
            runs_bat
            + wides
            + no_balls
            + byes
            + leg_byes
            + penalties
        )

        if over not in overs:
            overs[over] = {
                "runs": 0,
                "wickets": 0
            }

        overs[over]["runs"] += total_delivery_runs

        # -------------------------
        # WICKET
        # -------------------------

        if ball.get("dismissedParticipantId"):

            dismissed = safe_name(
                ball.get("strikerShortName")
            )

            dismissal = ball.get(
                "dismissalType",
                "Wicket"
            )

            bowler = safe_name(
                ball.get("bowlerShortName")
            )

            wickets.append({
                "over": display_over,
                "ball": ball_number,
                "score": progress_runs,
                "wickets": progress_wickets,
                "batter": dismissed,
                "bowler": bowler,
                "dismissal": dismissal,
                "description": ball.get(
                    "description",
                    ""
                )
            })

            overs[over]["wickets"] += 1

        # -------------------------
        # BOUNDARY
        # -------------------------

        if runs_bat >= 4:

            boundaries.append({
                "over": display_over,
                "ball": ball_number,
                "runs": runs_bat,
                "batter": safe_name(
                    ball.get("strikerShortName")
                ),
                "score": progress_runs
            })

    # -----------------------------------
    # BIG OVERS
    # -----------------------------------

    big_overs = []

    for over_number, data in overs.items():

        if data["runs"] >= 10:

            big_overs.append({
                "over": over_number + 1,
                "runs": data["runs"],
                "wickets": data["wickets"]
            })

    big_overs.sort(
        key=lambda x: x["runs"],
        reverse=True
    )

    # -----------------------------------
    # WICKET CLUSTERS
    # -----------------------------------

    wicket_clusters = []

    for i in range(len(wickets) - 1):

        first = wickets[i]
        second = wickets[i + 1]

        run_difference = (
            second["score"] -
            first["score"]
        )

        if run_difference <= 10:

            wicket_clusters.append({
                "first_score":
                    f"{first['score']}/{first['wickets']}",
                "second_score":
                    f"{second['score']}/{second['wickets']}",
                "runs_between": run_difference
            })

    final_score = ""

    if balls:
        final_ball = balls[-1]

        final_score = (
            f"{final_ball.get('progressRuns', 0)}/"
            f"{final_ball.get('progressWickets', 0)}"
        )

    return {
        "team": team_name,
        "balls": len(balls),
        "final_score": final_score,
        "wickets": wickets,
        "boundaries": boundaries,
        "big_overs": big_overs,
        "wicket_clusters": wicket_clusters
    }


def analyse_ball_by_ball(ball_data):

    team_lookup = get_team_lookup(ball_data)

    results = []

    for innings in get_ball_innings(ball_data):

        results.append(
            analyse_ball_innings(
                innings,
                team_lookup
            )
        )

    return results


# =========================================================
# DISPLAY
# =========================================================

def display_match(score_analysis, story_analysis):

    st.header("Match Analysis")

    st.success(score_analysis["result"])

    st.subheader("Scorecard")

    for team in score_analysis["teams"]:

        st.write(
            f"**{team.get('displayName', 'Team')}:** "
            f"{team.get('scoreText', '')}"
        )

    # -----------------------------------
    # LEADING PERFORMERS
    # -----------------------------------

    st.subheader("Leading Performers")

    for number, innings in enumerate(
        score_analysis["innings"],
        start=1
    ):

        st.markdown(
            f"**Innings {number}**"
        )

        if innings["batting"]:

            st.write("Top batting:")

            for batter in innings["batting"][:4]:

                st.write(
                    f"• {batter['name']} — "
                    f"{batter['runs']} "
                    f"from {batter['balls']} balls"
                )

        wicket_takers = [
            b for b in innings["bowling"]
            if b["wickets"] > 0
        ]

        if wicket_takers:

            st.write("Leading bowling:")

            for bowler in wicket_takers[:4]:

                st.write(
                    f"• {bowler['name']} — "
                    f"{bowler['wickets']}/"
                    f"{bowler['runs']} "
                    f"from {bowler['overs']} overs"
                )

    # -----------------------------------
    # MATCH STORY
    # -----------------------------------

    st.subheader("Ball-by-Ball Story")

    for innings in story_analysis:

        st.markdown(
            f"### {innings['team']}"
        )

        if innings["final_score"]:

            st.write(
                f"Ball-by-ball final score: "
                f"**{innings['final_score']}**"
            )

        # Wickets

        if innings["wickets"]:

            st.markdown("**Wickets**")

            for wicket in innings["wickets"]:

                st.write(
                    f"• {wicket['score']}/"
                    f"{wicket['wickets']} — "
                    f"{wicket['description']}"
                )

        # Big overs

        if innings["big_overs"]:

            st.markdown("**High-scoring overs**")

            for over in innings["big_overs"][:5]:

                st.write(
                    f"• Over {over['over']}: "
                    f"{over['runs']} runs"
                )

        # Wicket clusters

        if innings["wicket_clusters"]:

            st.markdown(
                "**Potential wicket clusters**"
            )

            for cluster in innings[
                "wicket_clusters"
            ][:5]:

                st.write(
                    f"• {cluster['first_score']} "
                    f"to {cluster['second_score']} "
                    f"for only "
                    f"{cluster['runs_between']} runs"
                )


# =========================================================
# APP
# =========================================================

st.title("🏏 HB Hawks Match Report Generator")

st.write(
    "Paste a PlayCricket match URL and the tool will "
    "analyse the scorecard and ball-by-ball story."
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
                    "Analysing match..."
                ):

                    scorecard_data = get_scorecard(
                        match_id
                    )

                    ball_data = get_ball_by_ball(
                        match_id
                    )

                    score_analysis = analyse_scorecard(
                        scorecard_data
                    )

                    story_analysis = analyse_ball_by_ball(
                        ball_data
                    )

                st.success(
                    "Match analysed successfully!"
                )

                display_match(
                    score_analysis,
                    story_analysis
                )

                st.divider()

                st.subheader(
                    "Article Instructions"
                )

                if match_context:

                    st.write(
                        f"**Context:** {match_context}"
                    )

                if avoid_context:

                    st.write(
                        f"**Avoid:** {avoid_context}"
                    )

                st.write(
                    f"**Length:** {article_length}"
                )

                with st.expander(
                    "Developer Data"
                ):

                    st.json({
                        "scorecard_analysis":
                            score_analysis,
                        "ball_by_ball_analysis":
                            story_analysis
                    })

            except requests.exceptions.RequestException as e:

                st.error(
                    "Could not retrieve "
                    "PlayCricket data."
                )

                st.exception(e)

            except Exception as e:

                st.error(
                    "The match was retrieved, "
                    "but analysis failed."
                )

                st.exception(e)
