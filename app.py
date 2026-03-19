import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageEnhance, ImageFilter
import io
import re
import json
import time
import datetime
import pandas as pd
import difflib
import genanki
from gtts import gTTS
import tempfile
import os
import random
import hashlib
import html as html_lib
import functools
import requests
import http.cookiejar
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

# ── YouTube transcript imports ────────────────────────────────────────────────
try:
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        TranscriptsDisabled,
        NoTranscriptFound,
    )
    from youtube_transcript_api.formatters import TextFormatter as YTTextFormatter
    YOUTUBE_AVAILABLE = True
except ImportError:
    YOUTUBE_AVAILABLE = False

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# ── Session State Initialization ──────────────────────────────────────────────
if 'generated_cards' not in st.session_state: st.session_state['generated_cards'] = []
if 'preview_page'    not in st.session_state: st.session_state['preview_page']    = 0
if 'audio_cache'     not in st.session_state: st.session_state['audio_cache']     = {}
if 'last_api_call'   not in st.session_state: st.session_state['last_api_call']   = 0.0
if 'apkg_cache'      not in st.session_state: st.session_state['apkg_cache']      = None
if 'apkg_hash'       not in st.session_state: st.session_state['apkg_hash']       = None
if 'undo_stack'      not in st.session_state: st.session_state['undo_stack']      = []
if 'card_filter'     not in st.session_state: st.session_state['card_filter']     = ""
if 'cookie_path'     not in st.session_state: st.session_state['cookie_path']     = None

# ── Constants ─────────────────────────────────────────────────────────────────
TRACKER_FILE = "rpd_tracker.json"
_COOKIE_PATH = "/tmp/yt_cookies.txt"

# ── Compiled regex patterns ───────────────────────────────────────────────────
_RE_HTML_TAGS     = re.compile(r'<[^>]+>')
_RE_WHITESPACE    = re.compile(r'\s+')
_RE_CAPTIONS      = re.compile(r'"captionTracks"\s*:\s*(\[.*?\])')
_RE_ACCESS_DENIED = re.compile(r'Access Denied|edgesuite\.net|Reference #')

# Strings that indicate the scrape returned an error page, not real captions.
# If the cleaned text contains any of these, we reject it and move to next stage.
_ERROR_SIGNATURES = [
    "we're sorry",
    "youtube is currently blocking",
    "fetching subtitles",
    "working on a fix",
    "access denied",
    "sign in to confirm",
    "before you continue",
]

# ── Transcript-specific prompt suffix ────────────────────────────────────────
TRANSCRIPT_SUFFIX = (
    "\n\nINPUT TYPE: Raw video transcript/subtitles. "
    "IGNORE filler words ('um', 'uh', 'you know', 'like', 'right', 'okay', 'so'), "
    "self-corrections, off-topic tangents, sponsor segments, and channel plugs. "
    "Focus ONLY on factual claims, definitions, step-by-step explanations, and "
    "clearly stated concepts. Reconstruct fragmented or run-on sentences into "
    "coherent, atomic flashcard content. "
    "If a concept is repeated multiple times in the transcript, generate only ONE "
    "card for it — pick the clearest phrasing. "
    "Do NOT make cards about the speaker's opinions, jokes, or anecdotes unless "
    "they directly illustrate a factual concept."
)


def load_rpd():
    today = str(datetime.date.today())
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, 'r') as f:
            data = json.load(f)
            if data.get('date') == today:
                return data.get('calls', 0)
    return 0

def increment_rpd(calls=1):
    today   = str(datetime.date.today())
    current = load_rpd() + calls
    with open(TRACKER_FILE, 'w') as f:
        json.dump({'date': today, 'calls': current}, f)
    return current

if 'rpd_used' not in st.session_state:
    st.session_state['rpd_used'] = load_rpd()

# ── MathJax CDN + Full CSS ────────────────────────────────────────────────────
st.markdown("""
    <script>
    MathJax = {
      tex: {
        inlineMath: [['\\\\(', '\\\\)']],
        displayMath: [['\\\\[', '\\\\]']],
        packages: {'[+]': ['mhchem']}
      },
      loader: { load: ['[tex]/mhchem'] },
      options: { skipHtmlTags: ['script','noscript','style','textarea','pre'] }
    };
    </script>
    <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
            id="MathJax-script" async></script>

    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    .anki-preview-container {
        background-color: #272828; color: #e2e2e2; border-radius: 12px; padding: 20px;
        border: 2px solid #444; margin-bottom: 15px;
        font-family: 'Inter', Arial, sans-serif; text-align: center; font-size: 18px;
    }
    .anki-preview-container hr   { border: 0; border-top: 1px solid #555; margin: 15px 0; }
    .anki-preview-answer         { color: #00aaff; font-weight: 600; }
    .anki-preview-context        { color: #aaa; font-size: 0.85em; padding-top: 10px;
                                   font-style: italic; border-top: 1px dashed #555; margin-top: 15px; }
    .anki-preview-mcq            { text-align: left; display: inline-block; margin: 10px auto;
                                   background: rgba(255,255,255,0.05); padding: 15px;
                                   border-radius: 8px; border: 1px solid #444; }
    .anki-preview-meta           { font-size: 0.72em; text-align: right; opacity: 0.6; margin-bottom: 6px; }
    .conf-dot                    { display: inline-block; width: 10px; height: 10px;
                                   border-radius: 50%; margin-right: 5px; vertical-align: middle; }
    .conf-high { background: #4caf50; } .conf-med { background: #ff9800; } .conf-low { background: #f44336; }
    .yt-cookie-box               { background: #1a1a2e; border: 1px solid #4a4a8a; border-radius: 8px;
                                   padding: 10px; font-size: 12px; color: #aaa; margin-top: 6px; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { border: 1px solid #555; padding: 8px; text-align: left; }
    </style>
""", unsafe_allow_html=True)

