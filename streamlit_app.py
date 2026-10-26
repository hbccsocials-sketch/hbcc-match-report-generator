import html

import json
import io
import zipfile
import hmac
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import re
import time
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone

import requests
import streamlit as st
from google import genai
from google.genai import types


# ============================================================
# PRIVATE SUPABASE PERSISTENCE (server-side credentials only)
# ============================================================
from urllib.parse import quote
import uuid


def sb_ready():
    return bool(st.secrets.get("SUPABASE_URL") and st.secrets.get("SUPABASE_SECRET_KEY"))


def sb_request(method, endpoint, *, json_data=None, data=None, content_type=None, params=None):
    if not sb_ready():
        raise RuntimeError("Supabase secrets are missing in Streamlit Cloud")
    root = str(st.secrets["SUPABASE_URL"]).rstrip("/")
    key = str(st.secrets["SUPABASE_SECRET_KEY"])
    headers = {"apikey": key, "Authorization": "Bearer " + key}
    if content_type:
        headers["Content-Type"] = content_type
    response = requests.request(method, root + endpoint, headers=headers,
                                json=json_data, data=data, params=params, timeout=(5, 18) if method == "GET" else 35)
    if not response.ok:
        raise RuntimeError(f"Supabase {response.status_code}: {response.text[:350]}")
    return response


@st.cache_data(ttl=180, max_entries=12, show_spinner=False)
def sb_rows(table):
    """Short-lived shared cache for read-only Supabase table metadata."""
    return sb_request("GET", "/rest/v1/" + table, params={"select": "*"}).json()


