import streamlit as st
import requests
import re

st.set_page_config(
    page_title="HB Hawks Match Report Generator",
    page_icon="🏏",
    layout="centered"
)


# ---------------------------------------------------
# FUNCTIONS
# ---------------------------------------------------

def extract_match_id(url):
    """Extract the PlayCricket Match ID from a match URL."""

    pattern = r"/match/([a-fA-F0-9-]+)"
    match = re.search(pattern, url)

    if match:
        return match.group(1)

    return None


def get_scorecard(match_id):
    """Retrieve scorecard data from PlayCricket."""

    url = (
        f"https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}"
        f"?responseModifier=includeScorecard"
        f"&jsconfig=eccn%3Atrue"
    )

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    return response.json()


def get_ball_by_ball(match_id):
    """Retrieve ball-by-ball data from PlayCricket."""

    url = (
        f"https://grassrootsapiproxy.cricket.com.au/"
        f"scores/matches/{match_id}/balls"
        f"?jsconfig=eccn%3Atrue"
    )

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    return response.json()


# ---------------------------------------------------
# PAGE
# ---------------------------------------------------

st.title("🏏 HB Hawks Match Report Generator")

st.write(
    "Paste a PlayCricket match URL below. "
    "The app will retrieve the scorecard and ball-by-ball "
    "data ready to create a match report."
)


# ---------------------------------------------------
# INPUTS
# ---------------------------------------------------

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
        "Hawks were looking to bounce back from last week."
    ),
    height=150
)

avoid_context = st.text_area(
    "Anything you'd like avoided?",
    placeholder="Example: Don't mention last season's result.",
    height=100
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


# ---------------------------------------------------
# GENERATE BUTTON
# ---------------------------------------------------

if st.button("Load Match", type="primary"):

    if not match_url:

        st.error("Please enter a PlayCricket match URL.")

    else:

        match_id = extract_match_id(match_url)

        if not match_id:

            st.error(
                "Could not identify a valid PlayCricket Match ID."
            )

        else:

            st.write("### Match ID")
            st.code(match_id)

            try:

                with st.spinner(
                    "Retrieving match data from PlayCricket..."
                ):

                    scorecard_data = get_scorecard(match_id)

                    ball_data = get_ball_by_ball(match_id)


                st.success(
                    "Match data retrieved successfully!"
                )


                # -----------------------------------
                # SCORECARD TEST
                # -----------------------------------

                st.subheader("✅ Scorecard")

                st.write(
                    "Scorecard data has been successfully retrieved."
                )

                with st.expander(
                    "View raw scorecard data"
                ):

                    st.json(scorecard_data)


                # -----------------------------------
                # BALL BY BALL TEST
                # -----------------------------------

                st.subheader("✅ Ball-by-Ball")

                st.write(
                    "Ball-by-ball data has been successfully retrieved."
                )

                with st.expander(
                    "View raw ball-by-ball data"
                ):

                    st.json(ball_data)


                # -----------------------------------
                # CONTEXT
                # -----------------------------------

                st.subheader("Match Report Settings")

                if match_context:

                    st.write("**Match Context**")

                    st.write(match_context)


                if avoid_context:

                    st.write("**Things to Avoid**")

                    st.write(avoid_context)


                st.write("**Article Length**")

                st.write(article_length)


            except requests.exceptions.RequestException as e:

                st.error(
                    "PlayCricket data could not be retrieved."
                )

                st.exception(e)

            except ValueError:

                st.error(
                    "PlayCricket returned data that "
                    "could not be read as JSON."
                )