# ================================================
# CORE HELPERS & SAFEGUARDS
# ================================================
def enforce_api_delay():
    elapsed       = time.time() - st.session_state['last_api_call']
    required_wait = 13.0
    if elapsed < required_wait:
        with st.spinner(f"⏳ Rate Limit Safeguard: Waiting {required_wait - elapsed:.1f}s…"):
            time.sleep(required_wait - elapsed)
    st.session_state['last_api_call'] = time.time()

def check_rpd_preflight(req_needed):
    remaining = 20 - st.session_state['rpd_used']
    if req_needed > remaining:
        return False, (
            f"🚨 This batch needs **{req_needed}** requests but only **{remaining}** remain today. "
            f"Reduce input size or wait until tomorrow."
        )
    if remaining > 0 and req_needed / remaining >= 0.75:
        return True, (
            f"⚠️ This will consume **{req_needed}/{remaining}** remaining requests "
            f"({int(req_needed / remaining * 100)}% of your quota)."
        )
    return True, None

def push_undo(cards):
    import copy
    st.session_state['undo_stack'].append(copy.deepcopy(cards))
    if len(st.session_state['undo_stack']) > 3:
        st.session_state['undo_stack'].pop(0)

def smart_chunk_text(text, max_chars=50000):
    chunks = []
    while len(text) > max_chars:
        split_idx = text.rfind('\n\n', 0, max_chars)
        if split_idx == -1: split_idx = text.rfind('. ', 0, max_chars)
        if split_idx == -1: split_idx = text.rfind(' ',  0, max_chars)
        if split_idx == -1: split_idx = max_chars
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    if text:
        chunks.append(text)
    return chunks

def enhance_image(img):
    img = img.convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    img.thumbnail((768, 768))
    return img

