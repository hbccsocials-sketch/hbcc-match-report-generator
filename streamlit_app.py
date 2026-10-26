
import json
import io
import zipfile
import hmac
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import re
import time
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
                                json=json_data, data=data, params=params, timeout=35)
    if not response.ok:
        raise RuntimeError(f"Supabase {response.status_code}: {response.text[:350]}")
    return response


def sb_rows(table):
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
    conflict_column = "key" if table == "hbcc_settings" else "id"
    response = requests.post(
        root + "/rest/v1/" + table,
        headers=headers,
        params={"on_conflict": conflict_column},
        json=record,
        timeout=35,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase {response.status_code}: {response.text[:350]}")
    return response


def sb_delete(table, field, value):
    sb_request("DELETE", "/rest/v1/" + table, params={field: "eq." + str(value)})


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
    return path


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
                "title": "UPCOMING MATCHES", "title_size": 90, "cards_top": 255,
                "cards_bottom": 1175, "yellow": "#f5b82e", "logo_scale": 1.65,
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
            f"Date: {row['date']}",
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
    date_line = " & ".join(playing_dates) if playing_dates else str(row.get("date") or "Date unavailable")
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


def font_asset(size):
    uploaded = st.session_state.get("graphic_font_bytes")
    if uploaded:
        try:
            return ImageFont.truetype(io.BytesIO(uploaded), size=size)
        except (OSError, ValueError):
            pass
    if DEFAULT_FONT.exists():
        return ImageFont.truetype(str(DEFAULT_FONT), size=size)
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def graphic_opponent(row):
    name = row.get("name", "")
    teams = [part.strip() for part in re.split(r"\s+vs\s+", name, flags=re.I)]
    if len(teams) < 2:
        return name or "Opponent to be confirmed"
    opposing = [team for team in teams if "hawthorn boroondara" not in team.lower()]
    return opposing[0] if opposing else teams[-1]


def graphic_date(row):
    dates = row.get("playing_dates") or [row.get("date", "")]
    return " & ".join(str(d) for d in dict.fromkeys(d for d in dates if d))


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
    title_font = font_asset(title_size)
    detail_font = font_asset(detail_size)
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
    file_path = FIXTURE_LIBRARY_DIR / filename
    if filename and file_path.is_file():
        return file_path.read_bytes()
    return None


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
    paths = [Path(__file__).resolve().parent / "ITC Franklin Gothic LT Medium.otf", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if bold else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for path in paths:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), int(size))
            except OSError:
                pass
    return ImageFont.load_default()


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
    """Paste a large, centre-cropped club visual, clipped to its fixture panel."""
    if not entry:
        return
    raw = fixture_logo_bytes(entry)
    if not raw:
        return
    try:
        logo = Image.open(io.BytesIO(raw)).convert("RGBA")
        # Square club artworks contain their own colour/texture, so preserve the
        # whole image and allow the outer edge to be cropped by the panel.
        logo = logo.resize((int(size), int(size)), Image.Resampling.LANCZOS)
        x, y = center
        left = int(x - logo.width / 2)
        upper = int(y - logo.height / 2)
        if clip_box is None:
            image.paste(logo, (left, upper), logo)
            return
        x0, y0, x1, y1 = map(int, clip_box)
        crop_l = max(0, x0-left)
        crop_t = max(0, y0-upper)
        crop_r = min(logo.width, x1-left)
        crop_b = min(logo.height, y1-upper)
        if crop_r <= crop_l or crop_b <= crop_t:
            return
        part = logo.crop((crop_l, crop_t, crop_r, crop_b))
        image.paste(part, (left+crop_l, upper+crop_t), part)
    except (OSError, ValueError):
        return


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
    top=int(settings.get("cards_top",255));bottom=int(settings.get("cards_bottom",1175))
    # Use a fixed three-slot layout on every page, including pages with 1 or 2 fixtures.
    # This prevents the final page from stretching cards and logos vertically.
    gap=24; card_h=max(185,(bottom-top-gap*2)//3)
    for i,row in enumerate(rows):
        y=top+i*(card_h+gap)
        home=row.get("home_name") or fixture_split_teams(row)[0]
        away=row.get("away_name") or fixture_split_teams(row)[1]
        hc=fixture_find_club(home); ac=fixture_find_club(away)
        d.rounded_rectangle((68,y,1012,y+card_h),radius=24,fill="#0c1b37",outline=yellow,width=3)
        # Crop logos within their own side zones; leave the central details clear.
        size=int(card_h*float(settings.get("logo_scale",1.65)))
        bleed=int(settings.get("logo_bleed",55))
        left_zone=(71,y+4,305,y+card_h-4)
        right_zone=(775,y+4,1009,y+card_h-4)
        # Centre the visual in each side panel, rather than pinning it to the inner edge.
        fixture_logo_paste(background,hc,((left_zone[0]+left_zone[2])/2,y+card_h/2),size,left_zone)
        fixture_logo_paste(background,ac,((right_zone[0]+right_zone[2])/2,y+card_h/2),size,right_zone)
        d=ImageDraw.Draw(background)
        # Semi-opaque centre provides guaranteed legibility, even with vivid club art.
        d.rectangle((305,y+5,775,y+card_h-5),fill="#0c1b37")
        # Club names sit directly beneath their abbreviations in the central
        # text area. Never overlay club names on top of the logo artwork.
        hname=str(hc.get("name") if hc else home)
        aname=str(ac.get("name") if ac else away)
        hab=fixture_abbrev(home,hc); aab=fixture_abbrev(away,ac)
        fixture_center(d,405,y+20,hab,fixture_text_fit(d,hab,155,57,28,True),"#ffffff")
        fixture_center(d,675,y+20,aab,fixture_text_fit(d,aab,155,57,28,True),"#ffffff")
        fixture_center(d,540,y+34,"VS",fixture_font(27,True),yellow)
        fixture_center(d,405,y+83,hname,fixture_text_fit(d,hname,205,17,10,False),"#ffffff")
        fixture_center(d,675,y+83,aname,fixture_text_fit(d,aname,205,17,10,False),"#ffffff")
        date_str=" & ".join(row.get("playing_dates") or [row.get("date","")])
        details=[date_str,str(row.get("venue") or "Venue TBC"),str(row.get("grade") or "")]
        base=y+min(140,max(116,card_h*0.48))
        step=min(33,max(25,(card_h-160)//3))
        for j,line in enumerate(details):
            font=fixture_text_fit(d,line,448,25 if j==0 else 21,14,j==0)
            fixture_center(d,540,base+j*step,line,font,yellow if j==0 else "#ffffff")
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
                "cards_top": 255, "cards_bottom": 1175, "yellow": "#f5b82e",
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
    current = st.session_state.get("fixture_editor_selection", "fixture_default")
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
                                    disabled=not replace, key=prefix + "_upload")
        st.markdown("**Match card layout**")
        st.caption("Your background supplies the heading. These controls position the match cards and logos only.")
        with st.expander("Adjust match cards", expanded=False):
            cards_top = st.slider("First card position", 195, 380, int(initial_settings["cards_top"]),
                                  key=prefix + "_cards_top")
            cards_bottom = st.slider("Match area bottom", 950, 1220,
                                     int(initial_settings["cards_bottom"]), key=prefix + "_cards_bottom")
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
            st.session_state.fixture_editor_selection = template_id
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
                        st.session_state.fixture_editor_selection = fallback["id"]
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


def fixture_club_admin():
    st.markdown("#### Club visuals and aliases")
    st.caption("Edit existing clubs without uploading images again, or add a new club with a visual.")
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
    tab_fixture,tab_team,tab_clubs=st.tabs(["Fixture templates","Team selection templates","Club visuals & aliases"])
    with tab_fixture: fixture_admin()
    with tab_team:
        team_template_admin()
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
    current = st.session_state.get("team_editor_selection", "team_default")
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
                                    type=["png", "jpg", "jpeg"], disabled=not replace,
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
            st.session_state.team_editor_selection = template_id
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
                        st.session_state.team_editor_selection = fallback["id"]
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
    st.caption(f"{len(chosen)} matches selected · Instagram portrait 1080 × 1350 · up to 3 fixtures per graphic")
    if "fixture_graphic_settings" not in st.session_state:
        st.session_state.fixture_graphic_settings = {"title":"UPCOMING MATCHES", "title_size":90, "cards_top":255, "cards_bottom":1175, "yellow":"#f5b82e", "background":"Red to navy gradient", "logo_scale":1.65, "logo_bleed":55}
    fixture_library()
    if "fixture_view" not in st.session_state:
        st.session_state.fixture_view = "graphics"
    st.caption("Templates, logos and aliases can be managed from Admin mode at the top of the app.")
    st.markdown("#### Upcoming fixture graphic generator")
    st.caption("Fixture details come from PlayCricket. You can correct any field before generating. Club logos are matched from your visual directory.")
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
    with st.expander("Fixture adjustments — home/away and details", expanded=False):
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
    missing=sorted({name for row in rows for name in fixture_split_teams(row) if not fixture_find_club(name)})
    if missing:
        st.warning(f"No matching logo for {len(missing)} team name(s): {', '.join(missing[:7])}{'…' if len(missing)>7 else ''}. Text abbreviations will be shown until you add visuals in Template Manager.")
    groups=[rows[i:i+3] for i in range(0,len(rows),3)]
    if not groups:
        st.info("Select at least one fixture."); return
    imgs=[fixture_graphic(group, st.session_state.fixture_graphic_settings) for group in groups]
    st.markdown(f"**{len(imgs)} graphics ready**")
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
        "Upcoming Fixtures"
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
        )
    }

    mode_columns = st.columns(4)

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

                st.caption(
                    mode_descriptions[option]
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
            if mode == "Team Selections"
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
                if not DEFAULT_FONT.exists() and not st.session_state.get("graphic_font_bytes"):
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
