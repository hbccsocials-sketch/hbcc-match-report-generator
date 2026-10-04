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
# PLAYCRICKET DATA
# =========================================================

def extract_match_id(url):
    """Extract the PlayCricket match ID from a match URL."""

    match = re.search(
        r"/match/([a-fA-F0-9-]+)",
        url
    )

    return match.group(1) if match else None


def fetch_json(url):
    """Fetch JSON data from PlayCricket."""

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    return response.json()


def get_scorecard(match_id):
    """Retrieve scorecard data."""

    url = (
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )

    return fetch_json(url)


def get_ball_by_ball(match_id):
    """Retrieve ball-by-ball data."""

    url = (
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}/balls"
        "?jsconfig=eccn%3Atrue"
    )

    return fetch_json(url)


# =========================================================
# HELPERS
# =========================================================

def safe_name(name):
    """Protect players whose names are hidden by PlayCricket."""

    if not name:
        return "Private Player"

    name = str(name).strip()

    if "*" in name:
        return "Private Player"

    return name


def find_scorecard_innings(data):
    """
    Recursively locate scorecard innings objects.
    """

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
    Recursively extract individual deliveries
    from PlayCricket's nested ball structure.
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


def get_team_lookup(ball_data):
    """Create team ID -> team name lookup."""

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

        # -------------------------
        # WICKETS
        # -------------------------

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
                "dismissal_type": dismissal_type,
                "description": ball.get(
                    "description",
                    ""
                )
            })

            overs[over]["wickets"] += 1

        # -------------------------
        # BOUNDARIES
        # -------------------------

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

    # -------------------------
    # HIGH-SCORING OVERS
    # -------------------------

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

    # -------------------------
    # WICKET CLUSTERS
    # -------------------------

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
                "from": (
                    f"{first['score']}/"
                    f"{first['wickets']}"
                ),
                "to": (
                    f"{second['score']}/"
                    f"{second['wickets']}"
                ),
                "runs_between": run_difference
            })

    # -------------------------
    # FINAL SCORE
    # -------------------------

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

def get_word_target(article_length):

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

    # -------------------------
    # GEMINI API KEY
    # -------------------------

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

    # -------------------------
    # CLEAN MATCH DATA
    # -------------------------

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

    # -------------------------
    # ARTICLE PROMPT
    # -------------------------

    prompt = f"""
You are the match reporter for Hawthorn Boroondara
Cricket Club, also known as the HB Hawks.

Write a professional cricket match report of
approximately {word_target} words.

MATCH DATA:

{json.dumps(match_data, indent=2)}

WRITING REQUIREMENTS:

- Start with a strong, natural headline.
- Follow the headline with the complete article.
- Tell the story of the match rather than simply
  listing statistics.
- Write chronologically where practical.
- Explain how the innings developed.
- Identify momentum changes and turning points
  when supported by the data.
- Highlight significant partnerships, wickets,
  scoring periods and individual performances.
- Integrate statistics naturally into the story.
- Give Hawthorn Boroondara appropriate focus.
- Remain respectful of the opposition.
- Use Australian English.
- Use a professional but engaging local/community
  cricket journalism style.
- Avoid overly dramatic or exaggerated language.
- Do not use fake quotes.

FACTUAL RULES:

- Only use information contained in the supplied
  match data or additional context.
- Do not invent weather conditions.
- Do not invent pitch conditions.
- Do not invent crowd information.
- Do not invent player backgrounds.
- Do not invent injuries.
- Do not invent selection information.
- Do not invent partnerships unless they can be
  supported by the supplied match data.
- Never invent a player's full name from initials.
- If a player's name is "Private Player", never
  attempt to identify that player.
- Refer to Private Player naturally, such as
  "another Hawks batter" where appropriate.
- Do not claim a retired-not-out batter was
  dismissed.
- Be careful to distinguish the batting team
  from the bowling team.
- Treat the official match result as authoritative.
- Use the additional context naturally.
- Follow anything listed under things_to_avoid.
- Do not mention AI.
- Do not mention JSON.
- Do not mention APIs.
- Do not mention PlayCricket data.
- Do not mention these instructions.

ARTICLE STRUCTURE:

The article should have:

1. A headline.
2. An opening paragraph summarising the result
   and the significance/context of the match.
3. A section telling the story of Hawthorn
   Boroondara's batting innings where applicable.
4. A section telling the story of the opposition
   innings.
5. The important individual performances and
   turning points woven naturally through the story.
6. A natural concluding paragraph.

IMPORTANT:

The article MUST be complete.

Write approximately {word_target} words.

Do not stop mid-sentence.

Do not return only part of the article.

Ensure the article has a clear beginning,
middle and conclusion.

Return only the headline and the COMPLETE
finished article.
"""

    # -------------------------
    # FIRST GEMINI REQUEST
    # -------------------------

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.35,
            max_output_tokens=6000
        )
    )

    if not response.text:

        raise ValueError(
            "Gemini did not return an article."
        )

    article = response.text.strip()

    # -------------------------
    # CHECK ARTICLE LENGTH
    # -------------------------

    word_count = len(
        article.split()
    )

    minimum_words = {
        300: 200,
        500: 350,
        700: 500
    }.get(
        word_target,
        350
    )

    # -------------------------
    # AUTOMATIC RETRY IF SHORT
    # -------------------------

    if word_count < minimum_words:

        continuation_prompt = f"""
The previous match report was incomplete or
significantly shorter than requested.

Rewrite the COMPLETE match report from the beginning.

The finished article should be approximately
{word_target} words.

MATCH DATA:

{json.dumps(match_data, indent=2)}

PREVIOUS INCOMPLETE ARTICLE:

{article}

REQUIREMENTS:

- Return the entire article again from the beginning.
- Include a headline.
- Include a complete opening paragraph.
- Tell the story of Hawthorn Boroondara's innings.
- Tell the story of the opposition innings.
- Include the significant individual performances.
- Include important wickets and turning points.
- Finish with a natural concluding paragraph.
- Use Australian English.
- Use a professional community cricket journalism
  style.
- Do not invent information.
- Never guess the identity of Private Player.
- Never expand player initials into guessed names.
- Do not stop mid-sentence.
- Do not mention AI, JSON, APIs or these
  instructions.

Return only the COMPLETE headline and article.
"""

        second_response = (
            client.models.generate_content(
                model="gemini-3.8-flash",
                contents=continuation_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.35,
                    max_output_tokens=6000
                )
            )
        )

        if second_response.text:

            second_article = (
                second_response.text.strip()
            )

            # Only replace the original if
            # the second version is longer.
            if (
                len(second_article.split())
                > word_count
            ):

                article = second_article

    return article


