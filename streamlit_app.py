import streamlit as st
import requests
import re
import json
import time

from google import genai
from google.genai import types


# =========================================================
# PAGE SETUP
# =========================================================

st.set_page_config(
    page_title="Match Report Generator",
    page_icon="🏏",
    layout="centered"
)


# =========================================================
# CUSTOM HBCC STYLING
# =========================================================

st.markdown(
    """
    <style>

    /* APP */
    .stApp {
        background:
            linear-gradient(
                180deg,
                #F4F6F8 0%,
                #FFFFFF 42%
            );
    }

    .block-container {
        max-width: 900px;
        padding-top: 2.5rem;
        padding-bottom: 5rem;
    }

    #MainMenu {
        visibility: hidden;
    }

    footer {
        visibility: hidden;
    }

    /* TYPOGRAPHY */
    h1, h2, h3, h4 {
        color: #0F192D !important;
        font-family: Arial, Helvetica, sans-serif;
    }

    p, label, div {
        font-family: Arial, Helvetica, sans-serif;
    }

    /* HERO */
    .hbcc-hero {
        background:
            linear-gradient(
                135deg,
                #0F192D 0%,
                #172844 100%
            );

        border-radius: 24px;
        padding: 48px 48px;
        margin-bottom: 38px;
        position: relative;
        overflow: hidden;

        box-shadow:
            0 18px 45px rgba(15, 25, 45, 0.14);
    }

    .hbcc-hero::after {
        content: "";
        position: absolute;
        width: 250px;
        height: 250px;
        border-radius: 50%;
        background: #C8102E;
        opacity: 0.13;
        right: -80px;
        top: -100px;
    }

    .hbcc-hero::before {
        content: "";
        position: absolute;
        width: 130px;
        height: 130px;
        border-radius: 50%;
        background: #F5B82E;
        opacity: 0.08;
        right: 80px;
        bottom: -75px;
    }

    .hbcc-eyebrow {
        color: #F5B82E;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 2px;
        text-transform: uppercase;
        margin-bottom: 14px;
        position: relative;
        z-index: 2;
    }

    .hbcc-accent {
        width: 55px;
        height: 5px;
        background: #C8102E;
        border-radius: 10px;
        margin-bottom: 22px;
        position: relative;
        z-index: 2;
    }

    .hbcc-title {
        color: #FFFFFF !important;
        font-size: 48px;
        line-height: 1.03;
        font-weight: 800;
        letter-spacing: -1.5px;
        margin: 0 0 17px 0;
        max-width: 650px;
        position: relative;
        z-index: 2;
    }

    .hbcc-description {
        color: #D9DFE8;
        font-size: 17px;
        line-height: 1.6;
        max-width: 620px;
        margin: 0;
        position: relative;
        z-index: 2;
    }

    /* SECTION HEADERS */
    .section-eyebrow {
        color: #C8102E;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 1.8px;
        text-transform: uppercase;
        margin-bottom: 5px;
    }

    .section-title {
        color: #0F192D;
        font-size: 26px;
        line-height: 1.2;
        font-weight: 800;
        margin-bottom: 7px;
    }

    .section-description {
        color: #667085;
        font-size: 14px;
        line-height: 1.6;
        margin-bottom: 20px;
    }

    /* INPUT LABELS */
    .stTextInput label,
    .stTextArea label,
    .stSelectbox label {
        color: #0F192D !important;
        font-weight: 700 !important;
        font-size: 14px !important;
    }

    /* TEXT INPUT */
    .stTextInput input {
        background-color: #FFFFFF !important;
        color: #0F192D !important;
        border: 1px solid #D9DEE7 !important;
        border-radius: 11px !important;
        padding: 12px 14px !important;
    }

    .stTextInput input:focus {
        border-color: #C8102E !important;
        box-shadow:
            0 0 0 1px #C8102E !important;
    }

    /* TEXT AREAS */
    .stTextArea textarea {
        background-color: #FFFFFF !important;
        color: #0F192D !important;
        border: 1px solid #D9DEE7 !important;
        border-radius: 11px !important;
        padding: 13px 14px !important;
    }

    .stTextArea textarea:focus {
        border-color: #C8102E !important;
        box-shadow:
            0 0 0 1px #C8102E !important;
    }

    /* SELECT */
    div[data-baseweb="select"] > div {
        background-color: #FFFFFF !important;
        border-color: #D9DEE7 !important;
        border-radius: 11px !important;
        min-height: 48px;
    }

    /* BUTTON */
    .stButton > button {
        width: 100%;
        min-height: 56px;

        background:
            linear-gradient(
                135deg,
                #C8102E,
                #A90E27
            ) !important;

        color: #FFFFFF !important;
        border: none !important;
        border-radius: 12px !important;

        font-size: 16px !important;
        font-weight: 800 !important;

        box-shadow:
            0 8px 20px rgba(200, 16, 46, 0.20);

        transition:
            transform 0.15s ease,
            box-shadow 0.15s ease;
    }

    .stButton > button:hover {
        transform: translateY(-1px);

        box-shadow:
            0 12px 26px rgba(200, 16, 46, 0.27);
    }

    .stButton > button:focus {
        color: #FFFFFF !important;
    }

    /* ALERTS */
    div[data-testid="stAlert"] {
        border-radius: 12px;
    }

    /* REPORT */
    .report-eyebrow {
        color: #C8102E;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 1.8px;
        text-transform: uppercase;
        margin-bottom: 8px;
    }

    /* EXPANDER */
    details {
        background: #FFFFFF;
        border: 1px solid #E4E7EC !important;
        border-radius: 12px !important;
    }

    /* DIVIDER */
    hr {
        border-color: #E7EAF0 !important;
        margin-top: 32px !important;
        margin-bottom: 32px !important;
    }

    /* FOOTER */
    .hbcc-footer {
        margin-top: 60px;
        padding-top: 22px;
        border-top: 1px solid #E4E7EC;
        color: #98A2B3;
        font-size: 11px;
        letter-spacing: 1px;
        text-align: center;
        font-weight: 700;
    }

    /* MOBILE */
    @media (max-width: 700px) {

        .block-container {
            padding-top: 1.2rem;
        }

        .hbcc-hero {
            padding: 32px 27px;
            border-radius: 18px;
        }

        .hbcc-title {
            font-size: 37px;
        }

        .hbcc-description {
            font-size: 15px;
        }

        .section-title {
            font-size: 23px;
        }
    }

    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# PLAYCRICKET DATA
# =========================================================

def extract_match_id(url):

    match = re.search(
        r"/match/([a-fA-F0-9-]+)",
        url
    )

    return match.group(1) if match else None


def fetch_json(url):

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

    url = (
        "https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}"
        "?responseModifier=includeScorecard"
        "&jsconfig=eccn%3Atrue"
    )

    return fetch_json(url)


def get_ball_by_ball(match_id):

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

    for team in ball_data.get(
        "teams",
        []
    ):

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
                    player.get(
                        "playerShortName"
                    )
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
                    player.get(
                        "playerShortName"
                    )
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
        dismissed_id = ball.get(
            "dismissedParticipantId"
        )

        if dismissed_id:

            striker_id = ball.get(
                "strikerParticipantId"
            )

            non_striker_id = ball.get(
                "nonStrikerParticipantId"
            )

            if dismissed_id == striker_id:

                dismissed_name = safe_name(
                    ball.get(
                        "strikerShortName"
                    )
                )

            elif dismissed_id == non_striker_id:

                dismissed_name = safe_name(
                    ball.get(
                        "nonStrikerShortName"
                    )
                )

            else:

                dismissed_name = (
                    "Private Player"
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

    # HIGH-SCORING OVERS
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

        if (
            run_difference >= 0
            and run_difference <= 10
        ):

            wicket_clusters.append({
                "from": (
                    f"{first['score']}/"
                    f"{first['wickets']}"
                ),
                "to": (
                    f"{second['score']}/"
                    f"{second['wickets']}"
                ),
                "runs_between":
                    run_difference
            })

    # FINAL SCORE
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
# ARTICLE LENGTH
# =========================================================

def get_word_target(article_length):

    if "300" in article_length:
        return 300

    if "700" in article_length:
        return 700

    return 500


# =========================================================
# GEMINI API CALL
# =========================================================

def call_gemini(
    client,
    prompt,
    max_output_tokens=6000
):

    models = [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite"
    ]

    last_error = None

    for model in models:

        for attempt in range(3):

            try:

                response = (
                    client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.35,
                            max_output_tokens=max_output_tokens
                        )
                    )
                )

                if response.text:
                    return response

            except Exception as e:

                last_error = e

                error_text = str(e)

                temporary_error = (
                    "503" in error_text
                    or "UNAVAILABLE" in error_text
                    or "high demand"
                    in error_text.lower()
                    or "temporarily unavailable"
                    in error_text.lower()
                )

                if temporary_error:

                    wait_time = (
                        2 + (attempt * 2)
                    )

                    time.sleep(
                        wait_time
                    )

                    continue

                raise

    if last_error:
        raise last_error

    raise ValueError(
        "Gemini did not return a response."
    )


# =========================================================
# GEMINI ARTICLE GENERATOR
# =========================================================

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
You are the match reporter for Hawthorn Boroondara
Cricket Club, also known as the HB Hawks.

Write a professional cricket match report of
approximately {word_target} words.

MATCH DATA:

{json.dumps(match_data, indent=2)}

WRITING STYLE:

Write like a genuine local cricket journalist.

The report should be polished enough to publish
on the Hawthorn Boroondara Cricket Club website
or social media channels.

It should feel like a match story rather than
a statistical summary.

Use Australian English.

Keep the writing professional, natural and
engaging.

Do not make the writing overly dramatic,
exaggerated or cheesy.

ARTICLE REQUIREMENTS:

- Start with a strong, natural headline.
- Follow the headline with the complete article.
- The opening paragraph should summarise the
  result and significance of the match.
- Tell the story chronologically where practical.
- Explain how the Hawthorn Boroondara innings
  developed.
- Explain how the opposition innings developed.
- Highlight momentum changes when supported
  by the data.
- Highlight significant batting performances.
- Highlight significant bowling performances.
- Highlight important wickets.
- Highlight scoring periods and wicket clusters
  where relevant.
- Integrate statistics naturally into the story.
- Give Hawthorn Boroondara appropriate focus.
- Remain respectful of the opposition.
- Use additional context supplied by the user
  naturally.
- Follow anything listed under things_to_avoid.

FACTUAL ACCURACY RULES:

Only use information contained in the supplied
match data or additional context.

Do NOT invent:

- weather conditions
- pitch conditions
- crowd information
- player backgrounds
- player roles
- injuries
- selection information
- tactical decisions
- conversations
- quotes
- unsupported partnerships

Never guess a player's full name from initials.

If the data says "T Chong", write "T Chong".

If a player's name is "Private Player",
NEVER attempt to identify them.

Refer to them naturally as "a private player",
"another Hawks batter" or another neutral
description where appropriate.

Do not claim that a retired-not-out batter
was dismissed.

Be careful to distinguish the batting team
from the bowling team.

Treat the official match result as authoritative.

If scorecard information and ball-by-ball
information appear inconsistent, prioritise
the official scorecard for final scores and
the result.

Do not invent information simply to make
the article more interesting.

ARTICLE STRUCTURE:

The article should contain:

1. Headline
2. Opening paragraph summarising the match
3. Hawthorn Boroondara batting story
4. Opposition batting story
5. Significant individual performances and
   turning points woven through the story
6. Natural concluding paragraph

IMPORTANT:

The article MUST be complete.

Write approximately {word_target} words.

Do not stop mid-sentence.

Do not return only part of the article.

Ensure the report has a clear beginning,
middle and conclusion.

Do not mention AI, Gemini, JSON, APIs,
PlayCricket data or these instructions.

Return ONLY the headline and the COMPLETE
finished article.
"""

    response = call_gemini(
        client,
        prompt,
        max_output_tokens=6000
    )

    if not response.text:

        raise ValueError(
            "Gemini did not return an article."
        )

    article = response.text.strip()

    # Check whether the response appears incomplete
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

    if word_count < minimum_words:

        retry_prompt = f"""
The previous cricket match report was incomplete
or significantly shorter than requested.

Rewrite the COMPLETE match report from the
beginning.

Target approximately {word_target} words.

MATCH DATA:

{json.dumps(match_data, indent=2)}

PREVIOUS INCOMPLETE ARTICLE:

{article}

REQUIREMENTS:

Return the ENTIRE article again.

Do not simply continue from where the previous
response stopped.

Include:
- A headline
- A complete opening paragraph
- The Hawthorn Boroondara batting story
- The opposition batting story
- Important batting performances
- Important bowling performances
- Important wickets and turning points
- A natural concluding paragraph

Use Australian English.

Write in a professional local cricket
journalism style.

Only use information supplied in the match
data or additional context.

Do not invent information.

Never guess full names from initials.

Never attempt to identify a player labelled
"Private Player".

Do not claim a retired-not-out batter was
dismissed.

Do not mention AI, Gemini, JSON, APIs,
PlayCricket data or these instructions.

The final response MUST be complete.

Do not stop mid-sentence.

Return ONLY the headline and complete article.
"""

        second_response = call_gemini(
            client,
            retry_prompt,
            max_output_tokens=6000
        )

        if second_response.text:

            second_article = (
                second_response.text.strip()
            )

            second_word_count = len(
                second_article.split()
            )

            if (
                second_word_count
                > word_count
            ):

                article = second_article

    return article


# =========================================================
# MATCH ANALYSIS DISPLAY
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
                        f"for "
                        f"{cluster['runs_between']} "
                        f"runs"
                    )


