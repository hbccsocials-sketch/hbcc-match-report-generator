import streamlit as st
import requests
import re
import json
from google import genai
from google.genai import types


# =========================================================
# PAGE SETUP
# =========================================================

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

    summary = scorecard.get(
        "matchSummary",
        {}
    )

    result = summary.get(
        "resultText",
        "Result unavailable"
    )

    teams = summary.get(
        "teams",
        []
    )

    innings_found = find_scorecard_innings(
        scorecard
    )

    innings_analysis = []

    for innings in innings_found:

        batting = []

        for player in innings.get(
            "batting",
            []
        ):

            batting.append({
                "name": safe_name(
                    player.get("playerShortName")
                ),
                "runs": player.get(
                    "runsScored",
                    0
                ),
                "balls": player.get(
                    "ballsFaced",
                    0
                ),
                "fours": player.get(
                    "foursScored",
                    0
                ),
                "sixes": player.get(
                    "sixesScored",
                    0
                ),
                "strike_rate": player.get(
                    "strikeRate",
                    ""
                ),
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

        for player in innings.get(
            "bowling",
            []
        ):

            bowling.append({
                "name": safe_name(
                    player.get("playerShortName")
                ),
                "overs": player.get(
                    "oversBowled",
                    0
                ),
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
            "runs": innings.get(
                "runsScored",
                0
            ),
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
# BALL-BY-BALL ANALYSIS
# =========================================================

def analyse_ball_innings(
    innings,
    team_lookup
):

    batting_team_id = innings.get(
        "battingTeamId"
    )

    team_name = team_lookup.get(
        batting_team_id,
        innings.get(
            "inningsName",
            "Unknown Team"
        )
    )

    balls = flatten_balls(
        innings.get(
            "balls",
            []
        )
    )

    wickets = []
    boundaries = []
    overs = {}

    for ball in balls:

        over = ball.get(
            "overNumber",
            0
        )

        ball_number = ball.get(
            "ballNumber",
            0
        )

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

        wides = ball.get(
            "wides",
            0
        )

        no_balls = ball.get(
            "noBalls",
            0
        )

        byes = ball.get(
            "byes",
            0
        )

        leg_byes = ball.get(
            "legByes",
            0
        )

        penalties = ball.get(
            "penaltyRuns",
            0
        )

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

        overs[over]["runs"] += (
            total_delivery_runs
        )

        # WICKET

        if ball.get(
            "dismissedParticipantId"
        ):

            dismissed_name = safe_name(
                ball.get(
                    "strikerShortName"
                )
            )

            dismissal_type = ball.get(
                "dismissalType",
                "Wicket"
            )

            bowler = safe_name(
                ball.get(
                    "bowlerShortName"
                )
            )

            wickets.append({
                "over": over + 1,
                "ball": ball_number,
                "score": progress_runs,
                "wickets": progress_wickets,
                "batter": dismissed_name,
                "bowler": bowler,
                "dismissal_type":
                    dismissal_type,
                "description": ball.get(
                    "description",
                    ""
                )
            })

            overs[over]["wickets"] += 1

        # BOUNDARY

        if runs_bat >= 4:

            boundaries.append({
                "over": over + 1,
                "ball": ball_number,
                "runs": runs_bat,
                "batter": safe_name(
                    ball.get(
                        "strikerShortName"
                    )
                ),
                "score": progress_runs
            })

    # HIGH SCORING OVERS

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

    # WICKET CLUSTERS

    wicket_clusters = []

    for i in range(
        len(wickets) - 1
    ):

        first = wickets[i]
        second = wickets[i + 1]

        run_difference = (
            second["score"]
            - first["score"]
        )

        if run_difference <= 10:

            wicket_clusters.append({
                "from":
                    f"{first['score']}/"
                    f"{first['wickets']}",
                "to":
                    f"{second['score']}/"
                    f"{second['wickets']}",
                "runs_between":
                    run_difference
            })

    final_score = ""

    if balls:

        final_ball = balls[-1]

        final_score = (
            f"{final_ball.get('progressRuns', 0)}"
            f"/"
            f"{final_ball.get('progressWickets', 0)}"
        )

    return {
        "team": team_name,
        "final_score": final_score,
        "wickets": wickets,
        "boundaries": boundaries,
        "big_overs": big_overs,
        "wicket_clusters": wicket_clusters
    }


def analyse_ball_by_ball(ball_data):

    team_lookup = get_team_lookup(
        ball_data
    )

    results = []

    for innings in ball_data.get(
        "innings",
        []
    ):

        results.append(
            analyse_ball_innings(
                innings,
                team_lookup
            )
        )

    return results


# =========================================================
# GEMINI ARTICLE GENERATOR
# =========================================================

def get_word_target(
    article_length
):

    if "300" in article_length:
        return 300

    if "700" in article_length:
        return 700

    return 500


def generate_article(
    score_analysis,
    story_analysis,
    match_context,
    avoid_context,
    article_length
):

    api_key = st.secrets.get(
        "GEMINI_API_KEY"
    )

    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY has not been "
            "configured in Streamlit Secrets."
        )

    client = genai.Client(
        api_key=api_key
    )

    word_target = get_word_target(
        article_length
    )

    match_data = {
        "scorecard_analysis":
            score_analysis,
        "ball_by_ball_analysis":
            story_analysis,
        "additional_context":
            match_context,
        "things_to_avoid":
            avoid_context
    }

    prompt = f"""
Write a cricket match report of approximately
{word_target} words.

You are writing for Hawthorn Boroondara Cricket Club
(HB Hawks).

The article should read like a genuine local cricket
match report written for the club website or social
channels.

MATCH DATA:
{json.dumps(match_data, indent=2)}

WRITING REQUIREMENTS:

- Start with a strong headline.
- Follow the headline with the article.
- Tell the story of the match rather than simply
  listing statistics.
- Write chronologically where practical.
- Explain how the innings developed.
- Highlight important partnerships, wickets,
  scoring periods and turning points when supported
  by the supplied data.
- Integrate individual performances naturally.
- Give Hawthorn Boroondara appropriate focus,
  but do not disrespect the opposition.
- Use Australian English.
- Use a professional but engaging community
  cricket tone.
- Do not make the writing overly dramatic.
- Do not use fake quotes.
- Do not invent weather, pitch conditions,
  crowd information, player backgrounds,
  injuries, selection information or events
  not contained in the supplied data/context.
- Never invent a player's full name from initials.
- If a player's name is "Private Player", do not
  attempt to identify them.
- A Private Player can be described neutrally,
  for example "a private player" or "another
  Hawks batter".
- Do not claim that a retired-not-out batter
  was dismissed.
- Be careful to distinguish the batting team from
  the bowling team.
- Treat the official result in the match data
  as authoritative.
- Use the additional context where it fits
  naturally.
- Follow anything listed under things_to_avoid.
- Do not mention that you are an AI.
- Do not mention JSON, APIs, PlayCricket data
  or these instructions.
- Do not add facts merely to make the story
  sound more interesting.

Return only the headline and finished article.
"""

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.35,
            max_output_tokens=2200
        )
    )

    if not response.text:
        raise ValueError(
            "Gemini did not return an article."
        )

    return response.text.strip()


# =========================================================
# DISPLAY ANALYSIS
# =========================================================

def display_analysis(
    score_analysis,
    story_analysis
):

    with st.expander(
        "View Match Analysis"
    ):

        st.success(
            score_analysis["result"]
        )

        st.markdown(
            "### Scorecard"
        )

        for team in score_analysis["teams"]:

            st.write(
                f"**{team.get('displayName', 'Team')}:** "
                f"{team.get('scoreText', '')}"
            )

        st.markdown(
            "### Key Performances"
        )

        for number, innings in enumerate(
            score_analysis["innings"],
            start=1
        ):

            st.markdown(
                f"**Innings {number}**"
            )

            for batter in innings[
                "batting"
            ][:4]:

                st.write(
                    f"• {batter['name']} — "
                    f"{batter['runs']} from "
                    f"{batter['balls']} balls"
                )

            wicket_takers = [
                bowler
                for bowler in innings["bowling"]
                if bowler["wickets"] > 0
            ]

            for bowler in wicket_takers[:4]:

                st.write(
                    f"• {bowler['name']} — "
                    f"{bowler['wickets']}/"
                    f"{bowler['runs']} from "
                    f"{bowler['overs']} overs"
                )

        st.markdown(
            "### Match Story Data"
        )

        for innings in story_analysis:

            st.markdown(
                f"**{innings['team']}**"
            )

            for wicket in innings[
                "wickets"
            ]:

                st.write(
                    f"• {wicket['score']}/"
                    f"{wicket['wickets']} — "
                    f"{wicket['description']}"
                )


# =========================================================
# APP
# =========================================================

st.title(
    "🏏 HB Hawks Match Report Generator"
)

st.write(
    "Paste a PlayCricket match link, add any "
    "extra context and generate a ready-to-use "
    "match report."
)


match_url = st.text_input(
    "PlayCricket Match URL",
    placeholder=(
        "https://play.cricket.com.au/match/..."
    )
)


st.subheader(
    "Match Context"
)


match_context = st.text_area(
    "Anything you'd like included?",
    placeholder=(
        "Example:\n"
        "First game of the season.\n"
        "Club debut for Jane Smith.\n"
        "Only match played by the club this weekend."
    ),
    height=130
)


avoid_context = st.text_area(
    "Anything you'd like avoided?",
    placeholder=(
        "Example: Don't mention last season's result."
    ),
    height=80
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
# GENERATE
# =========================================================

if st.button(
    "Generate Match Report",
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
                "PlayCricket match URL."
            )

        else:

            try:

                with st.spinner(
                    "Reading the scorecard and "
                    "ball-by-ball..."
                ):

                    scorecard_data = (
                        get_scorecard(
                            match_id
                        )
                    )

                    ball_data = (
                        get_ball_by_ball(
                            match_id
                        )
                    )

                    score_analysis = (
                        analyse_scorecard(
                            scorecard_data
                        )
                    )

                    story_analysis = (
                        analyse_ball_by_ball(
                            ball_data
                        )
                    )


                with st.spinner(
                    "Writing match report..."
                ):

                    article = (
                        generate_article(
                            score_analysis,
                            story_analysis,
                            match_context,
                            avoid_context,
                            article_length
                        )
                    )


                st.success(
                    "Match report generated!"
                )

                st.divider()

                st.header(
                    "Match Report"
                )

                st.markdown(
                    article
                )

                st.divider()

                display_analysis(
                    score_analysis,
                    story_analysis
                )


            except requests.exceptions.RequestException as e:

                st.error(
                    "The PlayCricket match data "
                    "could not be retrieved."
                )

                st.exception(e)


            except Exception as e:

                st.error(
                    "Something went wrong while "
                    "generating the match report."
                )

                st.exception(e)