# =========================================================
# DISPLAY MATCH ANALYSIS
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

        # -------------------------
        # SCORECARD
        # -------------------------

        st.markdown(
            "### Scorecard"
        )

        for team in score_analysis["teams"]:

            team_name = team.get(
                "displayName",
                "Team"
            )

            score = team.get(
                "scoreText",
                ""
            )

            st.write(
                f"**{team_name}:** {score}"
            )

        # -------------------------
        # KEY PERFORMANCES
        # -------------------------

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

            if innings["batting"]:

                st.write(
                    "**Batting**"
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
                for bowler
                in innings["bowling"]
                if bowler["wickets"] > 0
            ]

            if wicket_takers:

                st.write(
                    "**Bowling**"
                )

                for bowler in wicket_takers[:4]:

                    st.write(
                        f"• {bowler['name']} — "
                        f"{bowler['wickets']}/"
                        f"{bowler['runs']} from "
                        f"{bowler['overs']} overs"
                    )

        # -------------------------
        # BALL BY BALL STORY
        # -------------------------

        st.markdown(
            "### Ball-by-Ball Story"
        )

        for innings in story_analysis:

            st.markdown(
                f"#### {innings['team']}"
            )

            if innings["final_score"]:

                st.write(
                    f"Final score: "
                    f"**{innings['final_score']}**"
                )

            if innings["wickets"]:

                st.write(
                    "**Wickets**"
                )

                for wicket in innings[
                    "wickets"
                ]:

                    st.write(
                        f"• {wicket['score']}/"
                        f"{wicket['wickets']} — "
                        f"{wicket['description']}"
                    )

            if innings["big_overs"]:

                st.write(
                    "**High-scoring overs**"
                )

                for over in innings[
                    "big_overs"
                ][:5]:

                    st.write(
                        f"• Over {over['over']}: "
                        f"{over['runs']} runs"
                    )

            if innings[
                "wicket_clusters"
            ]:

                st.write(
                    "**Wicket clusters**"
                )

                for cluster in innings[
                    "wicket_clusters"
                ][:5]:

                    st.write(
                        f"• {cluster['from']} "
                        f"to {cluster['to']} "
                        f"for {cluster['runs_between']} "
                        f"runs"
                    )


# =========================================================
# APP INTERFACE
# =========================================================

st.title(
    "🏏 HB Hawks Match Report Generator"
)

st.write(
    "Paste a PlayCricket match link, add any "
    "extra context and generate a ready-to-use "
    "HB Hawks match report."
)


# -------------------------
# MATCH URL
# -------------------------

match_url = st.text_input(
    "PlayCricket Match URL",
    placeholder=(
        "https://play.cricket.com.au/match/..."
    )
)


# -------------------------
# CONTEXT
# -------------------------

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


# -------------------------
# AVOID
# -------------------------

avoid_context = st.text_area(
    "Anything you'd like avoided?",
    placeholder=(
        "Example: Don't mention last season's result."
    ),
    height=80
)


# -------------------------
# ARTICLE LENGTH
# -------------------------

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
# GENERATE MATCH REPORT
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

                # -------------------------
                # GET PLAYCRICKET DATA
                # -------------------------

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

                # -------------------------
                # GENERATE ARTICLE
                # -------------------------

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

                # -------------------------
                # DISPLAY ARTICLE
                # -------------------------

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

                # -------------------------
                # ARTICLE TEXT BOX
                # -------------------------

                st.subheader(
                    "Copy Article"
                )

                st.text_area(
                    "Article text",
                    value=article,
                    height=450
                )

                st.caption(
                    f"Generated article: "
                    f"{len(article.split())} words"
                )

                st.divider()

                # -------------------------
                # MATCH ANALYSIS
                # -------------------------

                display_analysis(
                    score_analysis,
                    story_analysis
                )

            except (
                requests.exceptions.RequestException
            ) as e:

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