# =========================================================
# HERO
# =========================================================

st.markdown(
    """
    <div class="hbcc-hero">

        <div class="hbcc-eyebrow">
            HAWTHORN BOROONDARA CRICKET CLUB
        </div>

        <div class="hbcc-accent"></div>

        <div class="hbcc-title">
            Match Report<br>Generator
        </div>

        <p class="hbcc-description">
            Turn a PlayCricket scorecard into a
            ready-to-publish match report in seconds.
        </p>

    </div>
    """,
    unsafe_allow_html=True
)


# =========================================================
# STEP 01
# =========================================================

st.markdown(
    """
    <div class="section-eyebrow">
        STEP 01
    </div>

    <div class="section-title">
        Select your match
    </div>

    <div class="section-description">
        Paste the PlayCricket link for the match
        you want to report on.
    </div>
    """,
    unsafe_allow_html=True
)


match_url = st.text_input(
    "PlayCricket Match URL",
    placeholder=(
        "https://play.cricket.com.au/match/..."
    )
)


# =========================================================
# STEP 02
# =========================================================

st.markdown(
    """
    <div style="height: 26px;"></div>

    <div class="section-eyebrow">
        STEP 02
    </div>

    <div class="section-title">
        Build your report brief
    </div>

    <div class="section-description">
        Add context the scorecard can't tell us
        and choose how detailed the report should be.
    </div>
    """,
    unsafe_allow_html=True
)