def sb_upsert(table, record):
    # PostgREST requires Prefer: resolution=merge-duplicates for true UPSERT.
    # on_conflict alone still performs INSERT and raises 23505 on existing IDs.
    if not sb_ready():
        raise RuntimeError("Supabase secrets are missing in Streamlit Cloud")
    root = str(st.secrets["SUPABASE_URL"]).rstrip("/")
    key = str(st.secrets["SUPABASE_SECRET_KEY"])
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    conflict_column = "key" if table == "hbcc_settings" else ("source_id" if table == "hbcc_club_mappings" else "id")
    response = requests.post(
        root + "/rest/v1/" + table,
        headers=headers,
        params={"on_conflict": conflict_column},
        json=record,
        timeout=35,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase {response.status_code}: {response.text[:350]}")
    sb_rows.clear()
    return response


def sb_delete(table, field, value):
    sb_request("DELETE", "/rest/v1/" + table, params={field: "eq." + str(value)})
    sb_rows.clear()


def sb_upload(path, content):
    # Private bucket: only this server-side secret can upload/download.
    endpoint = "/storage/v1/object/hbcc-assets/" + quote(path, safe="/")
    if not sb_ready():
        raise RuntimeError("Supabase secrets are missing")
    root = str(st.secrets["SUPABASE_URL"]).rstrip("/")
    key = str(st.secrets["SUPABASE_SECRET_KEY"])
    response = requests.post(root + endpoint, data=content, headers={
        "apikey": key, "Authorization": "Bearer " + key,
        "Content-Type": "image/png", "x-upsert": "true",
    }, timeout=35)
    if not response.ok:
        raise RuntimeError(f"Supabase Storage {response.status_code}: {response.text[:350]}")
    sb_download.clear()
    return path


@st.cache_data(ttl=600, max_entries=180, show_spinner=False)
def sb_download(path):
    if not path:
        return None
    return sb_request("GET", "/storage/v1/object/authenticated/hbcc-assets/" + quote(path, safe="/")).content


def sb_image(uploaded):
    image = Image.open(io.BytesIO(uploaded)).convert("RGBA")
    image.thumbnail((2500, 2500))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def sb_save_club(club, image_bytes=None):
    # Existing GitHub logos remain a fallback unless replaced by a cloud upload.
    if image_bytes is not None:
        path = "club-logos/" + uuid.uuid4().hex + ".png"
        sb_upload(path, image_bytes)
        club["logo_path"] = path
    club_id = str(club.get("id") or uuid.uuid4().hex)
    club["id"] = club_id
    sb_upsert("hbcc_clubs", {"id": club_id, "name": club["name"],
              "abbreviation": club.get("abbr", ""), "aliases": club.get("aliases", []),
              "logo_path": club.get("logo_path")})


def sb_save_template(kind, name, image_bytes, settings):
    """Persist template metadata without creating duplicate background objects.

    Existing random-named images remain untouched until the user actually
    changes the background. A replacement is written to one stable path per
    template, so repeated saves do not accumulate orphaned files.
    """
    if kind not in ("fixture", "team_selection"):
        raise ValueError("Unknown template type")
    template_id = ("fixture_" if kind == "fixture" else "team_") + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not template_id.strip("_"):
        raise ValueError("Enter a template name")

    # Fetch the authoritative saved record; session state may be stale.
    matches = sb_request("GET", "/rest/v1/hbcc_templates", params={
        "id": "eq." + template_id, "select": "id,background_path"
    }).json()
    previous = matches[0] if matches else None
    previous_path = previous.get("background_path") if previous else None
    path = previous_path

    if image_bytes is not None:
        normalized = sb_image(image_bytes)
        # Compare with the existing stored image. Settings-only saves do not
        # upload another PNG, even if the uploader still holds the same image.
        existing_bytes = sb_download(previous_path) if previous_path else None
        if existing_bytes != normalized:
            path = kind.replace("_", "-") + "-templates/" + template_id + ".png"
            # POST with x-upsert:true replaces a file at the same path.
            sb_upload(path, normalized)

    sb_upsert("hbcc_templates", {"id": template_id, "name": name,
              "template_type": kind, "background_path": path,
              "settings": dict(settings)})
    sb_refresh_templates()

def sb_refresh_templates():
    st.session_state.sb_templates = sb_rows("hbcc_templates")


# Match Results defaults must exist before Supabase restores saved templates.
RESULTS_DEFAULT_SETTINGS = {
    "outcome_top": 300, "score_top": 495, "performers_top": 755,
    "logo_scale": 1.0, "gold": "#f5b82e",
}


def sb_load_once():
    if st.session_state.get("sb_loaded") or not sb_ready():
        return
    try:
        cloud_clubs = sb_rows("hbcc_clubs")
        local = fixture_library()
        # Prefer cloud records over GitHub manifest, without dropping old clubs.
        for record in cloud_clubs:
            cloud = {"id": record["id"], "name": record["name"],
                     "abbr": record.get("abbreviation") or "",
                     "aliases": record.get("aliases") or [],
                     "logo_path": record.get("logo_path")}
            idx = next((i for i, c in enumerate(local) if c.get("id") == cloud["id"] or fixture_key(c.get("name")) == fixture_key(cloud["name"])), None)
            if idx is None:
                local.append(cloud)
            else:
                local[idx].update(cloud)
        sb_refresh_templates()
        for kind, key, settings_key in (("fixture", "fixture_background_bytes", "fixture_graphic_settings"),
                                         ("team_selection", "graphic_template_bytes", "graphic_settings")):
            selected = next((x for x in st.session_state.sb_templates if x["template_type"] == kind and x["id"] == ("fixture_default" if kind == "fixture" else "team_default")), None)
            if selected:
                if selected.get("background_path"):
                    st.session_state[key] = sb_download(selected["background_path"])
                if isinstance(selected.get("settings"), dict):
                    if kind == "team_selection":
                        st.session_state.graphic_settings_version = 3
                    st.session_state[settings_key] = selected["settings"]
        # Restore the selected team background and settings across restarts.
        team_active_rows = sb_request("GET", "/rest/v1/hbcc_settings", params={
            "key": "eq.active_team_template", "select": "value"}).json()
        team_active_id = ((team_active_rows[0].get("value") or {}).get("id")
                          if team_active_rows else "team_default")
        active_team = next((t for t in st.session_state.sb_templates
                            if t.get("id") == team_active_id and t.get("template_type") == "team_selection"), None)
        if active_team:
            st.session_state.team_active_template_id = team_active_id
            st.session_state.team_editor_selection = team_active_id
            st.session_state.graphic_settings = {**DEFAULT_GRAPHIC_SETTINGS, **(active_team.get("settings") or {})}
            st.session_state.graphic_settings_version = 3
            if active_team.get("background_path"):
                st.session_state.graphic_template_bytes = sb_download(active_team["background_path"])
        # Restore the independently selected results design.
        result_rows = sb_request("GET", "/rest/v1/hbcc_settings", params={
            "key":"eq.active_results_template", "select":"value"}).json()
        result_id = ((result_rows[0].get("value") or {}).get("id") if result_rows else None)
        result_templates = [t for t in st.session_state.sb_templates if t.get("template_type") == "match_results"]
        result_record = next((t for t in result_templates if t.get("id") == result_id), None)
        if not result_record and result_templates:
            result_record = result_templates[0]
        if result_record:
            st.session_state.results_active_template_id = result_record["id"]
            st.session_state.results_graphic_settings = {**RESULTS_DEFAULT_SETTINGS, **(result_record.get("settings") or {})}
            if result_record.get("background_path"):
                st.session_state.results_background_bytes = sb_download(result_record["background_path"])
            else:
                st.session_state.pop("results_background_bytes", None)
        # Restore the template selected by the admin on previous visits.
        active_settings = sb_request("GET", "/rest/v1/hbcc_settings", params={
            "key": "eq.active_fixture_template", "select": "value"}).json()
        active_id = ((active_settings[0].get("value") or {}).get("id")
                     if active_settings else "fixture_default")
        active_record = next((t for t in st.session_state.sb_templates
                              if t.get("id") == active_id and t.get("template_type") == "fixture"), None)
        if active_record:
            st.session_state.fixture_active_template_id = active_id
            st.session_state.fixture_editor_selection = active_id
            st.session_state.fixture_graphic_settings = {**{
                "title": "UPCOMING MATCHES", "title_size": 90, "cards_top": 240,
                "cards_bottom": 1045, "yellow": "#f5b82e", "logo_scale": 1.65,
                "logo_bleed": 55}, **(active_record.get("settings") or {})}
            if active_record.get("background_path"):
                st.session_state.fixture_background_bytes = sb_download(active_record["background_path"])
            else:
                st.session_state.pop("fixture_background_bytes", None)
        else:
            st.session_state.setdefault("fixture_active_template_id", "fixture_default")
        st.session_state.sb_loaded = True
    except Exception as exc:
        st.session_state.sb_error = str(exc)


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


def scheduled_match_dates(record):
    """Return every playing date from PlayCricket's matchSchedule.

    Two-day matches can contain multiple scheduled days in an array. The
    generic parse_date() helper only returns one, so it cannot be used for
    weekend inclusion tests.
    """
    schedule = record.get("matchSchedule") if isinstance(record, dict) else None
    found = set()

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            # Try the date-bearing fields of each scheduled day before
            # descending into nested schedule structures.
            for key in (
                "startDateTime", "startDate", "matchDate", "date",
                "scheduledStart", "scheduledDate", "startTime",
                "start", "dateTime", "localStartTime", "utcStartTime",
                "startDateTimeLocal", "days", "dates", "matchSchedule"
            ):
                if key in value:
                    walk(value[key])
            if all(k in value for k in ("year", "month", "day")):
                day = parse_date(value)
                if day:
                    found.add(day)
        else:
            day = parse_date(value)
            if day:
                found.add(day)

    walk(schedule)
    return sorted(found)


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
        timeout=(5, 18)
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


def fixture_home_away(record, detail=None):
    """Read actual home/away indicators before relying on display order."""
    sources = [record]
    if isinstance(detail, dict):
        sources.extend([detail, detail.get("matchSummary"), detail.get("match")])
    for obj in sources:
        if not isinstance(obj, dict):
            continue
        h = obj.get("homeTeam") or obj.get("homeTeamSummary")
        a = obj.get("awayTeam") or obj.get("awayTeamSummary")
        if isinstance(h, dict) and isinstance(a, dict):
            hn, an = name_of(h), name_of(a)
            if hn and an:
                return hn, an
        teams = teams_of(obj)
        home = away = ""
        for team in teams:
            role = str(team.get("homeAway") or team.get("homeOrAway") or team.get("side") or team.get("teamType") or "").lower()
            if role in ("home", "h") or team.get("isHome") is True:
                home = name_of(team)
            if role in ("away", "a") or team.get("isAway") is True:
                away = name_of(team)
        if home and away:
            return home, away
    return None, None


def fixture_from(record, grade, team_id, detail=None):
    summary = (
        (detail or {}).get("matchSummary")
        or {}
    )

    if not isinstance(summary, dict):
        summary = {}

    playing_dates = scheduled_match_dates(record)
    when = (
        playing_dates[0] if playing_dates
        else parse_date(record.get("matchSchedule"))
        or parse_date(record)
        or parse_date(summary)
    )

    label = fixture_name(record)

    if label == "Fixture":
        label = fixture_name(summary)

    mid = match_id(record)
    home_name, away_name = fixture_home_away(record, detail)

    return {
        "id": mid,
        "grade": grade,
        "team_id": team_id,
        "date": str(when) if when else "",
        "playing_dates": [str(day) for day in playing_dates],
        "name": label,
        "home_name": home_name or "",
        "away_name": away_name or "",
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
            f"Date: {graphic_date(row)}",
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


def card_copy_parts(row):
    """Build copy-ready Canva text using the *edited* player list."""
    grade = str(row.get("grade") or "Team").upper()
    fixture = str(row.get("name") or "")
    parts = re.split(r"\s+vs\.?\s+", fixture, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        left, right = (part.strip() for part in parts)
        # PlayCricket can list HBCC as either home or away.
        if "hawthorn boroondara" in left.lower() and "hawthorn boroondara" not in right.lower():
            opponent = right
        elif "hawthorn boroondara" in right.lower() and "hawthorn boroondara" not in left.lower():
            opponent = left
        else:
            opponent = right
        match_line = f"vs {opponent}"
    else:
        match_line = fixture or "Opponent unavailable"

    playing_dates = row.get("playing_dates") or []
    date_line = graphic_date(row)
    venue_line = str(row.get("venue") or "Venue unavailable")
    details = "\n".join([grade, match_line, f"Date: {date_line}", f"Venue: {venue_line}"])

    players = row.get("players") or []
    if players:
        # Player edits may contain numbering pasted from elsewhere.
        player_lines = []
        for i, player in enumerate(players, 1):
            clean = re.sub(r"^\s*\d+[.)]\s*", "", str(player)).strip()
            if clean:
                player_lines.append(f"{i}. {clean}")
        player_text = "\n".join(player_lines) or "Selected team coming soon"
    else:
        player_text = "Selected team coming soon"
    return details, player_text, details + "\n\n" + player_text


# ============================================================
# INSTAGRAM TEAM GRAPHIC GENERATOR
# ============================================================

ASSET_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = ASSET_DIR / "hbcc_team_template.png"
DEFAULT_FONT = ASSET_DIR / "ITC Franklin Gothic LT Medium.otf"
DEFAULT_GRAPHIC_SETTINGS = {
    "width": 1080, "height": 1350,
    "left": 84, "top": 77,
    "title_size": 51, "details_size": 45,
    "players_size": 45, "line_spacing": 1.3,
    "yellow_line_spacing": 1.3, "yellow_bold_stroke": 1,
    "section_gap": 24,
    "heading_color": "#ffce2c", "players_color": "#ffffff",
    "max_content_y": 1130,
}


def graphic_settings():
    if st.session_state.get("graphic_settings_version") != 3:
        st.session_state.graphic_settings = dict(DEFAULT_GRAPHIC_SETTINGS)
        st.session_state.graphic_settings_version = 3
    if "graphic_settings" not in st.session_state:
        st.session_state.graphic_settings = dict(DEFAULT_GRAPHIC_SETTINGS)
    return st.session_state.graphic_settings


def image_asset():
    uploaded = st.session_state.get("graphic_template_bytes")
    if uploaded:
        return Image.open(io.BytesIO(uploaded)).convert("RGB")
    if DEFAULT_TEMPLATE.exists():
        return Image.open(DEFAULT_TEMPLATE).convert("RGB")
    return Image.new("RGB", (580, 725), "#0f192d")


# Broadcast font system shared by every generated graphic.
# These files live beside streamlit_app.py in the GitHub repository.
BROADCAST_BOLD_FONT = Path(__file__).resolve().parent / "RobotoCondensed-Bold.ttf"
BROADCAST_BODY_FONT = Path(__file__).resolve().parent / "ITC Franklin Gothic LT Medium.otf"


@lru_cache(maxsize=128)
def broadcast_font(size, bold=False):
    """Roboto Condensed for display text; Franklin Gothic for supporting text.

    Keep existing installations functional if the new Roboto file is absent.
    """
    paths = ([BROADCAST_BOLD_FONT, BROADCAST_BODY_FONT] if bold else
             [BROADCAST_BODY_FONT, BROADCAST_BOLD_FONT])
    paths += [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf') if bold
              else Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for path in paths:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), int(size))
            except (OSError, ValueError):
                continue
    return ImageFont.load_default()


def font_asset(size, bold=False):
    # A font uploaded in the Team Selection template manager still takes
    # precedence for that template; all others use the shared broadcast fonts.
    uploaded = st.session_state.get('graphic_font_bytes')
    if uploaded:
        try:
            return ImageFont.truetype(io.BytesIO(uploaded), size=int(size))
        except (OSError, ValueError):
            pass
    return broadcast_font(size, bold)


def graphic_opponent(row):
    name = row.get("name", "")
    teams = [part.strip() for part in re.split(r"\s+vs\s+", name, flags=re.I)]
    if len(teams) < 2:
        return name or "Opponent to be confirmed"
    opposing = [team for team in teams if "hawthorn boroondara" not in team.lower()]
    return opposing[0] if opposing else teams[-1]


def graphic_date(row):
    """Use the same human-readable date style as Upcoming Fixtures graphics."""
    dates = row.get("playing_dates") or [row.get("date", "")]
    return fixture_pretty_dates(dates)


def wrap_to_width(draw, text, font, max_width):
    words = str(text).split()
    if not words:
        return [""]
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), trial, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def render_team_graphic(row, settings=None):
    """Render fixed-baseline Canva-like typography without runaway spacing."""
    cfg = dict(DEFAULT_GRAPHIC_SETTINGS)
    cfg.update(settings or graphic_settings())
    width, height = int(cfg["width"]), int(cfg["height"])
    image = image_asset().resize((width, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    left, y = int(cfg["left"]), int(cfg["top"])
    usable_width = width - left - 65
    yellow = cfg["heading_color"]
    white = cfg["players_color"]
    # 1.3 is a multiplier of FONT SIZE, not font metrics or total image height.
    # Clamp old session/JSON values to avoid a single bad value spreading names apart.
    white_spacing = min(1.6, max(1.1, float(cfg.get("line_spacing", 1.3))))
    yellow_spacing = min(1.6, max(1.1, float(cfg.get("yellow_line_spacing", 1.3))))
    stroke = min(3, max(0, int(cfg.get("yellow_bold_stroke", 1))))

    def write_lines(text, font, colour, step, stroke_width=0):
        nonlocal y
        for line in wrap_to_width(draw, text, font, usable_width):
            draw.text((left, y), line, font=font, fill=colour,
                      stroke_width=stroke_width, stroke_fill=colour)
            y += step

    title_size = int(cfg["title_size"])
    detail_size = int(cfg["details_size"])
    title_font = font_asset(title_size, bold=True)
    detail_font = font_asset(detail_size, bold=False)
    write_lines(str(row.get("grade", "")).upper(), title_font, yellow,
                round(title_size * yellow_spacing), stroke)
    for line in (
        "vs " + graphic_opponent(row),
        "Date: " + graphic_date(row),
        "Venue: " + (row.get("venue") or "To be confirmed"),
    ):
        write_lines(line, detail_font, yellow,
                    round(detail_size * yellow_spacing), stroke)
    y += int(cfg["section_gap"])

    players = row.get("players") or []
    numbered = [f"{i}. {re.sub(r'^\s*\d+[.)]\s*', '', str(player)).strip()}"
                for i, player in enumerate(players, 1)]
    if not numbered:
        numbered = ["Selected team coming soon"]

    max_y = min(int(cfg["max_content_y"]), height - 205)
    preferred = int(cfg["players_size"])
    # Determine the full list height BEFORE drawing; never let a previous
    # player's y position influence the font-fitting calculation.
    for size in range(preferred, 19, -1):
        candidate = font_asset(size)
        step = round(size * white_spacing)
        line_count = sum(len(wrap_to_width(draw, item, candidate, usable_width))
                         for item in numbered)
        if y + line_count * step <= max_y:
            break
    else:
        size = 20
        candidate = font_asset(size)
        step = round(size * white_spacing)

    for player in numbered:
        write_lines(player, candidate, white, step)
    return image


def graphic_png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def graphic_filename(row, index):
    label = re.sub(r"[^a-z0-9]+", "_", row.get("grade", "team").lower()).strip("_")
    return f"{index:02d}_{label}_team_selection.png"


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
# UPCOMING FIXTURE GRAPHICS AND CLUB VISUAL LIBRARY
# ============================================================
import unicodedata

FIXTURE_LIBRARY_DIR = Path(__file__).resolve().parent / "club_visuals"
FIXTURE_LIBRARY_MANIFEST = FIXTURE_LIBRARY_DIR / "clubs.json"
FIXTURE_SIZE = (1080, 1350)


def fixture_key(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = re.sub(r"\b(?:women'?s|mens|men'?s|seniors?|juniors?|first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th|xi|cricket|club|cc)\b", " ", value)
    return re.sub(r"[^a-z0-9]", "", value)


def fixture_library():
    if "fixture_clubs" not in st.session_state:
        records = []
        if FIXTURE_LIBRARY_MANIFEST.exists():
            try:
                records = json.loads(FIXTURE_LIBRARY_MANIFEST.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                records = []
        st.session_state.fixture_clubs = records if isinstance(records, list) else []
    if "fixture_uploaded_logos" not in st.session_state:
        st.session_state.fixture_uploaded_logos = {}
    return st.session_state.fixture_clubs


@lru_cache(maxsize=128)
def _local_logo_bytes(filename):
    path = FIXTURE_LIBRARY_DIR / filename
    return path.read_bytes() if filename and path.is_file() else None


def fixture_logo_bytes(entry):
    if entry.get("logo_path") and sb_ready():
        cache = st.session_state.setdefault("sb_logo_cache", {})
        path = entry["logo_path"]
        if path not in cache:
            try:
                cache[path] = sb_download(path)
            except Exception:
                cache[path] = None
        if cache[path]:
            return cache[path]
    filename = Path(str(entry.get("file", ""))).name
    if filename in st.session_state.fixture_uploaded_logos:
        return st.session_state.fixture_uploaded_logos[filename]
    return _local_logo_bytes(filename)


def fixture_team_identifiers(detail, team_name):
    """Find real identifiers from matching PlayCricket team objects only."""
    wanted = fixture_key(team_name)
    if not wanted or not isinstance(detail, dict):
        return []
    found = set()
    visited = set()
    def visit(obj, depth=0):
        if depth > 7 or id(obj) in visited:
            return
        visited.add(id(obj))
        if isinstance(obj, list):
            for item in obj[:80]:
                visit(item, depth + 1)
            return
        if not isinstance(obj, dict):
            return
        # IDs are collected only from objects explicitly identifying this team.
        candidate_names = [name_of(obj)]
        for key in ("teamName", "displayName", "name"):
            if isinstance(obj.get(key), str):
                candidate_names.append(obj[key])
        if any(fixture_key(n) == wanted for n in candidate_names if n):
            for field in ("teamId", "clubTeamId"):
                val = obj.get(field)
                if isinstance(val, (str, int)) and str(val).strip():
                    found.add("team:" + str(val).strip())
            # 'id' is a team ID only on a team-shaped object.
            if (obj.get("teamName") or obj.get("teamId") or obj.get("clubTeamId")
                    or obj.get("players") is not None or obj.get("homeAway") is not None):
                val = obj.get("id")
                if isinstance(val, (str, int)) and str(val).strip():
                    found.add("team:" + str(val).strip())
            for field in ("clubId", "organisationId", "organizationId"):
                val = obj.get(field)
                if isinstance(val, (str, int)) and str(val).strip():
                    found.add("club:" + str(val).strip())
            club = obj.get("club")
            if isinstance(club, dict) and club.get("id"):
                found.add("club:" + str(club["id"]).strip())
        for value in obj.values():
            if isinstance(value, (dict, list)):
                visit(value, depth + 1)
    visit(detail)
    return sorted(found)


def fixture_id_mappings():
    if "fixture_id_mappings" not in st.session_state:
        st.session_state.fixture_id_mappings = {}
        if sb_ready():
            try:
                st.session_state.fixture_id_mappings = {
                    str(r["source_id"]): str(r["club_id"])
                    for r in sb_rows("hbcc_club_mappings")
                }
            except Exception as exc:
                st.session_state.fixture_mapping_error = str(exc)
    return st.session_state.fixture_id_mappings


def fixture_club_by_id(club_id):
    return next((c for c in fixture_library() if str(c.get("id")) == str(club_id)), None)


def fixture_resolve_club(name, identifiers=None, override_id=None):
    if override_id:
        club = fixture_club_by_id(override_id)
        if club:
            return club, "manual"
    mapping = fixture_id_mappings()
    ids = identifiers or []
    matches = {mapping[x] for x in ids if x in mapping and fixture_club_by_id(mapping[x])}
    if len(matches) == 1:
        return fixture_club_by_id(next(iter(matches))), "ID"
    if len(matches) > 1:
        return None, "conflicting IDs"
    club = fixture_find_club(name)
    return club, "name/alias" if club else "unmatched"


def fixture_save_mapping(identifiers, club, source_name=""):
    if not identifiers:
        raise ValueError("No team/club ID was available. Select the logo manually for this fixture, but it cannot be remembered by ID.")
    if not sb_ready():
        raise ValueError("Supabase is not connected")
    if not club.get("id"):
        # Give original GitHub clubs a stable database identity first.
        club["id"] = "club_" + fixture_key(club.get("name"))
        sb_save_club(club)
    for identifier in identifiers:
        record = {
            "source_id": identifier, "club_id": str(club["id"]),
            "source_type": identifier.split(":", 1)[0],
        }
        # A team name belongs to a team ID. Club IDs can span many grades,
        # so never overwrite their shared record with an individual team name.
        if identifier.startswith("team:") and str(source_name).strip():
            record["source_name"] = str(source_name).strip()
        sb_upsert("hbcc_club_mappings", record)
        fixture_id_mappings()[identifier] = str(club["id"])


def fixture_find_club(name):
    key = fixture_key(name)
    if not key:
        return None
    clubs = fixture_library()
    exact = [x for x in clubs if fixture_key(x.get("name")) == key or any(fixture_key(a) == key for a in x.get("aliases", []))]
    return exact[0] if exact else None


def fixture_split_teams(row):
    label = str(row.get("name", ""))
    parts = re.split(r"\s+vs\.?\s+", label, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return parts
    return ("Home team", "Away team")


def fixture_abbrev(name, club=None):
    if club and club.get("abbr"):
        return str(club["abbr"]).upper()[:8]
    words = re.findall(r"[A-Za-z0-9]+", str(name))
    words = [w for w in words if w.lower() not in ("xi", "1st", "2nd", "3rd", "4th", "cc", "cricket", "club")]
    return ("".join(w[0] for w in words[:4]) or "TEAM").upper()


def fixture_font(size, bold=False):
    return broadcast_font(size, bold)


def fixture_text_fit(draw, text, max_width, max_size, min_size=18, bold=False):
    for size in range(max_size, min_size - 1, -2):
        font = fixture_font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return fixture_font(min_size, bold)


def fixture_center(draw, x, y, text, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((int(x - (box[2] - box[0]) / 2), int(y)), text, font=font, fill=fill)


def fixture_logo_paste(image, entry, center, size=140, clip_box=None):
    """Fill the logo panel vertically, preserving aspect ratio; crop horizontal overflow."""
    if not entry:
        return
    raw = fixture_logo_bytes(entry)
    if not raw:
        return
    try:
        logo = Image.open(io.BytesIO(raw)).convert("RGBA")
        if clip_box is None:
            factor = min(float(size) / logo.width, float(size) / logo.height)
            dimensions = (max(1, round(logo.width * factor)), max(1, round(logo.height * factor)))
            logo = logo.resize(dimensions, Image.Resampling.LANCZOS)
            left = round(center[0] - logo.width / 2)
            upper = round(center[1] - logo.height / 2)
            image.paste(logo, (left, upper), logo)
            return

        x0, y0, x1, y1 = map(int, clip_box)
        panel_w, panel_h = max(1, x1 - x0), max(1, y1 - y0)
        # Fill the entire panel height, even when this makes the artwork wider
        # than the side panel. Horizontal excess is clipped, never squeezed.
        factor = panel_h / logo.height
        scaled_w = max(1, round(logo.width * factor))
        logo = logo.resize((scaled_w, panel_h), Image.Resampling.LANCZOS)
        panel = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
        panel.alpha_composite(logo, (round((panel_w - scaled_w) / 2), 0))
        image.paste(panel, (x0, y0), panel)
    except (OSError, ValueError, ZeroDivisionError):
        return


def fixture_pretty_dates(values):
    """Compact, readable fixture dates for one- and two-day matches."""
    from datetime import datetime, date
    parsed = []
    for value in values:
        if not value:
            continue
        if isinstance(value, (date, datetime)):
            day = value.date() if isinstance(value, datetime) else value
        else:
            raw = str(value).strip()
            day = None
            for candidate in (raw, raw[:10]):
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%a %d %b %Y", "%A %d %B %Y"):
                    try:
                        day = datetime.strptime(candidate, fmt).date()
                        break
                    except ValueError:
                        continue
                if day:
                    break
            if day is None:
                parsed.append(raw.upper())
                continue
        parsed.append(day.strftime("%a").upper() + " " + str(day.day) + " " + day.strftime("%b").upper())
    return " & ".join(dict.fromkeys(parsed)) or "DATE TBC"


def fixture_wrapped_lines(draw, text, max_width, font, max_lines=2):
    """Wrap venue text without allowing it to leave the centre panel."""
    words = str(text).split()
    if not words:
        return ["VENUE TBC"]
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if current and draw.textbbox((0, 0), trial, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines[:max_lines]


def fixture_equal_team_names(draw, home_name, away_name, width, max_size=27, min_size=12):
    """Fit two full club names to equal-sized, centred two-line text blocks."""
    for font_size in range(max_size, min_size - 1, -1):
        font = fixture_font(font_size, True)
        lines = [fixture_wrapped_lines(draw, str(name), width, font, max_lines=99)
                 for name in (home_name, away_name)]
        if all(len(group) <= 2 and all(
            draw.textbbox((0, 0), line, font=font)[2] <= width for line in group
        ) for group in lines):
            return font, lines
    font = fixture_font(min_size, True)
    lines = [fixture_wrapped_lines(draw, str(name), width, font, max_lines=2)
             for name in (home_name, away_name)]
    return font, lines


def fixture_graphic(rows, settings):
    """NBA-inspired panels with bounded artwork and a dedicated text column."""
    w, h = FIXTURE_SIZE
    background = Image.new("RGB", (w, h), "#101b33")
    d = ImageDraw.Draw(background)
    for yy in range(h):
        t = yy / h
        a, b = (165, 12, 35), (14, 25, 47)
        d.line((0, yy, w, yy), fill=tuple(int(a[k]*(1-t)+b[k]*t) for k in range(3)))
    custom_bg = st.session_state.get("fixture_background_bytes")
    if custom_bg:
        try:
            background = Image.open(io.BytesIO(custom_bg)).convert("RGB").resize((w,h), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            pass
    elif settings.get("background") == "HBCC existing template" and DEFAULT_TEMPLATE.exists():
        background = Image.open(DEFAULT_TEMPLATE).convert("RGB").resize((w,h), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(background)
    yellow = settings.get("yellow", "#f5b82e")
    if not custom_bg:
        d.line((55,45,112,45), fill=yellow,width=6)
    if not custom_bg:
        d.line((55,45,55,100), fill=yellow,width=6)
    if not custom_bg:
        d.line((w-112,45,w-55,45),fill=yellow,width=6)
    if not custom_bg:
        d.line((w-55,45,w-55,100),fill=yellow,width=6)
    # Heading is part of the uploaded background; do not draw a second title.
    n=len(rows)
    # Reserve the lower ~300px for the club crest and sponsor artwork already
    # embedded in the selected 1080x1350 fixture background template.
    # Cap old saved layout settings too, so existing Supabase templates benefit
    # immediately without requiring an admin to re-save every template.
    top=min(int(settings.get("cards_top", 240)), 240)
    bottom=min(int(settings.get("cards_bottom", 1045)), 1045)
    # Use a fixed three-slot layout on every page, including pages with 1 or 2 fixtures.
    # This prevents the final page from stretching cards and logos vertically.
    gap=24; card_h=max(185,(bottom-top-gap*2)//3)
    for i,row in enumerate(rows):
        y=top+i*(card_h+gap)
        home=row.get("home_name") or fixture_split_teams(row)[0]
        away=row.get("away_name") or fixture_split_teams(row)[1]
        hc,_=fixture_resolve_club(home, row.get("home_ids"), row.get("home_club_override"))
        ac,_=fixture_resolve_club(away, row.get("away_ids"), row.get("away_club_override"))
        d.rounded_rectangle((68,y,1012,y+card_h),radius=24,fill="#0c1b37",outline=yellow,width=3)
        # Crop logos within their own side zones; leave the central details clear.
        size=int(card_h*float(settings.get("logo_scale",1.65)))
        bleed=int(settings.get("logo_bleed",55))
        left_zone=(71,y+4,305,y+card_h-4)
        right_zone=(775,y+4,1009,y+card_h-4)
        # Centre the visual in each side panel, rather than pinning it to the inner edge.
        fixture_logo_paste(background,hc,((left_zone[0]+left_zone[2])/2,y+card_h/2),size,left_zone)
        fixture_logo_paste(background,ac,((right_zone[0]+right_zone[2])/2,y+card_h/2),size,right_zone)
        # Match Results-style soft fade: the inner edge of each logo panel
        # gradually blends into the fixture card's navy central background.
        # Keep the outside of each logo visible and the text panel untouched.
        fade_width = 82
        navy = (12, 27, 55)
        for edge, direction in ((left_zone[2], 1), (right_zone[0], -1)):
            for step in range(fade_width):
                progress = step / max(1, fade_width - 1)
                x = edge - fade_width + step if direction == 1 else edge + step
                opacity = progress if direction == 1 else 1.0 - progress
                opacity = opacity * opacity * (3 - 2 * opacity)
                overlay = Image.new("RGB", (1, card_h - 8), navy)
                mask = Image.new("L", overlay.size, round(opacity * 255))
                background.paste(overlay, (int(x), y + 4), mask)
        d=ImageDraw.Draw(background)
        # Semi-opaque centre provides guaranteed legibility, even with vivid club art.
        d.rectangle((305,y+5,775,y+card_h-5),fill="#0c1b37")
        # Redesigned ESPN-style centre information panel. All text stays within x=305..775.
        centre_x = 540
        panel_left, panel_right = 305, 775
        panel_width = panel_right - panel_left
        pad = 18
        usable = panel_width - pad * 2
        # Grade strip is intentionally subtle and consistent on all three cards.
        strip_top = y + 9
        strip_bottom = y + 51
        draw_faded_navy_band(d, panel_left + 2, strip_top, panel_right - 2, strip_bottom, highlight="#243b60")
        grade = str(row.get("grade") or "MATCH").upper().strip()
        grade_font = fixture_text_fit(d, grade, usable - 18, 21, 13, True)
        grade_bbox = d.textbbox((0, 0), grade, font=grade_font)
        grade_height = grade_bbox[3] - grade_bbox[1]
        grade_y = strip_top + ((strip_bottom - strip_top) - grade_height) / 2 - grade_bbox[1]
        fixture_center(d, centre_x, grade_y, grade, grade_font, yellow)

        # Full club names replace abbreviations in both graphic modes.
        # Fit both names using one shared font size, centred in equal-width
        # columns, and allow up to two lines without overlapping VS.
        left_x, right_x = 421, 659
        home_label = str((hc or {}).get("name") or home).strip()
        away_label = str((ac or {}).get("name") or away).strip()
        name_width = 196
        label_font, label_lines = fixture_equal_team_names(
            d, home_label, away_label, name_width, max_size=27, min_size=12
        )
        name_line_height = label_font.size + 5
        name_top = y + 75
        for name_x, lines in zip((left_x, right_x), label_lines):
            block_height = len(lines) * name_line_height
            first_y = name_top + (2 * name_line_height - block_height) / 2
            for line_no, line in enumerate(lines):
                fixture_center(d, name_x, first_y + line_no * name_line_height,
                               line, label_font, "#ffffff")
        fixture_center(d, centre_x, name_top + 13, "VS", fixture_font(20, True), yellow)

        dates = row.get("playing_dates") or [row.get("date", "")]
        date_str = fixture_pretty_dates(dates)
        date_y = y + 159  # Preserve spacing below two-line club names
        date_font = fixture_text_fit(d, date_str, usable, 26, 15, True)
        fixture_center(d, centre_x, date_y, date_str, date_font, yellow)

        venue = str(row.get("venue") or "Venue TBC").strip()
        # Larger venue typography (25px vs 20px) and an increased date/venue gap.
        # Fit dynamically inside the remaining card space, preserving all words.
        date_bbox = d.textbbox((0, 0), date_str, font=date_font)
        date_bottom = date_y + date_bbox[3]
        venue_area_top = max(y + 192, date_bottom + 13)
        venue_area_bottom = y + card_h - 12
        available_h = max(16, venue_area_bottom - venue_area_top)
        venue_font = fixture_font(25, False)
        venue_lines = [venue]
        for size_pt in range(25, 11, -1):
            trial_font = fixture_font(size_pt, False)
            trial_lines = fixture_wrapped_lines(d, venue, usable, trial_font, max_lines=99)
            line_height = size_pt + 5
            if (len(trial_lines) <= 2 and
                    all(d.textbbox((0, 0), line, font=trial_font)[2] <= usable for line in trial_lines) and
                    len(trial_lines) * line_height <= available_h):
                venue_font, venue_lines = trial_font, trial_lines
                break
        else:
            # Very long names: retain the complete text, at a smaller font if needed.
            venue_font = fixture_font(12, False)
            venue_lines = fixture_wrapped_lines(d, venue, usable, venue_font, max_lines=99)
        line_height = getattr(venue_font, "size", 12) + 5
        for line_no, line in enumerate(venue_lines):
            fixture_center(d, centre_x, venue_area_top + line_no * line_height,
                           line, venue_font, "#ffffff")
        # Draw the outline last, above the artwork and label strips. This prevents
        # the square cropped visuals from covering the yellow left/right borders.
        d.rounded_rectangle((68,y,1012,y+card_h),radius=24,outline=yellow,width=4)
    if not custom_bg:
        fixture_center(d,w//2,1250,"HAWTHORN BOROONDARA CRICKET CLUB",fixture_font(24,True),yellow)
    return background


def fixture_asset_export():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("clubs.json", json.dumps(fixture_library(), indent=2))
        for club in fixture_library():
            raw = fixture_logo_bytes(club)
            if raw:
                filename = Path(str(club.get("file") or (fixture_key(club.get("name")) + ".png"))).name
                z.writestr(filename, raw)
    return out.getvalue()


def fixture_admin():
    """Single-save, Supabase-backed fixture template manager."""
    defaults = {"title": "UPCOMING MATCHES", "title_size": 90,
                "cards_top": 240, "cards_bottom": 1045, "yellow": "#f5b82e",
                "background": "Red to navy gradient", "logo_scale": 1.65,
                "logo_bleed": 55}
    st.markdown("#### Fixture templates")
    st.caption("Choose a template, change its name or background, then click Save changes. All changes are stored in Supabase.")
    if not sb_ready():
        st.error("Supabase is not configured. Add SUPABASE_URL and SUPABASE_SECRET_KEY to Streamlit Secrets.")
        return

    try:
        # Refresh the list when the admin editor is opened; no local-only template copies.
        templates = sorted((t for t in sb_rows("hbcc_templates")
                            if t.get("template_type") == "fixture"),
                           key=lambda t: (t.get("name") or "").lower())
        st.session_state.sb_templates = [t for t in st.session_state.get("sb_templates", [])
                                         if t.get("template_type") != "fixture"] + templates
    except Exception as exc:
        st.error(f"Could not load templates: {exc}")
        return

    by_id = {t["id"]: t for t in templates}
    options = list(by_id) + ["__new__"]
    pending = st.session_state.pop("fixture_editor_pending_selection", None)
    current = pending or st.session_state.get("fixture_editor_selection", "fixture_default")
    if current not in options:
        current = options[0] if templates else "__new__"
    st.session_state.fixture_editor_selection = current
    selection = st.selectbox(
        "Choose template", options, key="fixture_editor_selection",
        format_func=lambda key: "+ Create new template" if key == "__new__"
        else (by_id[key].get("name") or key) + (" (Default)" if key == "fixture_default" else ""),
    )
    record = by_id.get(selection, {})
    # Inputs are bound to the chosen template's ID, so switching templates never
    # reuses a stale name, uploader or slider value from another template.
    prefix = "fixture_editor_" + selection
    initial_settings = {**defaults, **(record.get("settings") or {})}
    image_path = record.get("background_path")

    with st.form("fixture_template_edit_form_" + selection, clear_on_submit=False):
        name = st.text_input("Template name", value=record.get("name", "New fixture template"),
                             key=prefix + "_name")
        st.markdown("**Background image**")
        if image_path:
            try:
                preview = sb_download(image_path)
                if preview:
                    st.image(preview, width=190, caption="Current saved background")
            except Exception as exc:
                st.warning(f"Could not preview background: {exc}")
        else:
            st.caption("No uploaded background — the built-in graphic background will be used.")
        replace = st.checkbox("Replace background image", key=prefix + "_replace")
        uploaded = st.file_uploader("New background (PNG or JPG, ideally 1080 × 1350)",
                                    type=["png", "jpg", "jpeg"],
                                    key=prefix + "_upload")
        st.markdown("**Match card layout**")
        st.caption("Your background supplies the heading. These controls position the match cards and logos only.")
        with st.expander("Adjust match cards", expanded=False):
            cards_top = st.slider("First card position", 195, 240, min(240, int(initial_settings["cards_top"])),
                                  key=prefix + "_cards_top")
            cards_bottom = st.slider("Match area bottom", 950, 1045,
                                     min(1045, int(initial_settings["cards_bottom"])), key=prefix + "_cards_bottom")
            logo_scale = st.slider("Logo scale", 1.0, 3.2, float(initial_settings["logo_scale"]),
                                   0.05, key=prefix + "_logo_scale")
        saved = st.form_submit_button("Save changes", type="primary", use_container_width=True)

    if saved:
        clean_name = name.strip()
        if not clean_name:
            st.error("Enter a template name before saving.")
            return
        if replace and uploaded is None:
            st.error("Choose an image to replace the current background, or untick Replace background image.")
            return
        if selection == "__new__":
            # Use a stable generated ID: renaming never changes this ID.
            template_id = "fixture_" + uuid.uuid4().hex[:16]
        else:
            template_id = selection
        settings = dict(initial_settings)
        settings.update({"cards_top": cards_top, "cards_bottom": cards_bottom,
                         "logo_scale": logo_scale})
        path = image_path
        try:
            if replace and uploaded is not None:
                # One predictable storage object per template, overwritten on replacement.
                path = "fixture-templates/" + template_id + ".png"
                sb_upload(path, sb_image(uploaded.getvalue()))
            sb_upsert("hbcc_templates", {
                "id": template_id, "name": clean_name, "template_type": "fixture",
                "background_path": path, "settings": settings,
            })
            # The saved template immediately becomes the active design for graphics.
            st.session_state.fixture_graphic_settings = settings
            if path:
                st.session_state.fixture_background_bytes = sb_download(path)
            else:
                st.session_state.pop("fixture_background_bytes", None)
            sb_upsert("hbcc_settings", {"key": "active_fixture_template", "value": {"id": template_id}})
            st.session_state.fixture_active_template_id = template_id
            st.session_state.fixture_editor_pending_selection = template_id
            st.session_state.pop(prefix + "_replace", None)
            st.success("Template saved to Supabase. It is now active for Upcoming Fixtures.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save template: {exc}")

    if selection != "__new__":
        # Destructive actions live outside the edit form and require confirmation.
        with st.expander("Delete this template", expanded=False):
            if len(templates) <= 1:
                st.info("Keep at least one fixture template. Create another template before deleting this one.")
            else:
                st.warning(f"This permanently deletes the template ‘{record.get('name') or selection}’. It cannot be undone.")
                confirm_key = prefix + "_confirm_delete"
                confirmed = st.checkbox("I understand this template will be deleted", key=confirm_key)
                if st.button("Delete template", type="secondary", disabled=not confirmed,
                             key=prefix + "_delete", use_container_width=True):
                    try:
                        # Recheck live records to avoid deleting the final template.
                        live = [t for t in sb_rows("hbcc_templates")
                                if t.get("template_type") == "fixture"]
                        selected_live = next((t for t in live if t.get("id") == selection), None)
                        survivors = [t for t in live if t.get("id") != selection]
                        if selected_live is None:
                            st.error("This template was already deleted. Refresh the page.")
                            st.stop()
                        if not survivors:
                            st.error("Cannot delete the last remaining fixture template.")
                            st.stop()
                        active_id = st.session_state.get("fixture_active_template_id", "fixture_default")
                        if active_id == selection:
                            fallback = next((t for t in survivors if t.get("id") == "fixture_default"), survivors[0])
                            # Switch the active template before removing the current one.
                            sb_upsert("hbcc_settings", {"key": "active_fixture_template",
                                                            "value": {"id": fallback["id"]}})
                        else:
                            fallback = next((t for t in survivors if t.get("id") == active_id), survivors[0])
                        sb_delete("hbcc_templates", "id", selection)
                        # Only delete a background owned by this template, if no
                        # remaining template points to the same object. Historical
                        # random-name files are left intact for safe manual cleanup.
                        old_path = selected_live.get("background_path") or ""
                        owned_path = "fixture-templates/" + selection + ".png"
                        warning = None
                        if old_path == owned_path and not any(
                                t.get("background_path") == old_path for t in survivors):
                            try:
                                sb_request("DELETE", "/storage/v1/object/hbcc-assets/" +
                                           quote(old_path, safe="/"))
                            except Exception as image_exc:
                                warning = str(image_exc)
                        st.session_state.fixture_active_template_id = fallback["id"]
                        st.session_state.fixture_graphic_settings = {
                            **defaults, **(fallback.get("settings") or {})}
                        if fallback.get("background_path"):
                            try:
                                st.session_state.fixture_background_bytes = sb_download(fallback["background_path"])
                            except Exception:
                                st.session_state.pop("fixture_background_bytes", None)
                        else:
                            st.session_state.pop("fixture_background_bytes", None)
                        st.session_state.fixture_editor_pending_selection = fallback["id"]
                        sb_refresh_templates()
                        if warning:
                            st.warning("Template deleted, but its unused background could not be removed: " + warning)
                        else:
                            st.success("Template deleted. Another saved template is now selected.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete template: {exc}")

    if selection != "__new__":
        active = st.session_state.get("fixture_active_template_id", "fixture_default")
        if selection == active:
            st.caption("This template is currently used for Upcoming Fixtures.")
        else:
            st.caption("Click Save changes to make this the active Upcoming Fixtures template.")
    st.caption("No ZIP exports or extra Save buttons are required. Existing stored images are never deleted automatically.")


def fixture_import_club_zip(uploaded_zip):
    """One-time, repeat-safe migration from GitHub club_visuals ZIP to Supabase.

    Import only when explicitly requested. Keep existing Supabase clubs/logos intact.
    """
    import zipfile
    import hashlib
    if not sb_ready():
        raise RuntimeError("Connect Supabase in Streamlit Secrets before importing.")
    payload = uploaded_zip.getvalue()
    if len(payload) > 100 * 1024 * 1024:
        raise ValueError("The ZIP exceeds the 100 MB migration limit.")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest_files = [x for x in archive.namelist() if Path(x).name == "clubs.json" and not x.startswith("__MACOSX/")]
        if len(manifest_files) != 1:
            raise ValueError("ZIP must contain exactly one clubs.json file.")
        manifest_path = manifest_files[0]
        prefix = manifest_path[:-len("clubs.json")]
        records = json.loads(archive.read(manifest_path))
        if not isinstance(records, list):
            raise ValueError("clubs.json must contain a list of clubs.")
        existing = sb_rows("hbcc_clubs")
        by_name = {fixture_key(r.get("name")): r for r in existing}
        by_id = {str(r.get("id")): r for r in existing}
        imported, skipped, errors = 0, 0, []
        for record in records:
            try:
                if not isinstance(record, dict) or not str(record.get("name", "")).strip():
                    raise ValueError("Missing club name")
                name = str(record["name"]).strip()
                normalized = fixture_key(name)
                if not normalized:
                    raise ValueError("Invalid club name")
                stable_id = "club_" + hashlib.sha256(normalized.encode()).hexdigest()[:20]
                previous = by_name.get(normalized) or by_id.get(stable_id)
                if previous:
                    # Preserve edits and logos already made in Supabase.
                    skipped += 1
                    continue
                filename = Path(str(record.get("file") or "")).name
                if not filename:
                    raise ValueError("Missing logo filename")
                matches = [prefix + filename, filename]
                match = next((x for x in matches if x in archive.namelist()), None)
                if match is None:
                    raise ValueError("Logo not found in ZIP: " + filename)
                if archive.getinfo(match).file_size > 12 * 1024 * 1024:
                    raise ValueError("Logo exceeds 12 MB")
                raw = archive.read(match)
                normalized_png = sb_image(raw)
                path = "club-logos/" + stable_id + ".png"
                sb_upload(path, normalized_png)
                cloud = {"id": stable_id, "name": name,
                         "abbreviation": str(record.get("abbr") or ""),
                         "aliases": record.get("aliases") if isinstance(record.get("aliases"), list) else [],
                         "logo_path": path}
                sb_upsert("hbcc_clubs", cloud)
                by_name[normalized] = cloud
                by_id[stable_id] = cloud
                imported += 1
            except Exception as exc:
                errors.append(f"{record.get('name', 'Unnamed') if isinstance(record, dict) else 'Invalid club'}: {exc}")
    # Refresh local catalog so the newly migrated Supabase logos are used now.
    cloud_clubs = sb_rows("hbcc_clubs")
    local = fixture_library()
    for record in cloud_clubs:
        cloud = {"id": record["id"], "name": record["name"],
                 "abbr": record.get("abbreviation") or "",
                 "aliases": record.get("aliases") or [],
                 "logo_path": record.get("logo_path")}
        idx = next((i for i, c in enumerate(local)
                    if c.get("id") == cloud["id"] or fixture_key(c.get("name")) == fixture_key(cloud["name"])), None)
        if idx is None:
            local.append(cloud)
        else:
            local[idx].update(cloud)
    st.session_state.sb_logo_cache = {}
    return imported, skipped, errors


def fixture_club_mapping_admin(club):
    """Manage PlayCricket identifiers linked to the selected Supabase club."""
    st.markdown("##### Connected PlayCricket IDs")
    st.caption("One club can have multiple team IDs. Club IDs cover multiple grades when PlayCricket exposes them. These links choose the club's single saved logo.")
    if not sb_ready():
        st.info("Connect Supabase to manage saved mappings.")
        return
    if not club.get("id"):
        st.info("Save this club to Supabase before linking team IDs.")
        return
    try:
        mappings = sb_rows("hbcc_club_mappings")
    except Exception as exc:
        st.error("Could not load mappings: " + str(exc))
        return
    own = sorted((r for r in mappings if str(r.get("club_id")) == str(club["id"])), key=lambda r: str(r.get("source_id", "")))
    if own:
        for record in own:
            source = str(record.get("source_id", ""))
            kind = str(record.get("source_type") or source.split(":", 1)[0]).title()
            source_name = record.get("source_name") or "No team name recorded"
            left, right = st.columns([5, 1])
            left.markdown(f"**{kind}** · `{source}`  \n{source_name}")
            if right.button("Remove", key="club_mapping_remove_" + str(club["id"]) + "_" + source):
                try:
                    sb_delete("hbcc_club_mappings", "source_id", source)
                    st.session_state.pop("fixture_id_mappings", None)
                    st.success("Mapping removed")
                    st.rerun()
                except Exception as exc:
                    st.error("Could not remove mapping: " + str(exc))
    else:
        st.caption("No IDs connected to this club yet. You can add one below or save a correction from Upcoming Fixtures.")
    with st.form("club_mapping_add_" + str(club["id"])):
        id_type = st.selectbox("Identifier type", ["team", "club"], key="mapping_type_" + str(club["id"]))
        raw_id = st.text_input("PlayCricket ID", placeholder="Paste the ID only, without team: or club:")
        source_name = st.text_input("Team / source name (optional)")
        if st.form_submit_button("Connect ID to this club"):
            clean_id = raw_id.strip()
            if clean_id.lower().startswith(("team:", "club:")):
                prefix, clean_id = clean_id.split(":", 1)
                id_type = prefix.lower()
            if not clean_id:
                st.error("Enter an ID first.")
            else:
                source_id = id_type + ":" + clean_id
                other = next((r for r in mappings if str(r.get("source_id")) == source_id and str(r.get("club_id")) != str(club["id"])), None)
                if other:
                    st.error("This ID is already linked to a different club. Remove its existing mapping before reassigning it.")
                else:
                    try:
                        sb_upsert("hbcc_club_mappings", {"source_id": source_id, "source_type": id_type, "source_name": source_name.strip(), "club_id": str(club["id"])})
                        st.session_state.pop("fixture_id_mappings", None)
                        st.success("ID linked to this club")
                        st.rerun()
                    except Exception as exc:
                        st.error("Could not save mapping: " + str(exc) + ". Check that the SQL migration has been run.")


def fixture_club_admin():
    st.markdown("#### Club visuals and aliases")
    st.caption("Edit existing clubs without uploading images again, or add a new club with a visual.")
    with st.expander("Migrate GitHub club logos to Supabase (one-time)", expanded=False):
        st.caption("Upload the club_visuals ZIP containing clubs.json and logo images. The importer uploads logos to the private hbcc-assets bucket and creates hbcc_clubs records. Existing Supabase club records are never overwritten. Safe to retry after a partial import.")
        migration_zip = st.file_uploader("Club visuals ZIP", type=["zip"], key="fixture_club_migration_zip")
        migration_confirm = st.checkbox("Import clubs and logos into Supabase", key="fixture_club_migration_confirm")
        if st.button("Import to Supabase", key="fixture_club_migration_run", type="primary", disabled=not (migration_zip and migration_confirm)):
            try:
                with st.spinner("Importing club logos to Supabase..."):
                    imported, skipped, errors = fixture_import_club_zip(migration_zip)
                st.success(f"Migration complete: {imported} clubs imported, {skipped} already in Supabase.")
                if errors:
                    st.warning(f"{len(errors)} clubs could not be imported. You can retry safely.")
                    st.code("\n".join(errors))
                st.caption("Check Supabase Table Editor → hbcc_clubs and Storage → hbcc-assets → club-logos. The existing GitHub logos remain as a fallback.")
            except Exception as exc:
                st.error(f"Import failed: {exc}")
    clubs=fixture_library()
    if clubs:
        names=[f"{c.get('name','Unnamed')} · {c.get('abbr','')}" for c in clubs]
        selected=st.selectbox("Select a club to edit", range(len(clubs)),format_func=lambda i:names[i],key="club_admin_selected")
        club=clubs[selected]
        with st.form("edit_existing_club"):
            name=st.text_input("Full club name",value=club.get("name", ""))
            abbr=st.text_input("Abbreviation",value=club.get("abbr", ""))
            aliases=st.text_area("PlayCricket aliases (one per line)",value="\n".join(club.get("aliases", [])),height=110)
            logo=st.file_uploader("Replace logo (optional)",type=["png","jpg","jpeg","webp"])
            save=st.form_submit_button("Save club details",type="primary")
            if save:
                if not name.strip(): st.error("Club name is required")
                else:
                    club.update({"name":name.strip(),"abbr":abbr.strip(),"aliases":[a.strip() for a in aliases.splitlines() if a.strip()]})
                    if logo:
                        try:
                            im=Image.open(io.BytesIO(logo.getvalue())).convert("RGBA")
                            buf=io.BytesIO();im.save(buf,format="PNG")
                            filename=re.sub(r"[^a-z0-9]+","_",name.casefold()).strip("_")+".png"
                            st.session_state.fixture_uploaded_logos[filename]=buf.getvalue()
                            club["file"]=filename
                        except (OSError, ValueError): st.error("Invalid logo")
                    if sb_ready():
                        try:
                            image_data = st.session_state.fixture_uploaded_logos.get(club.get("file")) if logo else None
                            sb_save_club(club, image_data)
                            st.session_state.sb_logo_cache = {}
                            st.success("Club details saved permanently to Supabase")
                        except Exception as exc:
                            st.error(f"Cloud save failed: {exc}")
                    else:
                        st.warning("Saved only for this session: Supabase not connected")
        fixture_club_mapping_admin(club)
        raw=fixture_logo_bytes(club)
        if raw: st.image(raw,width=140)
        if st.button("Delete this club",key="delete_club_global"):
            if club.get("id") and sb_ready():
                try:
                    sb_delete("hbcc_clubs", "id", club["id"])
                except Exception as exc:
                    st.error(f"Could not delete from Supabase: {exc}")
                    st.stop()
            clubs.pop(selected)
            st.warning("GitHub-supplied clubs will return after a restart unless removed from clubs.json.")
            st.rerun()
    with st.expander("Add a new club",expanded=False):
        with st.form("add_new_fixture_club",clear_on_submit=True):
            name=st.text_input("New club name")
            abbr=st.text_input("New club abbreviation")
            aliases=st.text_area("Aliases (one per line)")
            logo=st.file_uploader("Club image",type=["png","jpg","jpeg","webp"])
            if st.form_submit_button("Add club",type="primary"):
                if not name.strip() or logo is None: st.error("Provide a club name and image")
                else:
                    try:
                        im=Image.open(io.BytesIO(logo.getvalue())).convert("RGBA")
                        buf=io.BytesIO();im.save(buf,format="PNG")
                        filename=re.sub(r"[^a-z0-9]+","_",name.casefold()).strip("_")+".png"
                        st.session_state.fixture_uploaded_logos[filename]=buf.getvalue()
                        clubs.append({"name":name.strip(),"abbr":abbr.strip(),"aliases":[a.strip() for a in aliases.splitlines() if a.strip()],"file":filename})
                        if sb_ready():
                            try:
                                sb_save_club(clubs[-1], buf.getvalue())
                                st.success("New club saved permanently to Supabase")
                            except Exception as exc:
                                st.error(f"Cloud save failed: {exc}")
                        else:
                            st.warning("Added only for this session: Supabase not connected")
                    except (OSError, ValueError): st.error("Invalid image")
    st.download_button("Export complete club library (ZIP)",fixture_asset_export(),"club_visuals.zip","application/zip",type="primary",use_container_width=True)
    st.caption("Cloud changes save permanently when Supabase is connected. ZIP export is an optional backup. Existing GitHub club visuals remain available.")


# Load persistent assets before the publishing UI renders.
sb_load_once()


def global_admin_panel():
    st.markdown("### Admin mode")
    secret=str(st.secrets.get("TEMPLATE_ADMIN_PASSWORD", "HBCC1947"))
    if not secret:
        st.warning("Add TEMPLATE_ADMIN_PASSWORD to Streamlit Secrets to enable admin mode.")
        return
    if not st.session_state.get("global_admin_unlocked",False):
        with st.form("global_admin_login"):
            pwd=st.text_input("Admin password",type="password")
            if st.form_submit_button("Log in",type="primary"):
                if hmac.compare_digest(pwd,secret):
                    st.session_state.global_admin_unlocked=True
                    st.rerun()
                else: st.error("Incorrect password")
        return
    c1,c2=st.columns([5,1])
    c1.success("Admin mode active across Content Studio")
    if c2.button("Log out"):
        st.session_state.global_admin_unlocked=False
        st.rerun()
    if st.session_state.get("sb_error"):
        st.error("Supabase connection failed: " + st.session_state.sb_error)
        if st.button("Retry Supabase connection"):
            st.session_state.pop("sb_error", None)
            st.session_state.pop("sb_loaded", None)
            st.rerun()
    elif sb_ready():
        st.caption("Supabase connected · saved assets persist across restarts")
    tab_fixture,tab_team,tab_results,tab_clubs=st.tabs(["Fixture templates","Team selection templates","Match results templates","Club visuals & aliases"])
    with tab_fixture: fixture_admin()
    with tab_team:
        team_template_admin()
    with tab_results: results_template_admin()
    with tab_clubs: fixture_club_admin()


def team_template_admin():
    """Supabase-backed team selection templates: select, rename, replace, save, delete."""
    st.markdown("#### Team selection templates")
    st.caption("Choose a template, rename it or replace its background, then Save changes. Player information is added automatically.")
    if not sb_ready():
        st.error("Supabase is not configured. Check Streamlit Secrets.")
        return
    try:
        templates = sorted((t for t in sb_rows("hbcc_templates")
                            if t.get("template_type") == "team_selection"),
                           key=lambda t: (t.get("name") or "").lower())
        st.session_state.sb_templates = [t for t in st.session_state.get("sb_templates", [])
                                         if t.get("template_type") != "team_selection"] + templates
    except Exception as exc:
        st.error(f"Could not load team templates: {exc}")
        return
    by_id = {t["id"]: t for t in templates}
    options = list(by_id) + ["__new__"]
    pending = st.session_state.pop("team_editor_pending_selection", None)
    current = pending or st.session_state.get("team_editor_selection", "team_default")
    if current not in options:
        current = options[0] if templates else "__new__"
    st.session_state.team_editor_selection = current
    selection = st.selectbox("Choose template", options, key="team_editor_selection",
                             format_func=lambda k: "+ Create new template" if k == "__new__"
                             else (by_id[k].get("name") or k))
    record = by_id.get(selection, {})
    prefix = "team_editor_" + selection
    initial_settings = {**DEFAULT_GRAPHIC_SETTINGS, **(record.get("settings") or {})}
    image_path = record.get("background_path")
    with st.form("team_template_edit_form_" + selection):
        name = st.text_input("Template name", value=record.get("name", "New team selection template"),
                             key=prefix + "_name")
        st.markdown("**Background image**")
        if image_path:
            try:
                preview = sb_download(image_path)
                if preview:
                    st.image(preview, width=190, caption="Current saved background")
            except Exception as exc:
                st.warning(f"Could not preview background: {exc}")
        else:
            st.caption("No saved background. Upload one to create this template.")
        replace = st.checkbox("Upload or replace background image", key=prefix + "_replace",
                              value=selection == "__new__")
        uploaded = st.file_uploader("Background (PNG or JPG, ideally 1080 × 1350)",
                                    type=["png", "jpg", "jpeg"],
                                    key=prefix + "_upload")
        with st.expander("Adjust player text layout", expanded=False):
            left = st.number_input("Left margin", 10, 250, int(initial_settings["left"]), key=prefix+"_left")
            top = st.number_input("Top margin", 10, 250, int(initial_settings["top"]), key=prefix+"_top")
            grade_size = st.number_input("Grade size", 10, 150, int(initial_settings["title_size"]), key=prefix+"_grade")
            details_size = st.number_input("Fixture details size", 10, 150, int(initial_settings["details_size"]), key=prefix+"_details")
            players_size = st.number_input("Player names size", 10, 150, int(initial_settings["players_size"]), key=prefix+"_players")
            spacing = st.number_input("Player line spacing", 1.0, 2.5, float(initial_settings["line_spacing"]), 0.05, key=prefix+"_spacing")
        saved = st.form_submit_button("Save changes", type="primary", use_container_width=True)
    if saved:
        clean_name = name.strip()
        if not clean_name:
            st.error("Enter a template name.")
            return
        if replace and uploaded is None:
            st.error("Choose a background image or untick the replacement option.")
            return
        template_id = ("team_" + uuid.uuid4().hex[:16]) if selection == "__new__" else selection
        settings = dict(initial_settings)
        settings.update({"left":left, "top":top, "title_size":grade_size,
                         "details_size":details_size, "players_size":players_size,
                         "line_spacing":spacing})
        path = image_path
        try:
            if replace and uploaded is not None:
                path = "team-selection-templates/" + template_id + ".png"
                sb_upload(path, sb_image(uploaded.getvalue()))
            sb_upsert("hbcc_templates", {"id":template_id, "name":clean_name,
                      "template_type":"team_selection", "background_path":path,
                      "settings":settings})
            sb_upsert("hbcc_settings", {"key":"active_team_template", "value":{"id":template_id}})
            st.session_state.team_active_template_id = template_id
            st.session_state.team_editor_pending_selection = template_id
            st.session_state.graphic_settings = settings
            st.session_state.graphic_settings_version = 3
            if path:
                st.session_state.graphic_template_bytes = sb_download(path)
            else:
                st.session_state.pop("graphic_template_bytes", None)
            sb_refresh_templates()
            st.success("Team template saved to Supabase and activated.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save team template: {exc}")
    if selection != "__new__":
        with st.expander("Delete this template", expanded=False):
            if len(templates) <= 1:
                st.info("Create another team template before deleting the last one.")
            else:
                st.warning("Deleting this template cannot be undone.")
                confirmed = st.checkbox("I understand this template will be deleted", key=prefix+"_confirm")
                if st.button("Delete template", disabled=not confirmed,
                             key=prefix+"_delete", use_container_width=True):
                    try:
                        live = [t for t in sb_rows("hbcc_templates") if t.get("template_type") == "team_selection"]
                        survivors = [t for t in live if t.get("id") != selection]
                        if not survivors or not any(t.get("id") == selection for t in live):
                            st.error("Cannot delete the last team template, or the template no longer exists.")
                            return
                        active_id = st.session_state.get("team_active_template_id", "team_default")
                        fallback = next((t for t in survivors if t["id"] == active_id), survivors[0])
                        sb_upsert("hbcc_settings", {"key":"active_team_template", "value":{"id":fallback["id"]}})
                        sb_delete("hbcc_templates", "id", selection)
                        old_path = record.get("background_path") or ""
                        owned_path = "team-selection-templates/" + selection + ".png"
                        if old_path == owned_path and not any(t.get("background_path") == old_path for t in survivors):
                            try:
                                sb_request("DELETE", "/storage/v1/object/hbcc-assets/" + quote(old_path, safe="/"))
                            except Exception as exc:
                                st.warning("Template deleted; old background cleanup failed: " + str(exc))
                        st.session_state.team_active_template_id = fallback["id"]
                        st.session_state.team_editor_pending_selection = fallback["id"]
                        st.session_state.graphic_settings = {**DEFAULT_GRAPHIC_SETTINGS, **(fallback.get("settings") or {})}
                        st.session_state.graphic_settings_version = 3
                        if fallback.get("background_path"):
                            st.session_state.graphic_template_bytes = sb_download(fallback["background_path"])
                        else:
                            st.session_state.pop("graphic_template_bytes", None)
                        sb_refresh_templates()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete team template: {exc}")


def fixture_publisher(chosen):
    # The page already displays the section title and match count.
    if "fixture_graphic_settings" not in st.session_state:
        st.session_state.fixture_graphic_settings = {"title":"UPCOMING MATCHES", "title_size":90, "cards_top":240, "cards_bottom":1045, "yellow":"#f5b82e", "background":"Red to navy gradient", "logo_scale":1.65, "logo_bleed":55}
    fixture_library()
    if "fixture_view" not in st.session_state:
        st.session_state.fixture_view = "graphics"
    st.caption("Review your fixtures and club logos, then download the finished Instagram graphics.")
    total_graphics = (len(chosen) + 2) // 3
    metric_a, metric_b, metric_c = st.columns(3)
    metric_a.metric("Fixtures", len(chosen))
    metric_b.metric("Graphics", total_graphics)
    metric_c.metric("Format", "1080 × 1350")
    st.markdown("**1. Review details**")
    # Resolve home/away using the match detail endpoint, not the title order.
    # Cache by match ID to avoid repeat requests on every Streamlit rerun.
    cache = st.session_state.setdefault("fixture_home_away_cache", {})
    resolved = []
    for original in chosen:
        row = dict(original)
        mid = row.get("id")
        if mid and (not row.get("home_name") or not row.get("away_name")):
            if mid not in cache:
                try:
                    cache[mid] = fixture_home_away({}, match_detail(mid))
                except Exception:
                    cache[mid] = (None, None)
            hn, an = cache[mid]
            if hn and an:
                row["home_name"], row["away_name"] = hn, an
        resolved.append(row)
    chosen = resolved
    edited = []
    with st.expander("Edit fixture details and home/away", expanded=False):
        for i,row in enumerate(chosen):
            home = row.get("home_name") or fixture_split_teams(row)[0]
            away = row.get("away_name") or fixture_split_teams(row)[1]
            st.markdown(f"**{row.get('grade','Match')} — {home} vs {away}**")
            c1,c2=st.columns(2)
            home=c1.text_input("Home team",home,key=f"fix_home_{row['id']}")
            away=c2.text_input("Away team",away,key=f"fix_away_{row['id']}")
            # Explicit editorial decision: never rely on unreliable API ordering.
            st.radio(
                "Where is HBCC playing?",
                options=["HBCC home", "HBCC away", "Choose left team manually"],
                index=2,
                key=f"fix_side_{row['id']}",
                horizontal=True,
                help="HBCC home places the Hawks on the left. HBCC away places the Hawks on the right.",
            )
            st.caption("If PlayCricket's team order is incorrect, this selection controls the published graphic.")
            c3,c4=st.columns(2)
            venue=c3.text_input("Venue",row.get("venue") or "Venue TBC",key=f"fix_venue_{row['id']}")
            date_value=c4.text_input("Dates (separate with &)"," & ".join(row.get("playing_dates") or [row.get("date", "")]),key=f"fix_date_{row['id']}")
            edited.append({**row,"home_name":home,"away_name":away,"name":f"{home} vs {away}","venue":venue,"playing_dates":[x.strip() for x in date_value.split("&") if x.strip()]})
    if not edited:
        edited = chosen
    # Keep edits from widgets after expander closes.
    rows=[]
    for row in chosen:
        mid=row['id']
        home=row.get("home_name") or fixture_split_teams(row)[0]
        away=row.get("away_name") or fixture_split_teams(row)[1]
        home=st.session_state.get(f"fix_home_{mid}",home)
        away=st.session_state.get(f"fix_away_{mid}",away)
        side = st.session_state.get(f"fix_side_{mid}", "Choose left team manually")
        def is_hbcc(team):
            cleaned = re.sub(r"[^a-z0-9]", "", str(team).lower())
            return "hawthornboroondara" in cleaned or cleaned.startswith("hbcc")
        if side in ("HBCC home", "HBCC away"):
            if is_hbcc(home) and not is_hbcc(away):
                hawks, opponent = home, away
            elif is_hbcc(away) and not is_hbcc(home):
                hawks, opponent = away, home
            else:
                # Team-name fields may contain an abbreviation or typo. Use
                # the original fixture names to recover the actual HBCC team.
                originals = fixture_split_teams(row)
                hawks = next((team for team in originals if is_hbcc(team)), None)
                opponent = next((team for team in (home, away) if not is_hbcc(team)), None)
                if hawks is None or opponent is None:
                    st.warning(f"Check HBCC team names for {row.get('grade', 'this fixture')}: unable to determine the Hawks and opponent.")
                    hawks, opponent = home, away
            home, away = (hawks, opponent) if side == "HBCC home" else (opponent, hawks)
        dates=st.session_state.get(f"fix_date_{mid}"," & ".join(row.get("playing_dates") or [row.get("date", "")]))
        rows.append({**row,"home_name":home,"away_name":away,"name":f"{home} vs {away}","venue":st.session_state.get(f"fix_venue_{mid}",row.get("venue") or "Venue TBC"),"playing_dates":[x.strip() for x in dates.split("&") if x.strip()]})
    # Match IDs from actual team objects, not the fixture's match/grade ID.
    # Fetch details only for selected fixtures and cache them across reruns.
    detail_cache = st.session_state.setdefault("fixture_logo_detail_cache", {})
    for row in rows:
        mid = row.get("id")
        if mid and mid not in detail_cache:
            try:
                detail_cache[mid] = match_detail(mid)
            except Exception:
                detail_cache[mid] = {}
        detail = detail_cache.get(mid) or {}
        original_names = fixture_split_teams(row)
        for side, current_name in (("home", row.get("home_name")), ("away", row.get("away_name"))):
            ids = fixture_team_identifiers(detail, current_name)
            if not ids and current_name:
                # A manually edited display name might differ from the API name.
                # Only recover IDs if exactly one original API team name resolves.
                matches = [fixture_team_identifiers(detail, n) for n in original_names]
                matches = [m for m in matches if m]
                if len(matches) == 1 and fixture_key(current_name) in [fixture_key(n) for n in original_names]:
                    ids = matches[0]
            row[side + "_ids"] = ids

    # Fill in missing team names for IDs previously connected to Supabase.
    # Only update a saved mapping when a real team ID is present and its
    # current club link is already known. This never creates new mappings.
    if sb_ready():
        try:
            existing_records = {str(r.get("source_id")): r for r in sb_rows("hbcc_club_mappings")}
            name_updates = 0
            for row in rows:
                for side in ("home", "away"):
                    team_name = str(row.get(side + "_name") or "").strip()
                    if not team_name:
                        continue
                    for identifier in row.get(side + "_ids", []):
                        if not str(identifier).startswith("team:"):
                            continue
                        record = existing_records.get(str(identifier))
                        if record and not str(record.get("source_name") or "").strip():
                            sb_request("PATCH", "/rest/v1/hbcc_club_mappings",
                                params={"source_id": "eq." + str(identifier)},
                                json_data={"source_name": team_name})
                            record["source_name"] = team_name
                            name_updates += 1
            if name_updates:
                pass  # Quiet background sync: no need to interrupt the publishing workflow.
        except Exception as exc:
            st.warning("Could not update saved team names: " + str(exc))

    clubs = fixture_library()
    # Existing GitHub club entries might lack IDs. Create deterministic local IDs
    # for the dropdown, and persist them only when the user saves a mapping.
    for c in clubs:
        c.setdefault("id", "club_" + fixture_key(c.get("name")))
    choices = [""] + [str(c["id"]) for c in clubs]
    labels = {"": "Automatic matching"}
    labels.update({str(c["id"]): str(c.get("name", "Unnamed")) for c in clubs})
    st.markdown("**2. Check club logos**")
    with st.expander("Review logos, abbreviations and saved club matches", expanded=False):
        st.caption("Recognised clubs can be connected to PlayCricket IDs in one click. Corrections are remembered in Supabase.")
        if st.session_state.get("fixture_mapping_error"):
            st.warning("Mapping database unavailable: " + st.session_state.fixture_mapping_error)
        candidates = []
        for fixture_index, row in enumerate(rows):
            st.markdown(f"**{row.get('grade', 'Match')} — {row.get('home_name')} vs {row.get('away_name')}**")
            for side in ("home", "away"):
                team = row.get(side + "_name", "")
                ids = row.get(side + "_ids", [])
                automatic, method = fixture_resolve_club(team, ids)
                key = "fixture_logo_override_" + str(row.get("id")) + "_" + side
                chosen_id = st.selectbox(
                    f"{team} · {('matched by ' + method) if automatic else 'logo not matched'}",
                    choices, format_func=lambda v: labels.get(v, v), key=key,
                )
                row[side + "_club_override"] = chosen_id or None
                chosen = fixture_club_by_id(chosen_id) if chosen_id else automatic
                # The abbreviation belongs to the club, not to an individual
                # fixture. Show the saved value and let editors update it here.
                if chosen:
                    abbr_key = "fixture_club_abbr_" + str(chosen.get("id")) + "_" + str(fixture_index) + "_" + side
                    if abbr_key not in st.session_state:
                        st.session_state[abbr_key] = str(chosen.get("abbr") or "")
                    abbr_col, save_col = st.columns([3, 1], vertical_alignment="bottom")
                    with abbr_col:
                        updated_abbr = st.text_input(
                            "Club abbreviation · " + str(chosen.get("name", team)),
                            max_chars=8, key=abbr_key,
                            help="Used on every fixture graphic for this club. Saved to hbcc_clubs in Supabase.",
                        )
                    with save_col:
                        save_abbr = st.button("Save abbreviation", key="fixture_save_abbr_" + str(row.get("id")) + "_" + side)
                    if save_abbr:
                        cleaned = updated_abbr.strip().upper()
                        if not cleaned:
                            st.error("Enter an abbreviation before saving.")
                        elif not sb_ready():
                            st.error("Supabase is not connected; abbreviation was not saved.")
                        else:
                            try:
                                # Only update the abbreviation: don't overwrite
                                # the club's logo, name, or aliases with stale data.
                                club_id = str(chosen.get("id") or "")
                                saved = sb_request("PATCH", "/rest/v1/hbcc_clubs",
                                    params={"id": "eq." + club_id},
                                    json_data={"abbreviation": cleaned})
                                chosen["abbr"] = cleaned
                                for library_club in fixture_library():
                                    if str(library_club.get("id")) == club_id:
                                        library_club["abbr"] = cleaned
                                st.success("Saved " + cleaned + " to Supabase for " + str(chosen.get("name")))
                            except Exception as exc:
                                st.error("Could not save abbreviation: " + str(exc))
                if ids:
                    st.caption("PlayCricket IDs: " + ", ".join(ids))
                    if chosen and method != "conflicting IDs":
                        existing = fixture_id_mappings()
                        if any(existing.get(i) != str(chosen["id"]) for i in ids):
                            candidates.append((team, ids, chosen))
                            st.caption("Ready to connect → " + str(chosen.get("name")))
                        else:
                            st.caption("Already connected to Supabase")
                else:
                    st.caption("No team/club identifier found in PlayCricket details. Logo can still match by name.")
                if chosen_id and ids:
                    if st.button("Save ID mapping for " + team, key="save_map_" + str(row.get("id")) + side):
                        try:
                            fixture_save_mapping(ids, fixture_club_by_id(chosen_id), team)
                            st.success("Mapping saved to Supabase")
                        except Exception as exc:
                            st.error(str(exc))
        if candidates:
            st.markdown("**Connect recognised teams to Supabase**")
            st.caption("Only teams with a real PlayCricket ID and a selected/matched club are included. Review the matches above first.")
            if st.button("Save all reviewed ID connections", type="primary", key="fixture_bulk_save_ids"):
                saved, failures = 0, []
                for team, ids, club in candidates:
                    try:
                        # Do not silently overwrite a different existing club mapping.
                        existing = fixture_id_mappings()
                        conflicts = [i for i in ids if i in existing and existing[i] != str(club["id"])]
                        if conflicts:
                            failures.append(team + ": conflicting saved ID " + ", ".join(conflicts))
                            continue
                        fixture_save_mapping(ids, club, team)
                        saved += 1
                    except Exception as exc:
                        failures.append(team + ": " + str(exc))
                if saved:
                    st.success(f"Saved {saved} team/club connections to Supabase. They will be reused on future fixtures.")
                for error in failures:
                    st.warning(error)
        elif rows:
            st.info("No new ID connections to save. Existing mappings are already saved, or PlayCricket did not provide team/club IDs.")
    missing=sorted({name for row in rows for name,ids,override in ((row.get("home_name"),row.get("home_ids"),row.get("home_club_override")),(row.get("away_name"),row.get("away_ids"),row.get("away_club_override"))) if not fixture_resolve_club(name,ids,override)[0]})
    if missing:
        st.warning(
            f"Logo needed: {', '.join(missing[:7])}{'…' if len(missing)>7 else ''}. "
            "Open **Review logos, abbreviations and saved club matches** above to select a club. "
            "Until then, the graphic uses a text abbreviation."
        )
    groups=[rows[i:i+3] for i in range(0,len(rows),3)]
    if not groups:
        st.info("Select at least one fixture."); return
    imgs=[fixture_graphic(group, st.session_state.fixture_graphic_settings) for group in groups]
    st.markdown("**3. Preview and download**")
    st.caption(f"{len(imgs)} graphic(s) ready · up to 3 fixtures per graphic")
    gallery=st.columns(min(3,len(imgs)))
    zipped=io.BytesIO()
    with zipfile.ZipFile(zipped,"w",zipfile.ZIP_DEFLATED) as archive:
        for i,img in enumerate(imgs):
            blob=io.BytesIO();img.save(blob,format="PNG")
            filename=f"hbcc_upcoming_matches_{i+1:02}.png"
            archive.writestr(filename,blob.getvalue())
            with gallery[i%len(gallery)]:
                st.image(blob.getvalue(), caption=f"Graphic {i+1} · {len(groups[i])} fixtures", use_container_width=True)
                st.download_button(f"Download graphic {i+1}",blob.getvalue(),filename,"image/png",key=f"download_fixture_{i}",use_container_width=True)
    st.download_button("Download all fixture graphics (ZIP)",zipped.getvalue(),"hbcc_upcoming_matches.zip","application/zip",type="primary",use_container_width=True)


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
# RESULTS GRAPHIC GENERATOR
# ============================================================
def results_text(draw, center_x, top, value, max_width, size, color="#ffffff", bold=True):
    value = str(value or "").strip()
    font = fixture_text_fit(draw, value, max_width, size, 12, bold)
    fixture_center(draw, center_x, top, value, font, color)



def results_template_admin():
    """Independent Supabase-backed template library for match results."""
    st.markdown("#### Match results templates")
    st.caption("Use a different background and layout for results. Your fixture and team selection templates are unaffected.")
    if not sb_ready():
        st.error("Connect Supabase before managing results templates.")
        return
    try:
        templates = sorted([t for t in sb_rows("hbcc_templates") if t.get("template_type") == "match_results"],
                           key=lambda t: str(t.get("name") or "").lower())
    except Exception as exc:
        st.error(f"Could not load result templates: {exc}")
        return
    by_id = {t["id"]: t for t in templates}
    options = list(by_id) + ["__new__"]
    pending = st.session_state.pop("results_editor_pending", None)
    current = pending or st.session_state.get("results_editor_selection", "__new__")
    if current not in options:
        current = options[0] if templates else "__new__"
    st.session_state["results_editor_selection"] = current
    selection = st.selectbox("Choose results template", options, key="results_editor_selection",
                             format_func=lambda x: "+ Create new template" if x == "__new__" else by_id[x].get("name", x))
    record = by_id.get(selection, {})
    prefix = "result_template_" + selection
    settings = {**RESULTS_DEFAULT_SETTINGS, **(record.get("settings") or {})}
    path = record.get("background_path")
    with st.form("results_template_form_" + selection):
        name = st.text_input("Template name", value=record.get("name", "Results template 1"), key=prefix+"_name")
        if path:
            try:
                st.image(sb_download(path), width=190, caption="Current results background")
            except Exception as exc:
                st.warning("Background preview unavailable: " + str(exc))
        st.caption("Background can include your heading, branding and artwork. Match information is drawn over it.")
        replace = st.checkbox("Upload or replace background", value=False, key=prefix+"_replace")
        uploaded = st.file_uploader("Results background (1080 × 1350 PNG/JPG)", type=["png","jpg","jpeg"],
                                    key=prefix+"_upload")
        with st.expander("Adjust results layout", expanded=False):
            outcome_top = st.slider("Outcome position", 260, 355, int(settings["outcome_top"]), key=prefix+"_outcome")
            score_top = st.slider("Score position", 450, 575, int(settings["score_top"]), key=prefix+"_score")
            performers_top = st.slider("Player highlights position", 735, 825, int(settings["performers_top"]), key=prefix+"_players")
            logo_scale = st.slider("Club logo scale", 0.7, 1.3, float(settings["logo_scale"]), 0.05, key=prefix+"_logos")
        save = st.form_submit_button("Save changes", type="primary", use_container_width=True)
    if save:
        if not name.strip():
            st.error("Enter a template name.")
        elif replace and uploaded is None:
            st.error("Select a background image or untick replacement.")
        else:
            template_id = "results_" + uuid.uuid4().hex[:16] if selection == "__new__" else selection
            new_settings = {"outcome_top":outcome_top, "score_top":score_top,
                            "performers_top":performers_top, "logo_scale":logo_scale, "gold":"#f5b82e"}
            try:
                if replace:
                    path = "match-results-templates/" + template_id + ".png"
                    sb_upload(path, sb_image(uploaded.getvalue()))
                sb_upsert("hbcc_templates", {"id":template_id, "name":name.strip(),
                          "template_type":"match_results", "background_path":path, "settings":new_settings})
                sb_upsert("hbcc_settings", {"key":"active_results_template", "value":{"id":template_id}})
                st.session_state.results_active_template_id = template_id
                st.session_state.results_editor_pending = template_id
                st.session_state.results_graphic_settings = new_settings
                if path:
                    st.session_state.results_background_bytes = sb_download(path)
                else:
                    st.session_state.pop("results_background_bytes", None)
                sb_refresh_templates()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save results template: {exc}")
    if selection != "__new__":
        with st.expander("Delete this template"):
            if len(templates) < 2:
                st.info("Create another results template before deleting this one.")
            else:
                confirm = st.checkbox("I understand this template will be deleted", key=prefix+"_confirm")
                if st.button("Delete results template", disabled=not confirm, key=prefix+"_delete"):
                    try:
                        survivors = [t for t in sb_rows("hbcc_templates") if t.get("template_type") == "match_results" and t.get("id") != selection]
                        if not survivors:
                            st.error("Cannot delete the final results template.")
                        else:
                            fallback = survivors[0]
                            sb_upsert("hbcc_settings", {"key":"active_results_template", "value":{"id":fallback["id"]}})
                            sb_delete("hbcc_templates", "id", selection)
                            old_path = record.get("background_path") or ""
                            if old_path == "match-results-templates/" + selection + ".png" and not any(t.get("background_path") == old_path for t in survivors):
                                try:
                                    sb_request("DELETE", "/storage/v1/object/hbcc-assets/" + quote(old_path, safe="/"))
                                except Exception:
                                    pass
                            st.session_state.results_active_template_id = fallback["id"]
                            st.session_state.results_editor_pending = fallback["id"]
                            st.session_state.results_graphic_settings = {**RESULTS_DEFAULT_SETTINGS, **(fallback.get("settings") or {})}
                            if fallback.get("background_path"):
                                st.session_state.results_background_bytes = sb_download(fallback["background_path"])
                            else:
                                st.session_state.pop("results_background_bytes", None)
                            sb_refresh_templates()
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete results template: {exc}")


def results_render(row):
    """Single continuous results card, with full-height cropped logo panels."""
    w, h = FIXTURE_SIZE
    cfg = {**RESULTS_DEFAULT_SETTINGS, **st.session_state.get("results_graphic_settings", {})}
    gold = cfg["gold"]
    img = Image.new("RGB", (w, h), "#0d1b35")
    d = ImageDraw.Draw(img)
    for yy in range(h):
        t = yy / h
        a, b = (155, 9, 33), (14, 24, 47)
        d.line((0, yy, w, yy), fill=tuple(int(a[k]*(1-t)+b[k]*t) for k in range(3)))
    custom_bg = st.session_state.get("results_background_bytes")
    if custom_bg:
        try:
            img = Image.open(io.BytesIO(custom_bg)).convert("RGB").resize((w,h), Image.Resampling.LANCZOS)
        except Exception:
            pass
    d = ImageDraw.Draw(img)
    if not custom_bg:
        results_text(d, 540, 48, "MATCH RESULTS", 940, 87, gold)
    results_text(d, 540, 166, str(row.get('grade') or '').upper(), 900, 32, '#ffffff')
    # One single uninterrupted results panel, not separate score/performer cards.
    x0, y0, x1, y1 = 42, 233, 1038, 1240
    d.rounded_rectangle((x0,y0,x1,y1), radius=24, fill='#0c1b37', outline=gold, width=5)
    # Logo artwork fills the full-height side strips. Excess horizontal width is cropped.
    home_club, _ = fixture_resolve_club(row.get('home_name') or '')
    away_club, _ = fixture_resolve_club(row.get('away_name') or '')
    fixture_logo_paste(img, home_club, (154, 500), clip_box=(48, 290, 265, 730))
    fixture_logo_paste(img, away_club, (926, 500), clip_box=(815, 290, 1032, 730))
    d = ImageDraw.Draw(img)
    outcome = str(row.get('outcome') or '').strip()
    if outcome:
        d.rounded_rectangle((280,270,800,334),radius=13,fill='#263956')
        results_text(d,540,282,outcome,498,27,gold)
    hname = str((home_club or {}).get('name') or row.get('home_name') or 'TEAM 1')
    aname = str((away_club or {}).get('name') or row.get('away_name') or 'TEAM 2')
    name_font, name_lines = fixture_equal_team_names(d,hname,aname,210,max_size=31,min_size=15)
    line_height = name_font.size + 8
    for x,lines in zip((385,695),name_lines):
        start = 394 + (2-len(lines))*line_height/2
        for i,line in enumerate(lines):
            fixture_center(d,x,start+i*line_height,line,name_font,'#ffffff')
    results_text(d,540,412,'VS',48,21,gold)
    results_text(d,385,520,row.get('home_score') or '—',218,52,gold)
    results_text(d,695,520,row.get('away_score') or '—',218,52,gold)
    results_text(d,540,637,row.get('date_label') or '',560,28,gold)
    results_text(d,540,685,row.get('venue') or '',555,24,'#ffffff',False)
    # Integrated player highlights with no secondary card outline.
    d.line((105,785,975,785),fill='#40516d',width=2)
    for kind,x in (('bat',295),('ball',785)):
        for i in (1,2):
            pname = row.get(f'{kind}{i}_name') or '—'
            figures = row.get(f'{kind}{i}_figures') or ''
            top = 830+i*168
            results_text(d,x,top,pname,400,34,'#ffffff')
            results_text(d,x,top+54,figures,400,32,gold)
    return img


def results_score(innings):
    if not isinstance(innings, dict):
        return ''
    runs = innings.get('runsScored')
    wickets = innings.get('numberOfWicketsFallen')
    if runs is None:
        return ''
    return str(runs) + ('/' + str(wickets) if wickets is not None else '')


def results_scorecard_defaults(fixture):
    """Extract team totals and HBCC-only performers from scorecard innings.

    In a cricket innings, the BOWLING list belongs to the *other* team.
    Never assume that the bowling list belongs to the batting team.
    """
    data = {'home_name': fixture.get('home_name') or '',
            'away_name': fixture.get('away_name') or '',
            'grade': fixture.get('grade', ''),
            'venue': fixture.get('venue', ''),
            'date_label': fixture_pretty_dates(fixture.get('playing_dates') or [fixture.get('date', '')]),
            'home_score': '', 'away_score': '', 'outcome': '', '_innings_count': 0,
            '_fixture_status': str(fixture.get('status') or ''), '_match_status': '',
            **{f'{kind}{i}_{field}': '' for kind in ('bat','ball') for i in (1,2) for field in ('name','figures')}}
    try:
        detail = match_detail(fixture['id'])
        summary = detail.get('matchSummary') or {}
        if not isinstance(summary,dict): summary={}
        data['outcome'] = str(summary.get('resultText') or detail.get('resultText') or '')
        data['_match_status'] = ' | '.join(str(x) for x in (
            summary.get('status'), summary.get('matchStatus'), summary.get('matchState'),
            detail.get('status'), detail.get('matchStatus'), detail.get('matchState'),
            summary.get('resultType'), detail.get('resultType')
        ) if x is not None)
        home,away = fixture_home_away(fixture,detail)
        if home and 'venue tbc' not in home.lower(): data['home_name']=home
        if away and 'venue tbc' not in away.lower(): data['away_name']=away
        for side in ('home','away'):
            if 'venue tbc' in data[side+'_name'].lower(): data[side+'_name']=''
        def walk(obj, depth=0):
            if depth>15:return
            if isinstance(obj,dict):
                yield obj
                for val in obj.values(): yield from walk(val,depth+1)
            elif isinstance(obj,list):
                for val in obj:yield from walk(val,depth+1)
        def total(obj):
            if not isinstance(obj,dict):return ''
            for key in ('inningsScore','scoreSummary','total','score'):
                sub=obj.get(key)
                if isinstance(sub,dict):
                    val=total(sub)
                    if val:return val
                if isinstance(sub,str) and any(c.isdigit() for c in sub):return sub
            runs=next((obj[k] for k in ('runsScored','totalRuns','runs','scoreRuns') if obj.get(k) is not None),None)
            wickets=next((obj[k] for k in ('numberOfWicketsFallen','wicketsLost','wickets','totalWickets') if obj.get(k) is not None),None)
            if runs is None or not str(runs).isdigit():return ''
            return str(runs)+('/'+str(wickets) if wickets is not None else '')
        def norm(s):return re.sub(r'[^a-z0-9]','',str(s or '').lower())
        def is_hbcc(name):
            n=norm(name)
            return 'hawthornboroondara' in n or n=='hbcc'
        def team_info(obj):
            if not isinstance(obj,dict):return ('','')
            for key in ('battingTeam','team','teamSummary','battingTeamSummary'):
                if isinstance(obj.get(key),dict):
                    t=obj[key]
                    return name_of(t),str(t.get('id') or t.get('teamId') or '')
            return (name_of(obj.get('teamName') or obj.get('battingTeamName')),
                    str(obj.get('teamId') or obj.get('battingTeamId') or ''))
        # Team identities and IDs from the actual match (not from the venue).
        teams=[]
        for source in (summary,detail):
            for t in teams_of(source):
                n=name_of(t)
                if n and all(norm(n)!=norm(x[0]) for x in teams):
                    teams.append((n,str(t.get('id') or t.get('teamId') or '')))
        if len(teams)>=2:
            if not data['home_name']:data['home_name']=teams[0][0]
            if not data['away_name']:data['away_name']=teams[1][0]
        hbcc_ids={tid for n,tid in teams if is_hbcc(n) and tid}
        expected_id=str(fixture.get('team_id') or '')
        if expected_id:hbcc_ids.add(expected_id)
        # Capture team totals from labelled team summaries.
        for obj in walk(detail):
            if not isinstance(obj,dict):continue
            n,tid=team_info(obj)
            if not n: n=name_of(obj.get('displayName') or obj.get('name'))
            if not n:continue
            val=total(obj)
            if not val:continue
            for side in ('home','away'):
                if norm(n)==norm(data[side+'_name']) and not data[side+'_score']:
                    data[side+'_score']=val
        for container in (summary,detail):
            for side in ('home','away'):
                for key in (side+'Team',side+'TeamSummary'):
                    if isinstance(container.get(key),dict) and not data[side+'_score']:
                        data[side+'_score']=total(container[key])
        # Collect innings where batting side can be identified. The bowling
        # figures on that innings are recorded for the OPPOSITION, not the batter's team.
        bats=[];balls=[]; innings_seen=set(); innings_records=[]
        for obj in walk(detail):
            if not isinstance(obj,dict):continue
            batting=obj.get('batting')
            bowling=obj.get('bowling')
            if not isinstance(batting,list) or not isinstance(bowling,list):continue
            marker=id(obj)
            if marker in innings_seen:continue
            innings_seen.add(marker)
            n,tid=team_info(obj)
            batting_hbcc = is_hbcc(n) or (bool(tid) and tid in hbcc_ids)
            # If the batting side cannot be established, avoid assigning
            # opposition bowling figures to HBCC.
            batting_known = bool(n or tid)
            innings_total=total(obj)
            if batting_known and (innings_total or batting or bowling):
                innings_records.append({'name': n, 'id': tid, 'total': innings_total, 'obj': obj})
            if innings_total and batting_known:
                for side in ('home','away'):
                    target=data[side+'_name']
                    if norm(n)==norm(target) or (tid and any(norm(target)==norm(tn) and tid==t_id for tn,t_id in teams)):
                        if not data[side+'_score']:data[side+'_score']=innings_total
            if batting_hbcc:
                for p in batting:
                    if not isinstance(p,dict):continue
                    pname=name_of(p.get('playerShortName') or p.get('playerName') or p.get('player'))
                    try:runs=int(p.get('runsScored'))
                    except (TypeError,ValueError):continue
                    if pname and runs > 0:bats.append((runs,pname,f"{runs} ({p.get('ballsFaced') if p.get('ballsFaced') is not None else '—'})", len(innings_records)-1))
            elif batting_known:
                for p in bowling:
                    if not isinstance(p,dict):continue
                    pname=name_of(p.get('playerShortName') or p.get('playerName') or p.get('player'))
                    try:wickets=int(p.get('wicketsTaken'))
                    except (TypeError,ValueError):continue
                    if pname and (wickets > 0 or int(p.get('runsConceded') or 0) > 0):
                        conceded=p.get('runsConceded')
                        balls.append((wickets,-int(conceded or 0),pname,f"{wickets}/{conceded if conceded is not None else '—'}", len(innings_records)-1))
        # Multiple innings: show both team totals in batting order, not just
        # the first total found in a match summary. Innings are chronological
        # in the scorecard's innings list.
        data['_innings_count'] = len(innings_records)
        if len(innings_records) >= 3:
            by_side = {'home': [], 'away': []}
            for index, rec in enumerate(innings_records):
                for side in ('home', 'away'):
                    target = data[side + '_name']
                    if norm(rec['name']) == norm(target) or (rec['id'] and any(norm(target) == norm(tn) and rec['id'] == team_id for tn,team_id in teams)):
                        if rec['total']:
                            by_side[side].append(rec['total'])
                        break
            for side in ('home','away'):
                if by_side[side]:
                    data[side+'_score'] = ' | '.join(by_side[side])
        # Preserve the two strongest individual performances. For a match with
        # 3+ innings label the team's innings (not the global innings number).
        innings_numbers = {}
        for index, rec in enumerate(innings_records):
            key = rec['id'] or norm(rec['name'])
            innings_numbers[key] = innings_numbers.get(key, 0) + 1
            rec['team_innings'] = innings_numbers[key]
        for kind,entries in (('bat',sorted(bats,key=lambda e:e[0],reverse=True)),
                             ('ball',sorted(balls,key=lambda e:(e[0],e[1]),reverse=True))):
            seen=set();idx=0
            for entry in entries:
                pname,figures,inning_index=entry[-3:]
                if pname.casefold() in seen:continue
                seen.add(pname.casefold());idx+=1
                if idx>2:break
                if len(innings_records)>=3 and 0 <= inning_index < len(innings_records):
                    figures += ' · Inn ' + str(innings_records[inning_index]['team_innings'])
                data[f'{kind}{idx}_name']=pname
                data[f'{kind}{idx}_figures']=figures
        data['_scorecard_loaded']=True
    except Exception as error:
        data['_load_error']=str(error)
    return data


def draw_faded_navy_band(draw, x0, y0, x1, y1, base="#0c1b37", highlight="#243b60", fade_fraction=0.23):
    """Draw a light navy centre band that smoothly blends into the card navy."""
    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    dark, light = rgb(base), rgb(highlight)
    width = max(1, int(x1-x0))
    fade = max(1, int(width * fade_fraction))
    for i in range(width):
        intensity = min(1.0, i/fade, (width-1-i)/fade)
        intensity = max(0.0, intensity)
        intensity = intensity*intensity*(3-2*intensity)
        color = tuple(round(dark[k] + (light[k]-dark[k])*intensity) for k in range(3))
        draw.line((int(x0+i), int(y0), int(x0+i), int(y1)), fill=color, width=1)


def results_score_lines(value):
    """Return display-ready innings totals, keeping multiple innings separate."""
    parts = [part.strip() for part in str(value or '').split('|') if part.strip()]
    # Scorecard stores runs/wickets; an unstarted 0/0 innings is not a result.
    return [results_wickets_first(part) for part in parts
            if not re.fullmatch(r"0\s*/\s*0(?:\s*\([^)]*\))?", part)]


def results_wickets_first(value):
    """Display wickets/runs, retaining any overs annotation unchanged."""
    raw = str(value or "").strip()
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)(.*)", raw)
    if not match:
        return raw or "—"
    runs, wickets, suffix = match.groups()
    return f"{wickets}/{runs}{suffix}"


def results_render_pair(rows):
    """Mobile-first paired results, with a clean sponsor-ready footer."""
    w, h = FIXTURE_SIZE
    cfg = {**RESULTS_DEFAULT_SETTINGS, **st.session_state.get("results_graphic_settings", {})}
    gold = cfg["gold"]
    img = Image.new("RGB", (w, h), "#101b33")
    d = ImageDraw.Draw(img)
    for yy in range(h):
        t = yy / h
        a, b = (133, 10, 34), (13, 24, 47)
        d.line((0, yy, w, yy), fill=tuple(int(a[k]*(1-t)+b[k]*t) for k in range(3)))
    custom_bg = st.session_state.get("results_background_bytes")
    if custom_bg:
        try:
            source = Image.open(io.BytesIO(custom_bg)).convert("RGB")
            # Preserve the background artwork's aspect ratio. Cropping away the
            # old heading and then stretching the remaining image distorted
            # sponsor logos and other artwork. Use proportional cover scaling
            # instead, anchored toward the bottom to protect footer branding.
            from PIL import ImageOps
            img = ImageOps.fit(source, (w, h), method=Image.Resampling.LANCZOS,
                               centering=(0.5, 1.0))
        except Exception:
            pass
    d = ImageDraw.Draw(img)

    # Cards begin near the top, leaving 250px below for club/sponsor branding.
    for idx, row in enumerate(rows[:2]):
        top = 20 + idx * 540
        bottom = top + 520
        left, right = 24, 1056
        side_width = round((right - left) / 6)
        left_end, right_start = left + side_width, right - side_width
        d.rounded_rectangle((left, top, right, bottom), radius=22,
                            fill="#0c1b37", outline=gold, width=4)
        home_club, _ = fixture_resolve_club(row.get("home_name") or "", row.get("home_ids"), row.get("home_club_override"))
        away_club, _ = fixture_resolve_club(row.get("away_name") or "", row.get("away_ids"), row.get("away_club_override"))
        fixture_logo_paste(img, home_club, ((left + left_end)//2, (top + bottom)//2),
                           clip_box=(left+4, top+4, left_end, bottom-4))
        fixture_logo_paste(img, away_club, ((right_start + right)//2, (top + bottom)//2),
                           clip_box=(right_start, top+4, right-4, bottom-4))
        navy = (12, 27, 55)
        fade_width = 88
        for edge, direction in ((left_end, 1), (right_start, -1)):
            for step in range(fade_width):
                progress = step / max(1, fade_width - 1)
                x = edge - fade_width + step if direction == 1 else edge + step
                opacity = progress if direction == 1 else 1.0 - progress
                opacity = opacity * opacity * (3 - 2 * opacity)
                overlay = Image.new("RGB", (1, bottom - top - 8), navy)
                mask = Image.new("L", overlay.size, round(opacity * 255))
                img.paste(overlay, (int(x), top + 4), mask)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((left, top, right, bottom), radius=22, outline=gold, width=4)
        centre_left, centre_right = left_end + 8, right_start - 8
        centre = (centre_left + centre_right) / 2
        col_left = centre_left + (centre_right-centre_left)*0.26
        col_right = centre_left + (centre_right-centre_left)*0.74
        col_width = (centre_right-centre_left)*0.46

        draw_faded_navy_band(d, centre_left, top + 5, centre_right, top + 96,
                             highlight="#243b60")
        results_text(d, centre, top + 9, str(row.get("grade") or "").upper(),
                     centre_right-centre_left-12, 36, gold)
        outcome = str(row.get("outcome") or "").strip()
        if outcome:
            results_text(d, centre, top + 54, outcome,
                         centre_right-centre_left-15, 26, "#ffffff")

        hname = str((home_club or {}).get("name") or row.get("home_name") or "TEAM 1")
        aname = str((away_club or {}).get("name") or row.get("away_name") or "TEAM 2")
        # Always reserve a genuine two-line name area, with the VS in its
        # own centre gutter (rather than crowding a long club name).
        name_width = min(col_width - 14, (centre_right - centre_left) * 0.425)
        # Match Results: keep Hawthorn Boroondara on two balanced lines,
        # regardless of the space available for a single-line label.
        def result_name_lines(name, font):
            cleaned = " ".join(str(name).split())
            if cleaned.casefold() == "hawthorn boroondara":
                return ["Hawthorn", "Boroondara"]
            return fixture_wrapped_lines(d, cleaned, name_width, font, max_lines=99)

        for name_size in range(32, 17, -1):
            font = fixture_font(name_size, True)
            names = [result_name_lines(hname, font), result_name_lines(aname, font)]
            if all(len(lines) <= 2 and all(
                d.textbbox((0, 0), line, font=font)[2] <= name_width
                for line in lines
            ) for lines in names):
                break
        else:
            font = fixture_font(18, True)
            names = [result_name_lines(hname, font)[:2], result_name_lines(aname, font)[:2]]
        line_h = font.size + 4
        for x, lines in zip((col_left, col_right), names):
            y0 = top + 105 + (2-len(lines))*line_h/2
            for j, line in enumerate(lines):
                fixture_center(d, x, y0+j*line_h, line, font, "#ffffff")
        results_text(d, centre, top + 135, "VS", 38, 21, gold)
        for x, raw_score in ((col_left, row.get("home_score")), (col_right, row.get("away_score"))):
            scores = results_score_lines(raw_score)
            if len(scores) > 1:
                # Multiple innings share one score line; auto-fit within the team column.
                results_text(d, x, top + 194, ' & '.join(scores), col_width, 54, gold)
            else:
                results_text(d, x, top + 194, scores[0] if scores else '', col_width, 54, gold)
        results_text(d, centre, top + 253, row.get("date_label") or "",
                     centre_right-centre_left-10, 27, gold)
        results_text(d, centre, top + 289, row.get("venue") or "",
                     centre_right-centre_left-10, 24, "#ffffff", False)
        d.line((centre_left+8, top+330, centre_right-8, top+330), fill="#40516d", width=2)

        def hbcc_team(value):
            key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
            return "hawthornboroondara" in key or key == "hbcc"
        home_hbcc = hbcc_team(row.get("home_name")) or hbcc_team(hname)
        away_hbcc = hbcc_team(row.get("away_name")) or hbcc_team(aname)
        if home_hbcc and not away_hbcc:
            bat_x, ball_x = col_left, col_right
        elif away_hbcc and not home_hbcc:
            bat_x, ball_x = col_right, col_left
        else:
            bat_x, ball_x = col_left, col_right
        for kind, x in (("bat", bat_x), ("ball", ball_x)):
            for n in (1, 2):
                y = top + 356 + (n-1)*70
                pname = str(row.get(f"{kind}{n}_name") or "").strip()
                figures = str(row.get(f"{kind}{n}_figures") or "").strip()
                if not pname and not figures:
                    continue
                # Do not print placeholder/zero batting or bowling performances,
                # including records manually entered in the match editor.
                if kind == "bat" and re.match(r"^0(?:\s|\(|$)", figures):
                    continue
                if kind == "ball" and re.match(r"^0\s*/\s*0(?:\D|$)", figures):
                    continue
                # Single-line stat row gives larger, more legible type on phones.
                combined = f"{pname}  {figures}".strip()
                results_text(d, x, y, combined, col_width, 29, "#ffffff")

    # Optional assets sit in the reserved footer, not on top of match information.
    footer_top = 1110
    def footer_asset(data, box):
        if not data:
            return
        try:
            asset = Image.open(io.BytesIO(data)).convert("RGBA")
            asset.thumbnail((box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
            x = box[0] + ((box[2]-box[0])-asset.width)//2
            y = box[1] + ((box[3]-box[1])-asset.height)//2
            img.paste(asset, (x, y), asset)
        except Exception:
            pass
    footer_asset(st.session_state.get("results_footer_club_logo"), (65, footer_top, 505, 1320))
    footer_asset(st.session_state.get("results_footer_sponsor_logo"), (575, footer_top, 1015, 1320))
    return img


def results_is_cancelled(row, fixture=None):
    """Cancellation overrides score presence; washouts/abandonments do not.

    Read both fixture status and scorecard status/result. Do not classify
    abandoned, no-result or washed-out matches as cancelled.
    """
    fields = [row.get('_fixture_status'), row.get('_match_status'),
              row.get('outcome')]
    if fixture:
        fields.extend((fixture.get('status'), fixture.get('matchStatus'),
                       fixture.get('resultText')))
    for value in fields:
        label = str(value or '').strip().lower()
        if re.search(r"\b(cancelled|canceled|cancellation|match cancelled|match canceled)\b", label):
            # Avoid treating an explicit negation as a cancellation.
            if not re.search(r"\b(not|never)\s+(?:been\s+)?cancelled\b", label):
                return True
    return False


def results_is_publishable(row, fixture=None):
    """Exclude cancelled games even if a score exists; include scored washouts."""
    if results_is_cancelled(row, fixture):
        return False
    for key in ('home_score', 'away_score'):
        value = str(row.get(key) or '').strip()
        if any(re.search(r"\d", part) and not re.fullmatch(r"0\s*/\s*0", part)
               for part in value.split("|")):
            return True
    return False


def results_club_choices():
    """Return all Supabase-backed club visuals available to the fixture tool."""
    clubs = fixture_library()
    for club in clubs:
        club.setdefault("id", "club_" + fixture_key(club.get("name")))
    return {str(club["id"]): club for club in clubs}


def results_team_ids(fixture, row):
    """Extract verified team/club IDs from the same PlayCricket match detail."""
    mid = str(fixture.get("id") or "")
    cache = st.session_state.setdefault("fixture_logo_detail_cache", {})
    if mid not in cache:
        try:
            cache[mid] = match_detail(mid)
        except Exception:
            cache[mid] = {}
    detail = cache.get(mid) or {}
    return {side: fixture_team_identifiers(detail, row.get(side + "_name") or "")
            for side in ("home", "away")}


def results_swap_sides(match_id):
    """Swap the two displayed teams and their scores before widgets render.

    Player batting/bowling columns follow the HBCC team identity in the renderer.
    """
    store = st.session_state.setdefault('results_editors', {})
    row = store.get(str(match_id))
    if row is None:
        return
    for suffix in ('name', 'score', 'club_override', 'ids'):
        a, b = 'home_' + suffix, 'away_' + suffix
        row[a], row[b] = row.get(b), row.get(a)
    key_a = 'results_logo_override_' + str(match_id) + '_home'
    key_b = 'results_logo_override_' + str(match_id) + '_away'
    if key_a in st.session_state or key_b in st.session_state:
        st.session_state[key_a], st.session_state[key_b] = (
            st.session_state.get(key_b, ''), st.session_state.get(key_a, ''))
    # Explicitly synchronize visible team and score widgets with swapped values.
    for field, prefix in (('home_name','res_home_'),('away_name','res_away_'),
                          ('home_score','res_hscore_'),('away_score','res_ascore_')):
        st.session_state[prefix + str(match_id)] = row.get(field) or ''


def results_publisher(chosen):
    st.caption('Two match results per 1080 × 1350 graphic; an odd final match gets its own graphic.')
    with st.expander('Footer branding (optional)', expanded=False):
        st.caption('The heading is removed. Add a club logo and sponsor logo in the reserved bottom section; transparent PNGs work best.')
        footer_left, footer_right = st.columns(2)
        with footer_left:
            club_file = st.file_uploader('Footer club logo', type=['png', 'jpg', 'jpeg', 'webp'], key='results_footer_club_upload')
            if club_file is not None:
                st.session_state['results_footer_club_logo'] = club_file.getvalue()
        with footer_right:
            sponsor_file = st.file_uploader('Footer sponsor logo', type=['png', 'jpg', 'jpeg', 'webp'], key='results_footer_sponsor_upload')
            if sponsor_file is not None:
                st.session_state['results_footer_sponsor_logo'] = sponsor_file.getvalue()
        if st.button('Clear footer logos', key='results_clear_footer'):
            st.session_state.pop('results_footer_club_logo', None)
            st.session_state.pop('results_footer_sponsor_logo', None)
            st.rerun()
    if not chosen:
        st.info('Select at least one completed match in Review Fixtures.')
        return
    store = st.session_state.setdefault('results_editors', {})
    if st.button('Load / refresh all selected scorecards', key='results_refresh_all'):
        with st.spinner('Reading PlayCricket scorecards...'):
            for fixture in chosen:
                store[str(fixture['id'])] = results_scorecard_defaults(fixture)
                # Refresh any existing widget values too.
                mid = str(fixture['id'])
                for field, prefix in [('home_name','res_home_'),('away_name','res_away_'),('home_score','res_hscore_'),('away_score','res_ascore_'),('outcome','res_outcome_'),('grade','res_grade_'),('date_label','res_date_'),('venue','res_venue_')]:
                    st.session_state[prefix+mid] = store[mid][field]
                for kind in ('bat','ball'):
                    for i in (1,2):
                        st.session_state[f'res_{kind}{i}n_'+mid] = store[mid][f'{kind}{i}_name']
                        st.session_state[f'res_{kind}{i}f_'+mid] = store[mid][f'{kind}{i}_figures']
        st.rerun()
    missing = [f for f in chosen if str(f['id']) not in store]
    if missing:
        with st.spinner(f'Reading {len(missing)} PlayCricket scorecard(s)...'):
            for fixture in missing:
                store[str(fixture['id'])] = results_scorecard_defaults(fixture)
    st.caption(f'{len(chosen)} matches selected. Cancelled matches are excluded; washed-out or abandoned matches appear only when a score is recorded.')
    st.caption('Edit each match below. For matches with 3+ innings, scores show both innings and player figures identify their innings.')
    images = []
    club_choices = results_club_choices()
    club_options = [""] + sorted(club_choices, key=lambda cid: str(club_choices[cid].get("name") or "").lower())
    club_labels = {"": "Use automatic club matching"}
    club_labels.update({cid: str(club.get("name") or cid) for cid, club in club_choices.items()})
    unmatched = []
    for number, fixture in enumerate(chosen, 1):
        mid = str(fixture['id'])
        row = store[mid]
        heading = f"{number}. {fixture.get('grade') or 'Match'} · {fixture.get('name') or mid[:8]}"
        with st.expander(heading, expanded=(number == 1)):
            if row.get('_load_error'):
                st.warning('Could not fully load this scorecard: '+str(row['_load_error']))
            st.caption('Team 1 appears on the left of the graphic; Team 2 appears on the right. Use Swap sides to reverse both teams, their innings scores and their logos. You can also edit either team name below.')
            st.button('⇄ Swap home and away teams', key='results_swap_' + mid,
                      on_click=results_swap_sides, args=(mid,))
            with st.container(border=True):
                st.markdown('**Match details**')
                a,b = st.columns(2)
                with a:
                    row['home_name'] = st.text_input('Team 1', value=row['home_name'], key='res_home_'+mid)
                    row['home_score'] = st.text_input('Team 1 innings scores (separate with |)', value=row['home_score'], key='res_hscore_'+mid)
                with b:
                    row['away_name'] = st.text_input('Team 2', value=row['away_name'], key='res_away_'+mid)
                    row['away_score'] = st.text_input('Team 2 innings scores (separate with |)', value=row['away_score'], key='res_ascore_'+mid)
                row['outcome'] = st.text_input('Match outcome', value=row['outcome'], key='res_outcome_'+mid)
                row['grade'] = st.text_input('Grade', value=row['grade'], key='res_grade_'+mid)
                row['date_label'] = st.text_input('Match date', value=row['date_label'], key='res_date_'+mid)
                row['venue'] = st.text_input('Venue', value=row['venue'], key='res_venue_'+mid)
            with st.container(border=True):
                st.markdown('**HBCC performers**')
                left,right = st.columns(2)
                for kind, col, title in (('bat',left,'Batting'),('ball',right,'Bowling')):
                    with col:
                        st.markdown('**'+title+'**')
                        for i in (1,2):
                            row[f'{kind}{i}_name'] = st.text_input(f'Player {i} · {title}', value=row[f'{kind}{i}_name'], key=f'res_{kind}{i}n_'+mid)
                            row[f'{kind}{i}_figures'] = st.text_input(f'Figures {i} · {title}', value=row[f'{kind}{i}_figures'], key=f'res_{kind}{i}f_'+mid)
            # Review logo assignments here, without requiring a separate trip
            # to Upcoming Fixtures. Only valid PlayCricket IDs can be saved.
            team_ids = results_team_ids(fixture, row)
            with st.expander("Club logos — check and assign", expanded=False):
                st.caption("Choose a saved Supabase club logo if the automatic match is missing or incorrect. Save a detected team ID to remember the choice.")
                for side in ("home", "away"):
                    team_name = str(row.get(side + "_name") or "").strip()
                    ids = team_ids.get(side, [])
                    automatic, method = fixture_resolve_club(team_name, ids)
                    override_key = "results_logo_override_" + mid + "_" + side
                    selected = st.selectbox(
                        ("Team 1" if side == "home" else "Team 2") + " · " + (team_name or "Unknown team"),
                        club_options, key=override_key,
                        format_func=lambda cid: club_labels.get(cid, cid),
                    )
                    club = club_choices.get(selected) if selected else automatic
                    row[side + "_club_override"] = selected or None
                    if club and fixture_logo_bytes(club):
                        st.caption("Logo: " + str(club.get("name")) + " · " + ("manual selection" if selected else method))
                    else:
                        st.warning("No club logo assigned for " + (team_name or "this team") + ". Select a club above, or add its logo in Admin Mode → Club Visuals & Aliases.")
                    if ids and club:
                        st.caption("Detected ID(s): " + ", ".join(ids))
                        if st.button("Save logo mapping for " + (team_name or side), key="results_save_logo_" + mid + "_" + side):
                            existing = fixture_id_mappings()
                            conflicts = [identifier for identifier in ids
                                         if identifier in existing and existing[identifier] != str(club.get("id"))]
                            if conflicts:
                                st.error("An ID is already connected to another club: " + ", ".join(conflicts) + ". Remove the old mapping in Admin Mode first.")
                            else:
                                try:
                                    fixture_save_mapping(ids, club, team_name)
                                    st.success("Club logo mapping saved to Supabase.")
                                except Exception as exc:
                                    st.error("Could not save club mapping: " + str(exc))
                    elif not ids:
                        st.caption("No verified PlayCricket team ID was provided. Your logo selection applies to this graphic; it cannot be saved by ID.")
            st.caption('This match will appear in the combined graphic preview below if a score is recorded.')
    # Re-evaluate after edits. Cancelled games are never published.
    publishable = [f for f in chosen if results_is_publishable(store[str(f['id'])], f)]
    cancelled_count = sum(results_is_cancelled(store[str(f['id'])], f) for f in chosen)
    unscored_count = len(chosen) - len(publishable) - cancelled_count
    if cancelled_count or unscored_count:
        st.info(f'Excluded {cancelled_count} cancelled match(es) and {unscored_count} match(es) without scores.')
    # Alert on missing logos for matches that will actually be published.
    missing_logos = []
    for fixture in publishable:
        row = store[str(fixture['id'])]
        ids = results_team_ids(fixture, row)
        for side in ('home', 'away'):
            override = row.get(side + '_club_override')
            club, _ = fixture_resolve_club(row.get(side + '_name'), ids.get(side), override)
            if not club or not fixture_logo_bytes(club):
                missing_logos.append(str(row.get(side + '_name') or 'Unknown team'))
    if missing_logos:
        st.warning('Missing club logo for: ' + ', '.join(sorted(set(missing_logos))) + '. Expand the match editor → Club logos — check and assign to select the correct club.')
    if not publishable:
        st.warning('No completed matches with recorded scores to export yet.')
        return
    st.markdown(f"**{len(publishable)} results · {(len(publishable)+1)//2} graphics ready**")
    # Build combined graphics after all editors have updated their rows.
    # Match the Upcoming Fixtures gallery: up to three portrait previews per row.
    for offset in range(0, len(publishable), 2):
        batch = publishable[offset:offset+2]
        rows = []
        for fixture in batch:
            result_row = store[str(fixture["id"])]
            result_row.update({side + "_ids": results_team_ids(fixture, result_row).get(side, [])
                               for side in ("home", "away")})
            rows.append(result_row)
        graphic = results_render_pair(rows)
        graphic_num = offset // 2 + 1
        filename = f"HBCC_Match_Results_{graphic_num:02d}.png"
        images.append((filename, graphic))

    encoded_images = [(filename, graphic_png(graphic)) for filename, graphic in images]
    gallery = st.columns(min(3, len(encoded_images)))
    for i, (filename, png_bytes) in enumerate(encoded_images):
        match_count = min(2, len(publishable) - 2 * i)
        with gallery[i % len(gallery)]:
            st.image(png_bytes,
                     caption=f"Graphic {i+1} · {match_count} match{'es' if match_count > 1 else ''}",
                     use_container_width=True)
            st.download_button(f"Download graphic {i+1}", png_bytes,
                               filename, "image/png", key=f"results_pair_{i+1}",
                               use_container_width=True)
    if images:
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for filename, png_bytes in encoded_images:
                zf.writestr(filename, png_bytes)
        st.download_button(f"Download all {len(images)} graphics (ZIP)", archive.getvalue(),
                           "HBCC_Match_Results.zip", "application/zip", type="primary",
                           use_container_width=True, key="results_download_all")

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


admin_col, admin_status = st.columns([1, 4])
with admin_col:
    if st.button("Admin mode" if not st.session_state.get("global_admin_view",False) else "Back to Studio", use_container_width=True, type="primary" if st.session_state.get("global_admin_view",False) else "secondary"):
        st.session_state.global_admin_view = not st.session_state.get("global_admin_view",False)
        st.rerun()
with admin_status:
    if st.session_state.get("global_admin_unlocked",False):
        st.caption("Admin access unlocked")
if st.session_state.get("global_admin_view",False):
    global_admin_panel()
    st.stop()

stage = st.session_state.workflow

steps = [
    ("setup", "01 · Setup"),
    ("review", "02 · Review Fixtures"),
    ("export", "03 · Create & Export")
]

# Navigation styling — HBCC yellow with navy text
st.markdown("""
<style>
/* Only style the three progress navigation buttons */
.st-key-nav_setup button,
.st-key-nav_review button,
.st-key-nav_export button {
    background-color: #f5b82e !important;
    color: #0f192d !important;
    border: 1px solid #f5b82e !important;
    font-weight: 700 !important;
}

/* Selected navigation step */
.st-key-nav_setup button[kind="primary"],
.st-key-nav_review button[kind="primary"],
.st-key-nav_export button[kind="primary"] {
    background-color: #e5a700 !important;
    border: 2px solid #0f192d !important;
    color: #0f192d !important;
}

/* Hover styling */
.st-key-nav_setup button:hover,
.st-key-nav_review button:hover,
.st-key-nav_export button:hover {
    background-color: #ffd15a !important;
    border-color: #0f192d !important;
    color: #0f192d !important;
}

/* Disabled navigation steps */
.st-key-nav_setup button:disabled,
.st-key-nav_review button:disabled,
.st-key-nav_export button:disabled {
    background-color: #f5b82e !important;
    color: #0f192d !important;
    opacity: 0.55;
}
</style>
""", unsafe_allow_html=True)

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
        "Team Selections",
        "Upcoming Fixtures",
        "Match Results"
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
        ),
        "Upcoming Fixtures": (
            "Create upcoming match graphics with club logos"
        ),
        "Match Results": (
            "Scores, outcome and HBCC best performers"
        )
    }

    mode_columns = st.columns(5)

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

                # Reserve identical description space in all four mode cards.
                # Long descriptions wrap inside the reserved area without
                # pushing the button or card border down.
                st.markdown(
                    '<div style="height:52px;min-height:52px;max-height:52px;'
                    'overflow:hidden;display:flex;align-items:flex-start;'
                    'padding-top:7px;color:#778294;font-size:0.875rem;'
                    'line-height:1.45;">'
                    + html.escape(mode_descriptions[option])
                    + '</div>',
                    unsafe_allow_html=True,
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
            if mode in ("Team Selections", "Upcoming Fixtures")
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

                        playing_dates = scheduled_match_dates(record)
                        when = (
                            playing_dates[0] if playing_dates
                            else parse_date(record)
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

                        # Include multi-day matches if ANY scheduled
                        # playing day falls in the selected date range.
                        if not (set(playing_dates) & dates) and when not in dates:
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

                        display_dates = fixture.get("playing_dates") or [fixture["date"]]
                        st.caption(
                            f"{' & '.join(display_dates)} · "
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

    elif mode == "Upcoming Fixtures":
        fixture_publisher(chosen)

    elif mode == "Match Results":
        results_publisher(chosen)

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

            st.session_state.selection_rows = rows
            # A fresh retrieval must initialise each editor from the
            # newly fetched players, not a previous session's text area.
            for fixture_row in rows:
                fixture_mid = fixture_row["id"]
                for prefix in ("player_edit_v2_", "player_source_v2_"):
                    st.session_state.pop(prefix + fixture_mid, None)

        if st.session_state.selection_rows:
            team_rows = st.session_state.selection_rows
            edited_rows = []
            total_players = 0

            st.caption(f"{len(team_rows)} teams retrieved · Player lists are ready for publishing.")
            with st.expander("Edit or review player lists", expanded=False):
                # Compact team review: one editor, all team data retained.
                team_rows = st.session_state.selection_rows
                active_idx = st.selectbox(
                    "Team to review",
                    options=list(range(len(team_rows))),
                    format_func=lambda i: f"{team_rows[i]['grade']} · {team_rows[i]['name']}",
                    key="active_team_review_index",
                )

                for i, row in enumerate(team_rows):
                    mid = row["id"]
                    candidates = row["candidates"]
                    is_active = i == active_idx
                    source_key = "player_source_v2_" + mid
                    edit_key = "player_edit_v2_" + mid
                    choice_key = "source_" + mid

                    # Initialise each team's editor, even when it is not visible.
                    if candidates:
                        source_idx = min(int(st.session_state.get(choice_key, 0)), len(candidates) - 1)
                        if is_active:
                            source_idx = st.selectbox(
                                "Team list source",
                                options=list(range(len(candidates))),
                                index=source_idx,
                                format_func=lambda j, opts=candidates: (
                                    f"{len(opts[j]['players'])} players · {opts[j]['source']}"
                                ),
                                key=choice_key,
                            )
                        source_players = candidates[source_idx]["players"]
                    else:
                        source_players = []

                    signature = tuple(source_players)
                    existing_text = st.session_state.get(edit_key, "")
                    if (st.session_state.get(source_key) != signature or
                            (source_players and not str(existing_text).strip())):
                        st.session_state[edit_key] = "\n".join(source_players)
                        st.session_state[source_key] = signature

                    if is_active:
                        st.caption(f"{row['date']} · {row['venue'] or 'Venue unavailable'}")
                        if row["error"]:
                            st.error(row["error"])
                        if not candidates:
                            st.info("Selected team coming soon. You can add players manually when published.")
                        elif not candidates[source_idx]["explicit"]:
                            st.caption("General player list — verify against the published team.")
                        if source_players and not any("(c)" in player.lower() for player in source_players):
                            st.caption("Captain not detected. Add (c) after the captain's name if required.")
                        st.text_area(
                            "Published players (one per line; mark captain with (c))",
                            key=edit_key,
                            height=min(410, max(230, len(source_players) * 27 + 45)),
                        )
                        if st.checkbox("Show technical player list sources", key="show_technical_" + mid):
                            st.json([{"source": c["source"], "players": c["players"]} for c in candidates])

                    players = [line.strip() for line in st.session_state.get(edit_key, "").splitlines() if line.strip()]
                    total_players += len(players)
                    edited_rows.append({**row, "players": players})


            st.caption(f"{total_players} players across {len(edited_rows)} teams · [Verify selected team on PlayCricket]({team_rows[active_idx]['url']})")
            output = (
                selection_output(
                    edited_rows
                )
            )

            st.divider()
            st.markdown("### Publish your team selections")
            st.caption(
                "Review or edit the players above, then choose how to publish. "
                "Both outputs use the same updated team lists."
            )
            # All exports use the same edited teams, ordered by grade.
            grade_order = {grade: i for i, grade in enumerate(all_grades)}
            card_rows = sorted(
                edited_rows,
                key=lambda row: (
                    grade_order.get(row.get("grade"), 999),
                    row.get("date", ""),
                    row.get("name", ""),
                ),
            )
            # Primary publishing navigation: real Streamlit buttons, not tabs.
            if "team_publish_view" not in st.session_state:
                st.session_state.team_publish_view = "graphics"
            st.markdown("""
            <style>
            div[data-testid="stButton"] button[kind="primary"] {
                background: #f5b82e !important;
                color: #101b33 !important;
                border: 2px solid #f5b82e !important;
                font-weight: 800 !important;
            }
            div[data-testid="stButton"] button[kind="secondary"] {
                background: #101b33 !important;
                color: white !important;
                border: 2px solid #101b33 !important;
                font-weight: 700 !important;
            }
            div[data-testid="stButton"] button[kind="secondary"]:hover {
                border-color: #f5b82e !important;
                color: #f5b82e !important;
            }
            </style>
            """, unsafe_allow_html=True)
            publish_cols = st.columns(3, gap="small")
            publish_options = [
                ("graphics", "Instagram graphics"),
                ("announcement", "Combined team announcement"),
                ("template", "Template Manager"),
            ]
            for nav_col, (nav_key, nav_label) in zip(publish_cols, publish_options):
                with nav_col:
                    if st.button(
                        nav_label,
                        key=f"publish_nav_{nav_key}",
                        type="primary" if st.session_state.team_publish_view == nav_key else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state.team_publish_view = nav_key
                        st.rerun()
            st.divider()

            if st.session_state.team_publish_view == "graphics":
                st.markdown("#### Instagram graphic generator")
                st.caption(
                    "Each selected team becomes its own 1080 × 1350 Instagram graphic "
                    "using the HBCC background and ITC Franklin Gothic LT Medium. "
                    "Your edited player lists and multi-day dates are included."
                )
                if not DEFAULT_TEMPLATE.exists() and not st.session_state.get("graphic_template_bytes"):
                    st.warning("Template missing: add hbcc_team_template.png beside streamlit_app.py or upload one in Template Manager.")
                if not BROADCAST_BODY_FONT.exists() and not st.session_state.get("graphic_font_bytes"):
                    st.warning("Club font missing: add your licensed ITC Franklin Gothic LT Medium.otf beside streamlit_app.py or upload it in Template Manager.")
                graphic_rows = card_rows
                if graphic_rows:
                    st.markdown(f"**{len(graphic_rows)} team graphics ready**")
                    preview_mode = st.radio(
                        "Preview layout",
                        ["Gallery — all teams", "Single team — larger preview"],
                        horizontal=True,
                        key="team_graphic_preview_mode",
                    )
                    if preview_mode == "Gallery — all teams":
                        st.caption("See every team at once. Download a PNG beneath each preview, or use the ZIP below.")
                        for batch_start in range(0, len(graphic_rows), 3):
                            gallery_cols = st.columns(3, gap="medium")
                            for offset, gallery_col in enumerate(gallery_cols):
                                index = batch_start + offset
                                if index >= len(graphic_rows):
                                    continue
                                row = graphic_rows[index]
                                with gallery_col:
                                    st.markdown(f"**{row['grade']}**")
                                    st.caption(f"vs {graphic_opponent(row)}")
                                    rendered = render_team_graphic(row)
                                    st.image(rendered, use_container_width=True)
                                    st.download_button(
                                        "Download PNG",
                                        data=graphic_png(rendered),
                                        file_name=graphic_filename(row, index + 1),
                                        mime="image/png",
                                        key=f"gallery_download_{index}",
                                        use_container_width=True,
                                    )
                    else:
                        preview_choice = st.selectbox(
                            "Select team",
                            range(len(graphic_rows)),
                            format_func=lambda i: f"{graphic_rows[i]['grade']} · {graphic_opponent(graphic_rows[i])}",
                            key="graphic_preview_choice"
                        )
                        preview_row = graphic_rows[preview_choice]
                        preview = render_team_graphic(preview_row)
                        st.image(preview, caption=f"{preview_row['grade']} · Instagram portrait", width=420)
                        st.download_button(
                            "Download this team's PNG",
                            data=graphic_png(preview),
                            file_name=graphic_filename(preview_row, preview_choice + 1),
                            mime="image/png", use_container_width=True,
                        )
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for i, graphic_row in enumerate(graphic_rows, 1):
                            archive.writestr(
                                graphic_filename(graphic_row, i),
                                graphic_png(render_team_graphic(graphic_row))
                            )
                    st.download_button(
                        f"Download all {len(graphic_rows)} Instagram graphics (ZIP)",
                        data=zip_buffer.getvalue(),
                        file_name="hbcc_instagram_team_selections.zip",
                        mime="application/zip", use_container_width=True,
                        type="primary"
                    )

            if st.session_state.team_publish_view == "template":
                st.info("Template Manager has moved to the global Admin mode at the top of Content Studio. Log in once to manage both fixture and team selection templates, plus club visuals and aliases.")

            if st.session_state.team_publish_view == "announcement":
                st.markdown("#### Combined team announcement")

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

                with st.expander("Individual team copy cards (for Canva)"):
                    st.markdown("#### Individual team text cards")
                    st.caption(
                        "One copy-ready card per selected match. "
                        "Use the copy icon in each text block to paste into Canva. "
                        "All cards use your edited player lists."
                    )

                    card_columns = st.columns(2, gap="medium")
                    all_card_text = []
                    for index, card_row in enumerate(card_rows):
                        details, player_text, full_card = card_copy_parts(card_row)
                        all_card_text.append(full_card)
                        with card_columns[index % 2]:
                            with st.container(border=True):
                                st.markdown(f"**CARD {index + 1} — {card_row['grade'].upper()}**")
                                full_tab, details_tab, players_tab = st.tabs(
                                    ["Full card", "Match details", "Players only"]
                                )
                                with full_tab:
                                    st.code(full_card, language=None, wrap_lines=True)
                                with details_tab:
                                    st.code(details, language=None, wrap_lines=True)
                                with players_tab:
                                    st.code(player_text, language=None, wrap_lines=True)

                    st.download_button(
                        "Download All Team Cards",
                        data="\n\n".join(
                            f"{'-' * 36}\nCARD {i + 1}\n{'-' * 36}\n{card}"
                            for i, card in enumerate(all_card_text)
                        ),
                        file_name="hbcc_team_cards.txt",
                        mime="text/plain",
                        use_container_width=True,
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
