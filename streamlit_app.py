import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title='HBCC Content Studio', page_icon='🏏', layout='wide')
BASE = 'https://grassrootsapiproxy.cricket.com.au/scores'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
GRADES = {
    "Women's 1st XI": ('9b70ae63-142e-492b-b266-f42590204e93', '121436ac-b8db-40c5-9fdc-2bac4439419a'),
    "Men's 1st XI": ('f5b98728-e7b2-40e6-af73-ac2eae56cedf', '94ced5f2-a9f0-4ab3-b8aa-c3d0a643deab'),
    "Men's 2nd XI": ('d67df9f0-a982-461f-b0ee-4aa6bd89b6a6', '74392aeb-a39d-4f11-9d34-8bff6ca4690d'),
}
UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

st.markdown('''<style>
.stApp {background:#f5f7fa;}
.block-container {max-width:1150px;padding-top:2rem;}
.hbhero {background:linear-gradient(110deg,#0f192d,#192f52);padding:30px 36px;border-radius:16px;border-bottom:5px solid #c8102e;margin-bottom:22px;}
.hbhero h1 {color:white!important;margin:5px 0 10px;font-size:2.4rem;}
.hbhero p {color:#e2e7ef;margin:0;}
.hbeyebrow {color:#f5b82e;font-size:12px;font-weight:800;letter-spacing:1.6px;}
.stButton>button[kind="primary"] {background:#c8102e;border-color:#c8102e;color:white;}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="hbhero"><div class="hbeyebrow">HAWTHORN BOROONDARA CRICKET CLUB</div><h1>Content Studio</h1><p>Find fixtures by date and grade. Create match reports, weekend wrap-ups and team selections.</p></div>', unsafe_allow_html=True)


def get_name(obj):
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        for key in ('displayName', 'fullName', 'name', 'teamName', 'playerName', 'participantName', 'shortName', 'title'):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ('player', 'participant', 'team'):
            if isinstance(obj.get(key), dict):
                name = get_name(obj[key])
                if name:
                    return name
    return ''


def parse_date(value):
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        m = re.search(r'\d{4}-\d{2}-\d{2}', value)
        if m:
            try:
                return date.fromisoformat(m.group())
            except ValueError:
                return None
    if isinstance(value, dict):
        for key in ('startDateTime', 'startDate', 'matchDate', 'date', 'scheduledStart', 'scheduledDate', 'startTime', 'start'):
            if key in value:
                d = parse_date(value[key])
                if d:
                    return d
    return None


def date_from_match(obj):
    for key in ('startDateTime', 'startDate', 'matchDate', 'date', 'scheduledStart', 'scheduledDate', 'startTime', 'start', 'matchStartDateTime', 'matchStartDate', 'matchDates', 'schedule'):
        if key in obj:
            d = parse_date(obj[key])
            if d:
                return d
    return None


def id_from_match(obj):
    for key in ('matchId', 'matchID', 'id'):
        value = obj.get(key)
        if isinstance(value, str) and UUID.fullmatch(value):
            return value
    for key in ('match', 'matchSummary'):
        nested = obj.get(key)
        if isinstance(nested, dict):
            value = id_from_match(nested)
            if value:
                return value
    return None


def match_teams(obj):
    for key in ('teams', 'teamSummaries'):
        value = obj.get(key)
        if isinstance(value, list) and value:
            return [v for v in value if isinstance(v, dict)]
    result = []
    for key in ('homeTeam', 'awayTeam', 'homeTeamSummary', 'awayTeamSummary'):
        value = obj.get(key)
        if isinstance(value, dict):
            result.append(value)
    if not result and isinstance(obj.get('matchSummary'), dict):
        return match_teams(obj['matchSummary'])
    return result


def team_id(team):
    return str(team.get('teamId') or team.get('id') or '')


def is_hbcc(team, expected_id=''):
    name = get_name(team).lower()
    return (bool(expected_id) and team_id(team) == expected_id) or 'hawthorn boroondara' in name or name == 'hb hawks'


def fixture_name(obj):
    teams = match_teams(obj)
    names = [get_name(t) for t in teams]
    names = [n for n in names if n]
    if len(names) >= 2:
        return ' vs '.join(names[:2])
    return get_name(obj) or 'Match'


def fixture_venue(obj):
    for key in ('venue', 'ground', 'venueName', 'groundName', 'location'):
        value = obj.get(key)
        if value:
            return get_name(value) or str(value)
    if isinstance(obj.get('matchSummary'), dict):
        return fixture_venue(obj['matchSummary'])
    return ''


def fixture_grade(obj, fallback):
    for key in ('gradeName', 'competitionName', 'grade'):
        value = obj.get(key)
        if value:
            return get_name(value) or fallback
    return fallback


def collect_matches(payload):
    """Walk nested API responses without requiring a particular top-level key."""
    found = {}
    visited = set()

    def walk(obj, depth=0):
        if depth > 16 or id(obj) in visited:
            return
        if isinstance(obj, (dict, list)):
            visited.add(id(obj))
        if isinstance(obj, dict):
            mid = id_from_match(obj)
            if mid:
                # Prefer records with teams and/or dates over ID-only wrappers.
                score = int(bool(match_teams(obj))) * 3 + int(bool(date_from_match(obj))) * 2 + len(obj) / 1000
                old = found.get(mid)
                if old is None or score > old[0]:
                    found[mid] = (score, obj)
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)

    walk(payload)
    return [pair[1] for pair in found.values()]


@st.cache_data(ttl=600, show_spinner=False)
def api_get(path):
    url = BASE + path
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=600, show_spinner=False)
def get_grade_matches(grade_id):
    return api_get(f'/grades/{grade_id}/matches?jsconfig=eccn%3Atrue')


@st.cache_data(ttl=600, show_spinner=False)
def get_match(mid):
    return api_get(f'/matches/{mid}?responseModifier=includeScorecard&jsconfig=eccn%3Atrue')


@st.cache_data(ttl=600, show_spinner=False)
def get_balls(mid):
    return api_get(f'/matches/{mid}/balls?jsconfig=eccn%3Atrue')


def match_url(mid):
    return f'https://play.cricket.com.au/match/{mid}'


def manual_match_ids(raw):
    matches = []
    for token in re.split(r'[\s,]+', raw):
        token = token.strip()
        if not token:
            continue
        m = re.search(r'/match/([0-9a-fA-F-]{36})', token)
        mid = m.group(1) if m else (token if UUID.fullmatch(token) else None)
        if mid and mid not in matches:
            matches.append(mid)
    return matches


def find_hbcc_team(detail, expected_id):
    """Only look in team/selection structures; never derive selection from scorecards."""
    possible = []
    def walk(obj, depth=0):
        if depth > 12:
            return
        if isinstance(obj, dict):
            for key in ('teams', 'teamSummaries', 'homeTeam', 'awayTeam', 'teamSelections', 'lineups', 'lineUps'):
                value = obj.get(key)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and is_hbcc(item, expected_id):
                            possible.append(item)
                elif isinstance(value, dict) and is_hbcc(value, expected_id):
                    possible.append(value)
            for key, value in obj.items():
                if key in ('scorecard', 'batting', 'bowling', 'innings', 'balls', 'deliveries'):
                    continue
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)
    walk(detail)
    return possible


def selected_players(detail, expected_id):
    for team in find_hbcc_team(detail, expected_id):
        for key in ('selectedPlayers', 'players', 'lineup', 'lineUp', 'teamSelection', 'participants', 'selectedParticipants'):
            items = team.get(key)
            if isinstance(items, dict):
                items = next((items.get(k) for k in ('selectedPlayers', 'players', 'participants') if isinstance(items.get(k), list)), None)
            if not isinstance(items, list):
                continue
            names = []
            for item in items:
                name = get_name(item)
                if not name or '*' in name or name.lower() == 'private player':
                    continue
                if isinstance(item, dict):
                    role = str(item.get('role') or item.get('teamRole') or '').lower()
                    captain = item.get('isCaptain') is True or item.get('captain') is True or role == 'captain'
                    if captain and '(c)' not in name.lower():
                        name += ' (c)'
                if name not in names:
                    names.append(name)
            if names:
                return names
    return []


def extract_innings(detail):
    innings = []
    def walk(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get('batting'), list) and isinstance(obj.get('bowling'), list):
                bat = []
                for p in obj['batting']:
                    if not isinstance(p, dict):
                        continue
                    name = get_name(p.get('playerShortName') or p.get('playerName') or p.get('player'))
                    if name and '*' not in name:
                        bat.append({'name': name, 'runs': p.get('runsScored'), 'balls': p.get('ballsFaced'), 'dismissal': p.get('dismissalText')})
                bowl = []
                for p in obj['bowling']:
                    if not isinstance(p, dict):
                        continue
                    name = get_name(p.get('playerShortName') or p.get('playerName') or p.get('player'))
                    if name and '*' not in name:
                        bowl.append({'name': name, 'wickets': p.get('wicketsTaken'), 'runs': p.get('runsConceded'), 'overs': p.get('oversBowled')})
                innings.append({'runs': obj.get('runsScored'), 'wickets': obj.get('numberOfWicketsFallen'), 'batting': bat, 'bowling': bowl})
                return
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(detail)
    return innings


def ball_highlights(payload):
    output = []
    def walk(obj):
        if isinstance(obj, dict):
            if 'overNumber' in obj and 'ballNumber' in obj and 'progressRuns' in obj:
                if obj.get('dismissedParticipantId') or (obj.get('runsBat') or 0) >= 4:
                    output.append({'over': obj.get('overNumber'), 'ball': obj.get('ballNumber'), 'score': obj.get('progressRuns'), 'wickets': obj.get('progressWickets'), 'runs_off_bat': obj.get('runsBat'), 'wicket': bool(obj.get('dismissedParticipantId')), 'striker': obj.get('strikerShortName'), 'bowler': obj.get('bowlerShortName')})
                return
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(payload)
    return output[:120]


def report_payload(fixture):
    mid = fixture['id']
    detail = get_match(mid)
    summary = detail.get('matchSummary', {}) if isinstance(detail, dict) else {}
    data = {'fixture': fixture['name'], 'grade': fixture['grade'], 'date': fixture['date'], 'venue': fixture['venue'], 'result': summary.get('resultText'), 'teams': match_teams(summary) or match_teams(detail), 'scorecard_innings': extract_innings(detail)}
    try:
        data['ball_by_ball_highlights'] = ball_highlights(get_balls(mid))
    except Exception:
        data['ball_by_ball_highlights'] = 'Not available'
    return data


def generate_report(fixtures, mode, length, context, exclusions):
    key = st.secrets.get('GEMINI_API_KEY', '')
    if not key:
        raise RuntimeError('Missing GEMINI_API_KEY in Streamlit Secrets.')
    client = genai.Client(api_key=key)
    payload = [report_payload(f) for f in fixtures]
    prompt = f'''You are the match reporter for Hawthorn Boroondara Cricket Club (HB Hawks). Write a finished {mode.lower()} of approximately {length} words in Australian English. Begin with an engaging headline. Cover every supplied match, important performances, results and momentum. Use chronological narrative when facts support it. For weekend reports, make it a cohesive club-wide story with distinct coverage of each fixture. Do not invent quotes, pitch, weather, tactics, partnerships, players, results or other facts. Never guess private player names. Treat the official result as authoritative; do not invent outcomes for incomplete games. Respect opponents. Extra context: {context}. Avoid: {exclusions}. DATA: {json.dumps(payload, ensure_ascii=False, default=str)}. Return only the complete headline and article.'''
    last_error = None
    for model in ('gemini-2.5-flash', 'gemini-2.5-flash-lite'):
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(temperature=0.35, max_output_tokens=6500))
                if response.text and response.text.strip():
                    return response.text.strip()
            except Exception as exc:
                last_error = exc
                if any(x in str(exc).lower() for x in ('429', '503', 'unavailable', 'resource_exhausted')):
                    time.sleep(2 + 2 * attempt)
                    continue
                if '404' in str(exc) or 'not found' in str(exc).lower():
                    break
                raise
    raise RuntimeError(f'Gemini could not generate the report: {last_error}')


def build_fixture(mid, record, grade, expected_id, detail=None):
    obj = record if isinstance(record, dict) else {}
    full = detail if isinstance(detail, dict) else {}
    summary = full.get('matchSummary', {}) if isinstance(full.get('matchSummary'), dict) else {}
    date_value = date_from_match(obj) or date_from_match(summary) or date_from_match(full)
    name = fixture_name(obj)
    if name == 'Match':
        name = fixture_name(summary)
    if name == 'Match':
        name = fixture_name(full)
    venue = fixture_venue(obj) or fixture_venue(summary) or fixture_venue(full)
    return {'id': mid, 'name': name, 'grade': grade, 'team_id': expected_id, 'date': str(date_value) if date_value else '', 'venue': venue, 'url': match_url(mid)}


def formatted_selections(items):
    lines = ['HAWTHORN BOROONDARA CRICKET CLUB', 'TEAM SELECTIONS', '']
    for item in items:
        lines.extend([item['grade'].upper(), item['name'], ' | '.join(x for x in (item['date'], item['venue']) if x)])
        if item['players']:
            lines.extend(f'{i}. {name}' for i, name in enumerate(item['players'], 1))
        else:
            lines.append('Team selection not available')
        lines.append('')
    return '\n'.join(lines)


with st.sidebar:
    st.header('HBCC grades')
    st.caption('Three grades are already configured. Add others using the PlayCricket grade URL.')
    additional = st.text_area('Additional grades (optional)', placeholder="Men's 3rd XI | GRADE_ID | TEAM_ID", height=100)
    if st.button('Clear PlayCricket cache'):
        st.cache_data.clear()
        st.success('Cache cleared')
    st.caption('If fixtures are missing, expand the search diagnostics after clicking Find fixtures.')

all_grades = dict(GRADES)
for line in additional.splitlines():
    parts = [part.strip() for part in line.split('|')]
    if len(parts) == 3 and UUID.fullmatch(parts[1]) and UUID.fullmatch(parts[2]):
        all_grades[parts[0]] = (parts[1], parts[2])

mode = st.radio('What would you like to create?', ('Match Report', 'Weekend Report', 'Team Selections'), horizontal=True)
left, right = st.columns(2)
with left:
    st.subheader('1. Choose dates')
    date_mode = st.radio('Date mode', ('Date range', 'Individual dates'), horizontal=True, label_visibility='collapsed')
    if date_mode == 'Date range':
        chosen = st.date_input('Match date range', value=(date.today(), date.today() + timedelta(days=1)))
        selected_dates = set()
        if isinstance(chosen, (tuple, list)) and len(chosen) == 2:
            start, end = chosen
            if (end - start).days <= 45:
                selected_dates = {start + timedelta(days=i) for i in range((end - start).days + 1)}
            else:
                st.warning('Please use a date range of 46 days or less.')
    else:
        count = st.number_input('How many dates?', min_value=1, max_value=10, value=1)
        selected_dates = set()
        for i in range(count):
            selected_dates.add(st.date_input(f'Date {i + 1}', value=date.today() + timedelta(days=i), key=f'pick_date_{i}'))
with right:
    st.subheader('2. Choose grades')
    selected_grades = st.multiselect('HBCC grades', list(all_grades), default=list(GRADES))
    st.caption('Choose one or several grades.')

with st.expander('Fallback: add match links manually (optional)'):
    manual_links = st.text_area('One PlayCricket match URL per line', placeholder='https://play.cricket.com.au/match/...', height=90)
    st.caption('Useful if the grade fixture endpoint does not expose the match. Manual matches can still be used in all three generators.')

if 'fixtures' not in st.session_state:
    st.session_state.fixtures = []
if 'diagnostics' not in st.session_state:
    st.session_state.diagnostics = []
if 'article' not in st.session_state:
    st.session_state.article = ''
if 'selection_rows' not in st.session_state:
    st.session_state.selection_rows = []

if st.button('🔎 Find HBCC fixtures', type='primary', use_container_width=True, disabled=not selected_grades or not selected_dates):
    found = {}
    diagnostics = []
    with st.spinner('Searching PlayCricket...'):
        for grade in selected_grades:
            grade_id, expected_id = all_grades[grade]
            try:
                payload = get_grade_matches(grade_id)
                records = collect_matches(payload)
                stats = {'grade': grade, 'api_response_type': type(payload).__name__, 'records_identified': len(records), 'records_without_date': 0, 'records_outside_dates': 0, 'records_other_team': 0, 'matched': 0, 'sample_top_level_keys': list(payload)[:20] if isinstance(payload, dict) else [], 'sample_record_keys': list(records[0])[:25] if records else []}
                for record in records:
                    mid = id_from_match(record)
                    if not mid:
                        continue
                    when = date_from_match(record)
                    detail = None
                    # Fetch detail for records missing their date or team metadata.
                    if not when or not match_teams(record):
                        try:
                            detail = get_match(mid)
                            when = when or date_from_match(detail.get('matchSummary', {})) or date_from_match(detail)
                        except Exception:
                            pass
                    if not when:
                        stats['records_without_date'] += 1
                        continue
                    if when not in selected_dates:
                        stats['records_outside_dates'] += 1
                        continue
                    teams = match_teams(record) or match_teams(detail or {})
                    if teams and not any(is_hbcc(t, expected_id) for t in teams):
                        stats['records_other_team'] += 1
                        continue
                    if mid not in found:
                        found[mid] = build_fixture(mid, record, grade, expected_id, detail)
                        stats['matched'] += 1
                diagnostics.append(stats)
            except Exception as exc:
                diagnostics.append({'grade': grade, 'error': str(exc)})

        for mid in manual_match_ids(manual_links):
            if mid in found:
                continue
            try:
                detail = get_match(mid)
                summary = detail.get('matchSummary', {}) if isinstance(detail, dict) else {}
                teams = match_teams(summary) or match_teams(detail)
                grade = next((g for g in selected_grades if any(is_hbcc(t, all_grades[g][1]) for t in teams)), selected_grades[0])
                fixture = build_fixture(mid, summary or detail, grade, all_grades[grade][1], detail)
                # Explicit manual links are included even if the API doesn't expose a date.
                found[mid] = fixture
            except Exception as exc:
                diagnostics.append({'manual_match_id': mid, 'error': str(exc)})

    st.session_state.fixtures = sorted(found.values(), key=lambda f: (f['date'], f['grade']))
    st.session_state.diagnostics = diagnostics
    st.session_state.article = ''
    st.session_state.selection_rows = []

st.subheader('3. Review fixtures')
fixtures = st.session_state.fixtures
if fixtures:
    st.success(f'{len(fixtures)} fixture(s) found. Choose the matches you want to include.')
    chosen_fixtures = []
    for fixture in fixtures:
        label = ' · '.join(x for x in (fixture['date'], fixture['grade'], fixture['name']) if x)
        if st.checkbox(label, value=True, key='fixture_' + fixture['id']):
            chosen_fixtures.append(fixture)
        st.caption(f"[View match on PlayCricket]({fixture['url']})")
else:
    chosen_fixtures = []
    st.info('No fixtures loaded. Click Find HBCC fixtures. If nothing is found, open the diagnostics below.')

if st.session_state.diagnostics:
    with st.expander('Fixture search diagnostics', expanded=not bool(fixtures)):
        st.json(st.session_state.diagnostics)
        st.caption('These counts help identify whether the API is returning matches, whether dates are missing, or whether the request failed.')

st.divider()
if mode in ('Match Report', 'Weekend Report'):
    st.subheader('4. Generate article')
    if mode == 'Match Report' and chosen_fixtures:
        selected = st.selectbox('Choose one match', chosen_fixtures, format_func=lambda f: f"{f['grade']} — {f['name']} ({f['date']})")
        report_fixtures = [selected]
    else:
        report_fixtures = chosen_fixtures
    context = st.text_area('Additional context (optional)', placeholder='Milestones, debuts, club context...')
    exclusions = st.text_area('Anything to avoid (optional)', height=70)
    length = st.select_slider('Target article length', options=[300, 500, 700, 1000, 1500], value=500 if mode == 'Match Report' else 1000)
    if st.button('Generate ' + mode, type='primary', disabled=not report_fixtures):
        try:
            with st.spinner('Reading scorecards and writing the article...'):
                st.session_state.article = generate_report(report_fixtures, mode, length, context, exclusions)
        except Exception as exc:
            st.error(f'Could not generate article: {exc}')
    if st.session_state.article:
        st.subheader('Your article')
        st.markdown(st.session_state.article)
        st.text_area('Copy the article', st.session_state.article, height=330)
        st.download_button('Download article', st.session_state.article, file_name='hbcc_report.txt', mime='text/plain')
        st.caption('Please verify results and names before publishing.')
else:
    st.subheader('4. Retrieve selected teams')
    if st.button('Retrieve selected teams', type='primary', disabled=not chosen_fixtures):
        rows = []
        with st.spinner('Retrieving published team selections...'):
            for fixture in chosen_fixtures:
                row = dict(fixture)
                row['players'] = []
                row['error'] = ''
                try:
                    detail = get_match(fixture['id'])
                    row['players'] = selected_players(detail, fixture['team_id'])
                    row['venue'] = row['venue'] or fixture_venue(detail)
                except Exception as exc:
                    row['error'] = str(exc)
                rows.append(row)
        st.session_state.selection_rows = rows

    if st.session_state.selection_rows:
        edited_rows = []
        for row in st.session_state.selection_rows:
            with st.container(border=True):
                st.markdown(f"**{row['grade']} — {row['name']}**")
                st.caption(' · '.join(x for x in (row['date'], row['venue']) if x))
                if row['error']:
                    st.warning('Could not read selection: ' + row['error'])
                elif not row['players']:
                    st.warning('No published HBCC players found in the supported API fields. Check the match page; you can enter names below.')
                player_text = st.text_area('Players (one per line)', value='\n'.join(row['players']), height=180, key='players_' + row['id'])
                edited = dict(row)
                edited['players'] = [line.strip() for line in player_text.splitlines() if line.strip()]
                edited_rows.append(edited)
                st.markdown(f"[Verify on PlayCricket]({row['url']})")
        output = formatted_selections(edited_rows)
        st.subheader('Combined team announcement')
        st.text_area('Copy-ready selections', output, height=340)
        st.download_button('Download selections', output, file_name='hbcc_team_selections.txt', mime='text/plain')
        st.caption('Teams may be partially published or change before play. Verify the final lists.')

st.markdown('---')
st.caption('HBCC Content Studio · Verify all generated content against PlayCricket before publishing.')