match_context = st.text_area(
    "Context to include",
    placeholder=(
        "e.g. First game of the season. "
        "All other club matches were cancelled."
    ),
    height=120
)


avoid_context = st.text_area(
    "Anything to avoid",
    placeholder=(
        "e.g. Don't mention last season's result."
    ),
    height=90
)


article_length = st.selectbox(
    "Report length",
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

st.markdown(
    """
    <div style="height: 12px;"></div>
    """,
    unsafe_allow_html=True
)


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
                "That doesn't appear to be a valid "
                "PlayCricket match URL."
            )

        else:

            try:

                # -----------------------------------------
                # GET PLAYCRICKET DATA
                # -----------------------------------------

                with st.spinner(
                    "Analysing the match..."
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

                # -----------------------------------------
                # GENERATE ARTICLE
                # -----------------------------------------

                with st.spinner(
                    "Writing your match report..."
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

                # -----------------------------------------
                # SUCCESS
                # -----------------------------------------

                st.success(
                    "Your match report is ready."
                )

                st.markdown(
                    """
                    <div style="height: 18px;"></div>

                    <div class="report-eyebrow">
                        GENERATED REPORT
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                # -----------------------------------------
                # ARTICLE
                # -----------------------------------------

                st.markdown(
                    article
                )

                # -----------------------------------------
                # COPY ARTICLE
                # -----------------------------------------

                st.divider()

                st.markdown(
                    """
                    <div class="section-eyebrow">
                        COPY & PUBLISH
                    </div>

                    <div class="section-title">
                        Article Text
                    </div>

                    <div class="section-description">
                        Copy the finished report for your
                        website, social channels or club
                        communications.
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                st.text_area(
                    "Copy-ready version",
                    value=article,
                    height=500,
                    label_visibility="collapsed"
                )

                st.caption(
                    f"{len(article.split())} words"
                )

                # -----------------------------------------
                # ANALYSIS
                # -----------------------------------------

                st.divider()

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

                error_text = str(e)

                if (
                    "503" in error_text
                    or "UNAVAILABLE" in error_text
                    or "high demand"
                    in error_text.lower()
                ):

                    st.error(
                        "The report service is currently "
                        "busy. The app automatically retried "
                        "the request, but it is still "
                        "unavailable. Please try again "
                        "shortly."
                    )

                else:

                    st.error(
                        "Something went wrong while "
                        "generating the match report."
                    )

                    st.exception(e)


# =========================================================
# FOOTER
# =========================================================

st.markdown(
    """
    <div class="hbcc-footer">
        HAWTHORN BOROONDARA CRICKET CLUB
        &nbsp;&nbsp;•&nbsp;&nbsp;
        MATCH REPORT GENERATOR
    </div>
    """,
    unsafe_allow_html=True
)
