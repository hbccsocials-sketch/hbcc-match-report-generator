import streamlit as st
import re

st.set_page_config(
    page_title="HB Hawks Match Report Generator",
    page_icon="🏏",
    layout="centered"
)

def extract_match_id(url):
    pattern = r"/match/([a-fA-F0-9-]+)"
    match = re.search(pattern, url)

    if match:
        return match.group(1)

    return None


st.title("🏏 HB Hawks Match Report Generator")

st.write(
    "Paste a PlayCricket match link below to generate "
    "an article-style match report."
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

if st.button("Generate Match Report", type="primary"):

    if not match_url:
        st.error("Please enter a PlayCricket match URL.")

    else:
        match_id = extract_match_id(match_url)

        if not match_id:
            st.error("Could not identify a valid PlayCricket Match ID.")

        else:
            st.success("PlayCricket match identified successfully!")

            st.write("### Match ID")
            st.code(match_id)

            if match_context:
                st.write("### Match Context")
                st.write(match_context)

            if avoid_context:
                st.write("### Things to Avoid")
                st.write(avoid_context)

            st.write("### Article Length")
            st.write(article_length)