def markdown_to_html(text):
    text = str(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__',     r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*',     r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_',       r'<i>\1</i>', text)
    return text

def is_duplicate(new_q, existing_cards, threshold=0.85):
    for c in existing_cards:
        if difflib.SequenceMatcher(None, new_q.lower(), str(c['Question']).lower()).ratio() > threshold:
            return True
    return False

def get_confidence_dot(score):
    try:
        s = int(score)
        if s >= 80:   cls, label = "conf-high", "High"
        elif s >= 50: cls, label = "conf-med",  "Medium"
        else:         cls, label = "conf-low",  "Low"
        return f'<span class="conf-dot {cls}" title="{label} confidence ({s}%)"></span>'
    except:
        return ''

def get_confidence_tag(score):
    try:
        s = int(score)
        if s >= 80:   return "confidence_high"
        elif s >= 50: return "confidence_med"
        else:         return "confidence_low"
    except:
        return "confidence_unknown"

# ================================================
# YOUTUBE TRANSCRIPT ENGINE
# ================================================

@functools.lru_cache(maxsize=64)
def extract_youtube_id(url: str):
    patterns = [
        r'(?:v=)([\w-]{11})',
        r'(?:youtu\.be/)([\w-]{11})',
        r'(?:embed/)([\w-]{11})',
        r'(?:shorts/)([\w-]{11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    if re.match(r'^[\w-]{11}$', url.strip()):
        return url.strip()
    return None

def _is_error_content(text: str) -> bool:
    """
    Returns True if the scraped text is actually a YouTube error page
    rather than real caption content.

    This is the root cause of the 'We're sorry, YouTube is currently
    blocking us...' text appearing in the transcript box: the scrape
    successfully fetched a page, but that page was an error response.
    YouTube strips HTML tags, leaving just the error message text, which
    our code was incorrectly returning as a valid transcript.
    """
    lowered = text.lower()
    return any(sig in lowered for sig in _ERROR_SIGNATURES)

def _build_session_with_cookies(cookie_path: str = None) -> requests.Session:
    """
    Builds a requests.Session with a Chrome User-Agent, YouTube consent
    cookie, and optionally the user-supplied Netscape cookies.txt.

    Centralised here so both Stage 1 and Stage 2 of the scrape use
    identical auth — previously they used different request methods so
    only one stage could benefit from the uploaded cookies.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    session.cookies.set("CONSENT", "YES+cb.20210328-17-p0.en+FX+478")

    if cookie_path and os.path.exists(cookie_path):
        cj = http.cookiejar.MozillaCookieJar()
        try:
            cj.load(cookie_path, ignore_discard=True, ignore_expires=True)
            session.cookies.update(cj)
        except Exception:
            pass  # malformed file — proceed unauthenticated

    return session

def get_youtube_api(cookie_path: str = None):
    """
    Returns a fresh YouTubeTranscriptApi instance on every call.

    @st.cache_resource is intentionally NOT used here.
    If it were, Streamlit would permanently cache the first instance
    (typically unauthenticated) and never replace it, even after the user
    uploads cookies — because Streamlit's resource cache lives in process
    memory for the app lifetime.  Since this is a cheap object to create,
    we skip caching here and let @st.cache_data on get_youtube_transcript
    handle the expensive work of actually fetching and storing transcripts.
    """
    if not YOUTUBE_AVAILABLE:
        return None
    if cookie_path and os.path.exists(cookie_path):
        try:
            return YouTubeTranscriptApi(cookies=cookie_path)
        except TypeError:
            pass  # older library build without cookies kwarg
    return YouTubeTranscriptApi()

def _scrape_youtube_transcript(video_id: str, cookie_path: str = None) -> str:
    """
    Multi-stage HTML scrape fallback — ALL stages use the same authenticated
    session built from cookie_path so cookies bypass bot-blocking at every level.

    Previously cookie_path was only used in Plan A (the library).
    When Plan A failed and fell through to the scrape, the scrape ran
    unauthenticated, YouTube returned an error HTML page, and the error
    message text was returned as the 'transcript'.  This function fixes that
    by (a) passing cookies through the session and (b) rejecting any result
    that looks like an error page via _is_error_content().

    Stage 1 — Parse captionTracks from raw YouTube watch-page HTML.
    Stage 2 — Public proxy (youtubetranscript.com).
    """
    session = _build_session_with_cookies(cookie_path)

    # ── Stage 1 ───────────────────────────────────────────────────────────
    try:
        page_html = session.get(
            f"https://www.youtube.com/watch?v={video_id}", timeout=12
        ).text
        m = re.search(r'"captionTracks"\s*:\s*(\[.*?\])', page_html)
        if m:
            xml_url  = json.loads(m.group(1))[0]['baseUrl']
            xml_resp = session.get(xml_url, timeout=10)
            cleaned  = _RE_HTML_TAGS.sub(' ', xml_resp.text)
            cleaned  = html_lib.unescape(cleaned)
            cleaned  = _RE_WHITESPACE.sub(' ', cleaned).strip()
            if cleaned and not _is_error_content(cleaned):
                return cleaned
    except Exception:
        pass

    # ── Stage 2: public proxy ─────────────────────────────────────────────
    try:
        proxy   = session.get(
            f"https://youtubetranscript.com/?server_vid2={video_id}", timeout=12
        )
        cleaned = _RE_HTML_TAGS.sub(' ', proxy.text)
        cleaned = html_lib.unescape(cleaned)
        cleaned = _RE_WHITESPACE.sub(' ', cleaned).strip()
        if ('<transcript>' in proxy.text or '<?xml' in proxy.text) and not _is_error_content(cleaned):
            return cleaned
    except Exception:
        pass

    raise ValueError(
        "All extraction methods failed. "
        "The video may have no closed captions, or YouTube is blocking requests. "
        "Upload your YouTube cookies.txt in the sidebar to authenticate requests."
    )

@st.cache_data(ttl=3600, show_spinner=False)
def get_youtube_transcript(video_id: str, cookie_path: str = None) -> str:
    """
    Primary transcript engine — cached 1 hour per (video_id, cookie_path).

    Keying on cookie_path means (video_id, None) and (video_id, '/tmp/...')
    are separate cache entries.  Uploading cookies clears the old unauthenticated
    result and forces a fresh authenticated fetch.

    Plan A  youtube-transcript-api library with fresh cookie-aware instance
    Plan B  HTML scrape with MozillaCookieJar authentication (Stage 1 + 2)
    """
    if YOUTUBE_AVAILABLE:
        try:
            ytt             = get_youtube_api(cookie_path)
            transcript_list = ytt.list(video_id)
            try:
                transcript = transcript_list.find_transcript(['en'])
            except Exception:
                transcript = next(iter(transcript_list))
            fetched   = transcript.fetch()
            formatter = YTTextFormatter()
            return formatter.format_transcript(fetched)
        except Exception:
            pass

    return _scrape_youtube_transcript(video_id, cookie_path)

# ================================================
# ANKI .APKG EXPORT ENGINE
# ================================================
ANKI_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
.card { font-family: 'Inter', Arial, sans-serif; font-size: 20px; text-align: center;
        color: black; background-color: white; }
.card.nightMode { background-color: #272828; color: #e2e2e2; }
.context { font-size: 16px; color: #777; margin-top: 20px; font-style: italic;
           border-top: 1px solid #ccc; padding-top: 10px; }
.card.nightMode .context { color: #aaa; border-top: 1px solid #555; }
.mcq-options { text-align: left; display: inline-block; margin: 15px auto; padding: 15px;
               border: 1px solid #ccc; border-radius: 8px; background-color: #fafafa; }
.card.nightMode .mcq-options { background-color: #333; border: 1px solid #555; }
.mcq-answer { color: #00aaff; font-weight: bold; }
.conf-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:5px; }
.conf-high { background:#4caf50; } .conf-med { background:#ff9800; } .conf-low { background:#f44336; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
.card.nightMode th, .card.nightMode td { border: 1px solid #555; }
"""

BASIC_MODEL_ID = 1607392319
CLOZE_MODEL_ID = 1607392320
MCQ_MODEL_ID   = 1607392321

anki_basic_model = genanki.Model(
    BASIC_MODEL_ID, 'AI Anki PRO',
    fields=[{'name': 'Question'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}, {'name': 'Confidence'}],
    templates=[{'name': 'Card 1',
        'qfmt': '{{Question}}',
        'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}<br><br>{{Audio}}<div class="context">{{Context}}</div>'}],
    css=ANKI_CSS
)
anki_cloze_model = genanki.Model(
    CLOZE_MODEL_ID, 'AI Anki Cloze', model_type=genanki.Model.CLOZE,
    fields=[{'name': 'Text'}, {'name': 'Context'}, {'name': 'Audio'}, {'name': 'Confidence'}],
    templates=[{'name': 'Cloze',
        'qfmt': '{{cloze:Text}}',
        'afmt': '{{cloze:Text}}<br><br>{{Audio}}<div class="context">{{Context}}</div>'}],
    css=ANKI_CSS
)
anki_mcq_model = genanki.Model(
    MCQ_MODEL_ID, 'AI Anki MCQ',
    fields=[{'name': 'Question'}, {'name': 'Options'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}, {'name': 'Confidence'}],
    templates=[{'name': 'MCQ Card',
        'qfmt': '{{Question}}<br><br><div class="mcq-options">{{Options}}</div>',
        'afmt': '{{Question}}<br><br><div class="mcq-options">{{Options}}</div>'
                '<hr id="answer"><span class="mcq-answer">{{Answer}}</span>'
                '<br><br>{{Audio}}<div class="context">{{Context}}</div>'}],
    css=ANKI_CSS
)

def generate_apkg(cards, deck_name, include_audio, lang_code):
    deck_id = int(hashlib.sha256(deck_name.encode('utf-8')).hexdigest(), 16) % (10**10)
    deck    = genanki.Deck(deck_id, deck_name)
    media_files = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for c in cards:
            audio_field = ""
            if include_audio:
                try:
                    text_to_read = c['Answer'] if c.get('Answer') else c['Question']
                    clean_text   = re.sub(r'<[^>]+>|\\\[|\\\]|\\\(|\\\)', '', str(text_to_read)).strip()
                    if clean_text:
                        cache_key = hash(clean_text + lang_code)
                        filename  = f"audio_{cache_key}.mp3"
                        filepath  = os.path.join(tmpdir, filename)
                        if cache_key in st.session_state['audio_cache']:
                            with open(filepath, 'wb') as f:
                                f.write(st.session_state['audio_cache'][cache_key])
                        else:
                            tts = gTTS(clean_text, lang=lang_code)
                            tts.save(filepath)
                            with open(filepath, 'rb') as f:
                                st.session_state['audio_cache'][cache_key] = f.read()
                        media_files.append(filepath)
                        audio_field = f"[sound:{filename}]"
                except:
                    pass

            tags     = [t.strip().replace("#", "") for t in str(c.get('Tags', '')).split() if t.strip()]
            conf_str = str(c.get('Confidence', ''))
            conf_tag = get_confidence_tag(c.get('Confidence', 0))
            if conf_tag not in tags:
                tags.append(conf_tag)

            if c.get('Options'):
                note = genanki.Note(model=anki_mcq_model,
                    fields=[str(c['Question']), str(c['Options']), str(c['Answer']),
                            str(c['Context']), audio_field, conf_str], tags=tags)
            elif "{{c" in str(c['Question']):
                note = genanki.Note(model=anki_cloze_model,
                    fields=[str(c['Question']), str(c['Context']), audio_field, conf_str], tags=tags)
            else:
                note = genanki.Note(model=anki_basic_model,
                    fields=[str(c['Question']), str(c['Answer']),
                            str(c['Context']), audio_field, conf_str], tags=tags)
            deck.add_note(note)

        package             = genanki.Package(deck)
        package.media_files = media_files
        temp_apkg           = os.path.join(tmpdir, "export.apkg")
        package.write_to_file(temp_apkg)
        with open(temp_apkg, "rb") as f:
            return f.read()

# ================================================
# SUPER-BATCH PROMPT LOGIC
# ================================================
BASE_SYSTEM_INSTRUCTION = """You are an expert Anki professor processing a massive batch of inputs.

CHEMISTRY/MATH RULES: Use Native Anki MathJax for ALL math. Inline: \\( ... \\), Block: \\[ ... \\]
Chemistry: Use \\ce{...} for equations natively inside MathJax.

CARD RULES:
- Facts must be atomic.
- [REVERSE CARDS]: If definition, generate Term->Def and Def->Term.
- [BREVITY]: Answers < 15 words.
- [CONTEXT]: Elaboration goes here. Format tabular data using HTML <table>, <tr>, <td> tags.
- [HIGHLIGHT]: Wrap the single most critical keyword in the 'answer' field with <span style="color: #ffeb3b;">.
- [DEDUPLICATION]: Do NOT generate multiple cards for the exact same concept. Ensure maximum conceptual diversity.

CRITICAL: Return a strictly valid JSON array of objects matching the schema below.
EXAMPLE SCHEMA:
[{"question": "What is the primary function of the mitochondria?", "answer": "Cellular respiration and <span style='color: #ffeb3b;'>ATP production</span>.", "context": "The mitochondria is a double-membrane-bound organelle found in most eukaryotic organisms.", "distractors": ["Protein synthesis", "Lipid breakdown", "Photosynthesis"], "suggested_tags": ["Biology", "Cell_Structure"], "confidence_score": 99}]
"""

def extract_partial_json(text_response):
    cards   = []
    matches = re.findall(r'\{[^{}]*\}', text_response)
    for match in matches:
        try:
            card = json.loads(match)
            if 'question' in card and 'answer' in card:
                cards.append(card)
        except json.JSONDecodeError:
            pass
    return cards

@retry(wait=wait_exponential(multiplier=2, min=15, max=60),
       stop=stop_after_attempt(3),
       retry=retry_if_exception_type(Exception),
       reraise=True)
def _call_gemini(model, content):
    return model.generate_content(content)

def process_super_batch(payloads, model, prompt_suffix, is_image=True):
    enforce_api_delay()
    full_prompt = f"{prompt_suffix}\nExtract flashcards from ALL provided {'images' if is_image else 'text'}."
    content     = payloads + [full_prompt] if is_image else [full_prompt, payloads]
    response    = _call_gemini(model, content)
    clean_json  = re.sub(r'```json|```', '', response.text).strip()
    try:
        return json.loads(clean_json)
    except json.JSONDecodeError:
        st.warning("⚠️ API output hit token limits — salvaging valid cards…")
        return extract_partial_json(clean_json)

# ================================================
# SIDEBAR CONFIGURATION
# ================================================
with st.sidebar:
    st.title("⚙️ Configuration")

    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ API Key loaded from secrets")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    subject = st.text_input("Subject (use :: for sub-decks):", value="Science::Biology")
    if "::" in subject:
        parts = [p.strip() for p in subject.split("::") if p.strip()]
        st.caption("📂 Deck hierarchy: " + " → ".join(parts))

    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual"])
    LANG_MAP  = {"English": "en", "Bahasa Indonesia": "id", "Bilingual": "id"}
    current_lang_code = LANG_MAP.get(language, "en")

    st.divider()
    cloze_mode = st.checkbox("Enable Cloze Deletions")
    mcq_mode   = st.checkbox("Enable Multiple Choice (MCQ)")

    st.divider()
    st.subheader("🎯 Quality Filter")
    min_confidence = st.slider("Min. Confidence Score", 0, 100, 0, 5,
        help="Cards below this threshold are hidden from preview AND excluded from export.")

    st.divider()

    # ── YouTube Cookie Upload ─────────────────────────────────────────────
    st.subheader("🍪 YouTube Cookies")
    st.caption(
        "Upload `cookies.txt` (Netscape format) exported from your browser. "
        "Cookies are passed to **every** stage of transcript extraction — "
        "the library, the page scrape, and the XML fetch — so YouTube sees "
        "an authenticated request at all levels."
    )
    cookie_file = st.file_uploader("cookies.txt", type=["txt"],
                                   key="yt_cookie_upload", label_visibility="collapsed")

    if cookie_file is not None:
        with open(_COOKIE_PATH, "wb") as fh:
            fh.write(cookie_file.getvalue())
        st.session_state["cookie_path"] = _COOKIE_PATH
        # Clear transcript cache so the next request is a fresh authenticated
        # fetch rather than returning a previously cached unauthenticated result.
        get_youtube_transcript.clear()
        st.success("✅ Cookies loaded — all extraction stages are now authenticated.")

    elif st.session_state.get("cookie_path") and os.path.exists(_COOKIE_PATH):
        st.success("✅ Cookies active (session)")
        if st.button("🗑️ Remove Cookies", key="remove_cookies"):
            os.remove(_COOKIE_PATH)
            st.session_state["cookie_path"] = None
            get_youtube_transcript.clear()
            st.rerun()
    else:
        st.markdown(
            '<div class="yt-cookie-box">No cookies loaded — some videos may be blocked.</div>',
            unsafe_allow_html=True
        )

    st.divider()
    st.subheader("📊 API Quota Tracker")
    rpd_val = st.session_state['rpd_used']
    st.progress(min(rpd_val / 20.0, 1.0))
    st.markdown(f"**Used:** {rpd_val} / 20 &nbsp;|&nbsp; **Remaining:** {max(0, 20 - rpd_val)}")
    quota_reached = (rpd_val >= 20)
    if quota_reached:
        st.error("🚨 Daily Limit Reached. App is in Read-Only mode.")

    st.divider()
    st.subheader("📂 Session Management")
    session_file = st.file_uploader("Load Session (.json)", type=['json'])
    if session_file:
        try:
            loaded = json.load(session_file)
            if isinstance(loaded, list):
                push_undo(st.session_state['generated_cards'])
                st.session_state['generated_cards'] = loaded
                st.session_state['apkg_cache']      = None
                st.success(f"✅ Loaded {len(loaded)} cards from session.")
                st.rerun()
            else:
                st.error("Invalid session file format.")
        except Exception as e:
            st.error(f"Failed to load session: {e}")

    if st.button("🗑️ Reset Memory"):
        st.session_state['generated_cards'] = []
        st.session_state['audio_cache']     = {}
        st.session_state['apkg_cache']      = None
        st.session_state['undo_stack']      = []
        st.rerun()

# ================================================
# MODEL & PROMPT ASSEMBLY
# ================================================
st.title("🎓 AI Anki Generator PRO")

instruction = BASE_SYSTEM_INSTRUCTION
if cloze_mode:
    instruction += (
        "\nCLOZE MODE: 'question' must contain {{c1::...}}. "
        "Occlude ONLY the highest-yield noun/concept."
    )
if mcq_mode:
    instruction += (
        "\nMULTIPLE CHOICE MODE: Provide exactly 3 realistic wrong answers in 'distractors'. "
        "Distractors must be contextually similar to the correct answer."
    )
if language == "Bilingual":
    instruction += (
        "\nBILINGUAL MODE: Write 'question' in English and 'answer' entirely in Bahasa Indonesia. "
        "Include 'context' in both languages, separated by ' | '."
    )

model = None
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash-lite',
        system_instruction=instruction,
        generation_config=genai.GenerationConfig(response_mime_type="application/json")
    )

prompt_suffix = f"Subject: {subject}. Language: {language}."
tab_img, tab_txt, tab_yt = st.tabs(["📸 Image Super-Batch", "📝 Text / Notes", "▶️ YouTube URL"])

def append_cards_from_response(cards):
    for card in cards:
        new_q    = card.get('question', '')
        mcq_html = ""
        if mcq_mode and card.get('distractors'):
            options = [card.get('answer')] + card.get('distractors')
            random.shuffle(options)
            for j, opt in enumerate(options[:4]):
                mcq_html += f"<b>{'ABCD'[j]})</b> {opt}<br>"
        if not is_duplicate(new_q, st.session_state['generated_cards']):
            st.session_state['generated_cards'].append({
                "Question":   markdown_to_html(new_q),
                "Options":    mcq_html,
                "Answer":     markdown_to_html(card.get('answer', '')),
                "Context":    markdown_to_html(card.get('context', '')),
                "Tags":       f"#AI_Generated {' '.join(card.get('suggested_tags', []))}",
                "Confidence": card.get('confidence_score', 0)
            })

# ================================================
# TAB 1 — IMAGE SUPER-BATCH
# ================================================
with tab_img:
    uploaded_files = st.file_uploader("Upload Images (Groups of 10 max)",
                                      type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)
    if uploaded_files:
        with st.expander("🖼️ Preview Uploaded Images"):
            cols = st.columns(5)
            for i, f in enumerate(uploaded_files):
                cols[i % 5].image(f, use_column_width=True)

        if api_key:
            req_needed = max(1, (len(uploaded_files) + 9) // 10)
            ok, preflight_msg = check_rpd_preflight(req_needed)
            if preflight_msg: (st.warning if ok else st.error)(preflight_msg)
            st.info(f"ℹ️ {len(uploaded_files)} image(s) → **{req_needed} API Request(s)**.")

            if st.button("🚀 Generate from Images", type="primary", disabled=(quota_reached or not ok)):
                push_undo(st.session_state['generated_cards'])
                with st.status(f"Processing {req_needed} batch(es)…", expanded=True) as status:
                    for i in range(0, len(uploaded_files), 10):
                        chunk         = uploaded_files[i:i + 10]
                        target_cards  = min(30, len(chunk) * 5)
                        adaptive_sfx  = prompt_suffix + f" Generate approximately {target_cards} diverse, atomic flashcards."
                        try:
                            cards = process_super_batch([enhance_image(Image.open(f)) for f in chunk],
                                                        model, adaptive_sfx, is_image=True)
                            st.session_state['rpd_used'] = increment_rpd(1)
                            append_cards_from_response(cards)
                            st.write(f"✅ Batch {i//10 + 1}: {len(cards)} cards generated.")
                        except Exception as e:
                            st.error(f"Batch {i//10 + 1} Error: {e}")
                    status.update(label="✅ Image Processing Finished!", state="complete")
                st.rerun()

# ================================================
# TAB 2 — TEXT / NOTES
# ================================================
with tab_txt:
    pasted_text = st.text_area("Paste Lecture Notes, Transcripts, or PDF Text:", height=200)
    if pasted_text and api_key:
        text_chunks = smart_chunk_text(pasted_text)
        req_needed  = len(text_chunks)
        ok, preflight_msg = check_rpd_preflight(req_needed)
        if preflight_msg: (st.warning if ok else st.error)(preflight_msg)
        st.info(f"ℹ️ {len(pasted_text):,} chars → **{req_needed} chunk(s)** → **{req_needed} API Request(s)**.")

        if st.button("🚀 Generate from Text", type="primary", disabled=(quota_reached or not ok)):
            push_undo(st.session_state['generated_cards'])
            with st.status("Processing text chunks…", expanded=True) as status:
                for idx_c, chunk in enumerate(text_chunks):
                    target_cards = min(40, max(5, len(chunk) // 300))
                    adaptive_sfx = prompt_suffix + f" Generate approximately {target_cards} diverse, atomic flashcards."
                    try:
                        cards = process_super_batch(chunk, model, adaptive_sfx, is_image=False)
                        st.session_state['rpd_used'] = increment_rpd(1)
                        append_cards_from_response(cards)
                        st.write(f"✅ Chunk {idx_c + 1}/{req_needed}: {len(cards)} cards generated.")
                    except Exception as e:
                        st.error(f"Chunk {idx_c + 1} Error: {e}")
                status.update(label="✅ Text Processing Finished!", state="complete")
            st.rerun()

# ================================================
# TAB 3 — YOUTUBE URL
# ================================================
with tab_yt:
    st.subheader("▶️ Generate Cards from a YouTube Video")
    st.caption("Paste any YouTube link. The transcript is extracted automatically and fed to the AI.")

    yt_url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...", key="yt_url_input")

    if yt_url:
        vid_id = extract_youtube_id(yt_url)
        if vid_id: st.caption(f"🎬 Detected video ID: `{vid_id}`")
        else:      st.warning("⚠️ Could not detect a valid YouTube video ID in that URL.")

    if st.session_state.get("cookie_path") and os.path.exists(_COOKIE_PATH):
        st.success("🍪 Cookies active — authenticated requests will be used at every extraction stage.")
    else:
        st.info("💡 **Tip:** Upload `cookies.txt` in the sidebar to bypass YouTube bot-blocking.")

    col_fetch, col_gen = st.columns(2)
    with col_fetch:
        fetch_btn = st.button("📥 Extract Transcript", type="secondary",
                              disabled=(not yt_url), use_container_width=True)

    if fetch_btn and yt_url:
        vid_id = extract_youtube_id(yt_url)
        if not vid_id:
            st.error("Invalid YouTube URL — could not parse a video ID.")
        else:
            with st.spinner("Fetching transcript… (library → authenticated scrape → proxy)"):
                try:
                    cookie_path = st.session_state.get("cookie_path")
                    transcript  = get_youtube_transcript(vid_id, cookie_path)
                    st.session_state["yt_transcript"]  = transcript
                    st.session_state["yt_current_vid"] = vid_id
                    st.success(f"✅ Transcript extracted! ({len(transcript):,} characters)")
                except Exception as e:
                    st.session_state.pop("yt_transcript",  None)
                    st.session_state.pop("yt_current_vid", None)
                    st.error(f"Transcript extraction failed: {e}")
                    st.info("Upload your `cookies.txt` in the sidebar to authenticate and bypass blocking.")

    if "yt_transcript" in st.session_state:
        with st.expander("📄 Preview & Edit Transcript", expanded=True):
            edited_transcript = st.text_area(
                "Clean up or trim the transcript before generating cards:",
                value=st.session_state["yt_transcript"],
                height=250, key="yt_transcript_editor"
            )

        yt_chunks  = smart_chunk_text(edited_transcript)
        req_needed = len(yt_chunks)
        ok, preflight_msg = check_rpd_preflight(req_needed)
        if preflight_msg: (st.warning if ok else st.error)(preflight_msg)
        st.info(f"ℹ️ {len(edited_transcript):,} chars → **{req_needed} chunk(s)** → **{req_needed} API Request(s)**.")

        with col_gen:
            gen_btn = st.button("🚀 Generate Cards from Transcript", type="primary",
                                disabled=(quota_reached or not ok or not api_key),
                                use_container_width=True)

        if gen_btn:
            if not api_key:
                st.error("Gemini API key required — enter it in the sidebar.")
            else:
                push_undo(st.session_state['generated_cards'])
                with st.status(f"Processing {req_needed} chunk(s)…", expanded=True) as status:
                    total_new = 0
                    for idx_c, chunk in enumerate(yt_chunks):
                        target_cards = min(40, max(5, len(chunk) // 300))
                        adaptive_sfx = (
                            prompt_suffix + TRANSCRIPT_SUFFIX
                            + f" Generate approximately {target_cards} diverse, atomic flashcards."
                        )
                        try:
                            cards  = process_super_batch(chunk, model, adaptive_sfx, is_image=False)
                            st.session_state['rpd_used'] = increment_rpd(1)
                            before = len(st.session_state['generated_cards'])
                            append_cards_from_response(cards)
                            added      = len(st.session_state['generated_cards']) - before
                            total_new += added
                            st.write(f"✅ Chunk {idx_c + 1}/{req_needed}: {len(cards)} generated, {added} added.")
                        except Exception as e:
                            st.error(f"Chunk {idx_c + 1} Error: {e}")
                    status.update(label=f"✅ Done! {total_new} new cards added.", state="complete")
                st.session_state.pop("yt_transcript",  None)
                st.session_state.pop("yt_current_vid", None)
                st.rerun()

# ================================================
# CARD MANAGEMENT — PREVIEW, EDIT & EXPORT
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    all_cards = st.session_state['generated_cards']

    undo_col, _ = st.columns([1, 5])
    with undo_col:
        if st.session_state['undo_stack']:
            if st.button("↩️ Undo Last Batch"):
                st.session_state['generated_cards'] = st.session_state['undo_stack'].pop()
                st.session_state['apkg_cache']      = None
                st.rerun()

    total       = len(all_cards)
    mcq_count   = sum(1 for c in all_cards if c.get('Options'))
    cloze_count = sum(1 for c in all_cards if "{{c" in str(c.get('Question', '')))
    avg_conf    = int(sum(int(c.get('Confidence', 0) or 0) for c in all_cards) / max(total, 1))
    all_tags    = {t.strip() for c in all_cards for t in str(c.get('Tags', '')).split() if t.strip()}

    indexed_all      = list(enumerate(all_cards))
    filtered_indexed = [(i, c) for i, c in indexed_all
                        if int(c.get('Confidence', 0) or 0) >= min_confidence]

    st.markdown("#### 📊 Deck Statistics")
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Total Cards",     total)
    s2.metric("After Filter",    len(filtered_indexed))
    s3.metric("MCQ / Cloze",     f"{mcq_count} / {cloze_count}")
    s4.metric("Avg. Confidence", f"{avg_conf}%")
    s5.metric("Unique Tags",     len(all_tags))

    filter_query = st.text_input("🔍 Filter cards by keyword:",
                                 value=st.session_state['card_filter'])
    st.session_state['card_filter'] = filter_query
    if filter_query:
        fq = filter_query.lower()
        filtered_indexed = [(i, c) for i, c in filtered_indexed
                            if fq in str(c.get('Question', '')).lower()
                            or fq in str(c.get('Answer',   '')).lower()
                            or fq in str(c.get('Tags',     '')).lower()]
        st.caption(f"Showing {len(filtered_indexed)} card(s) matching '{filter_query}'.")

    filtered_cards = [c for _, c in filtered_indexed]

    df        = pd.DataFrame(all_cards)
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    st.session_state['generated_cards'] = edited_df.fillna("").to_dict('records')

    with st.expander("🏷️ Bulk Tag Manager"):
        b1, b2, b3 = st.columns(3)
        bulk_tag   = b1.text_input("Tag (e.g., #Exam1):").replace(" ", "_")
        if b2.button("➕ Add to All") and bulk_tag:
            for c in st.session_state['generated_cards']:
                if bulk_tag not in c['Tags']: c['Tags'] += f" {bulk_tag}"
            st.rerun()
        if b3.button("➖ Remove from All") and bulk_tag:
            for c in st.session_state['generated_cards']:
                c['Tags'] = c['Tags'].replace(bulk_tag, "").strip()
            st.rerun()

    st.subheader("👀 Night Mode Card Preview")
    if not filtered_cards:
        st.info("No cards match current filter / confidence threshold.")
    else:
        max_page = max(1, (len(filtered_cards) + 4) // 5)
        page     = st.number_input("Preview Page", min_value=1, max_value=max_page, step=1) - 1

        for slot, (real_idx, c) in enumerate(filtered_indexed[page*5 : page*5 + 5]):
            conf_dot  = get_confidence_dot(c.get('Confidence', 0))
            mcq_block = (f"<div class='anki-preview-mcq'>{c.get('Options', '')}</div>"
                         if c.get('Options') else "")
            st.markdown(f"""
                <div class="anki-preview-container">
                    <div class="anki-preview-meta">{conf_dot} Confidence: {c.get('Confidence', 'N/A')}</div>
                    <div>{c['Question']}</div>{mcq_block}<hr>
                    <div class="anki-preview-answer">{c['Answer']}</div>
                    <div class="anki-preview-context">{c['Context']}</div>
                </div>""", unsafe_allow_html=True)

            p1, p2 = st.columns([1, 4])
            with p1:
                if st.button("🔊 Listen", key=f"tts_{real_idx}_{slot}"):
                    clean_ans = re.sub(r'<[^>]+>|\\\[|\\\]|\\\(|\\\)', '', str(c['Answer']))
                    tts_obj   = gTTS(clean_ans, lang=current_lang_code)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                        tts_obj.save(fp.name); st.audio(fp.name)
            with p2:
                if st.button("🗑️ Delete Card", key=f"del_{real_idx}_{slot}"):
                    st.session_state['generated_cards'].pop(real_idx)
                    st.session_state['apkg_cache'] = None
                    st.rerun()

    st.divider()
    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("📦 Finalize Deck")
        include_audio = st.toggle("Include Answer TTS in Export", value=True)
        export_cards  = filtered_cards if (filter_query or min_confidence > 0) else all_cards
        if min_confidence > 0 or filter_query:
            st.caption(f"ℹ️ Exporting **{len(export_cards)}** filtered card(s) of {total} total.")

        current_data_hash = hash(str(export_cards) + str(include_audio) + subject)
        if st.session_state['apkg_hash'] != current_data_hash or st.session_state['apkg_cache'] is None:
            if st.button("⚡ Compile Anki Deck"):
                with st.spinner("Compiling media and packaging deck…"):
                    apkg = generate_apkg(export_cards, subject, include_audio, current_lang_code)
                    st.session_state['apkg_cache'] = apkg
                    st.session_state['apkg_hash']  = current_data_hash
                    st.rerun()
        else:
            st.success("✅ Compilation complete!")
            st.download_button("💾 Download .apkg", st.session_state['apkg_cache'],
                file_name=f"{subject.replace('::', '_')}.apkg",
                mime="application/octet-stream", use_container_width=True)

    with col2:
        st.subheader("📄 CSV Backup")
        st.download_button("📥 Download CSV", df.to_csv(index=False),
            file_name=f"{subject.replace('::', '_')}.csv",
            mime="text/csv", use_container_width=True)

    with col3:
        st.subheader("💾 Save Session")
        session_json = json.dumps(st.session_state['generated_cards'], indent=2, ensure_ascii=False)
        st.download_button("📤 Export Session (.json)", session_json,
            file_name=f"{subject.replace('::', '_')}_session.json",
            mime="application/json", use_container_width=True)
        st.caption("Reload this file via the sidebar to resume your session later.")
