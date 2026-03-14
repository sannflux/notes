import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageEnhance, ImageFilter
import csv
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
import uuid
from filelock import FileLock

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# Initialize Session State
if 'generated_cards' not in st.session_state: st.session_state['generated_cards'] = []
if 'preview_page' not in st.session_state: st.session_state['preview_page'] = 0
if 'audio_cache' not in st.session_state: st.session_state['audio_cache'] = {}
if 'last_api_call' not in st.session_state: st.session_state['last_api_call'] = 0.0
if 'apkg_cache' not in st.session_state: st.session_state['apkg_cache'] = None
if 'apkg_hash' not in st.session_state: st.session_state['apkg_hash'] = None
if 'active_key_index' not in st.session_state: st.session_state['active_key_index'] = 0

# Idea 21: Filelock Quota Management
TRACKER_FILE = "rpd_tracker.json"
LOCK_FILE = "rpd_tracker.lock"

def get_key_hash(api_key):
    return hashlib.md5(api_key.strip().encode()).hexdigest()[:8]

def load_rpd(api_key):
    key_id = get_key_hash(api_key)
    today = str(datetime.date.today())
    with FileLock(LOCK_FILE):
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, 'r') as f:
                data = json.load(f)
                day_data = data.get(today, {})
                return day_data.get(key_id, 0)
    return 0

def increment_rpd(api_key):
    key_id = get_key_hash(api_key)
    today = str(datetime.date.today())
    with FileLock(LOCK_FILE):
        data = {}
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, 'r') as f: data = json.load(f)
        
        if today not in data: data = {today: {}}
        current = data[today].get(key_id, 0) + 1
        data[today][key_id] = current
        
        with open(TRACKER_FILE, 'w') as f: json.dump(data, f)
    return current

# ================================================
# CORE HELPERS & SAFEGUARDS
# ================================================
def enforce_api_delay():
    elapsed = time.time() - st.session_state['last_api_call']
    required_wait = 13.0 
    if elapsed < required_wait:
        wait_time = required_wait - elapsed
        with st.spinner(f"⏳ API Limit Protection: Waiting {wait_time:.1f}s..."):
            time.sleep(wait_time)
    st.session_state['last_api_call'] = time.time()

def smart_chunk_text(text, max_chars=50000):
    chunks = []
    # Idea 24: Pre-flight Token Estimation
    est_tokens = len(text) // 4
    if est_tokens > 100000:
        st.toast(f"⚠️ Large Input: ~{est_tokens} tokens. Processing may take time.", icon="⏳")
        
    while len(text) > max_chars:
        split_idx = text.rfind('\n\n', 0, max_chars)
        if split_idx == -1: split_idx = text.rfind('. ', 0, max_chars)
        if split_idx == -1: split_idx = max_chars
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    if text: chunks.append(text)
    return chunks

def enhance_image(img, num_files):
    # Idea 23: Dynamic Downscaling
    img = img.convert("RGB")
    target_res = (512, 512) if num_files > 5 else (768, 768)
    img.thumbnail(target_res)
    enhancer = ImageEnhance.Contrast(img)
    return enhancer.enhance(2.5).filter(ImageFilter.SHARPEN)

def markdown_to_html(text):
    text = str(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    return text

# ================================================
# ANKI ENGINE (with Progress Bar Idea 34)
# ================================================
ANKI_CSS = """
.card { font-family: 'Inter', Arial; font-size: 20px; text-align: center; color: black; background-color: white; }
.card.nightMode { background-color: #272828; color: #e2e2e2; }
.context { font-size: 16px; color: #777; margin-top: 20px; font-style: italic; border-top: 1px solid #ccc; padding-top: 10px; }
.card.nightMode .context { color: #aaa; border-top: 1px solid #555; }
.mcq-answer { color: #00aaff; font-weight: bold; }
"""

def generate_apkg(cards, deck_name, include_audio, lang_code):
    deck_id = int(hashlib.sha256(deck_name.encode()).hexdigest(), 16) % (10**10) 
    deck = genanki.Deck(deck_id, deck_name)
    media_files = []
    
    progress_bar = st.progress(0, text="Starting Media Generation...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, c in enumerate(cards):
            progress_bar.progress((idx+1)/len(cards), text=f"Processing Card {idx+1}/{len(cards)}...")
            audio_field = ""
            if include_audio:
                try:
                    text_to_read = c['Answer'] or c['Question']
                    clean_text = re.sub(r'<[^>]+>|\\\[|\\\]|\\\(|\\\)', '', str(text_to_read)).strip()
                    if clean_text:
                        cache_key = hash(clean_text + lang_code)
                        filename = f"audio_{cache_key}.mp3"
                        filepath = os.path.join(tmpdir, filename)
                        if cache_key in st.session_state['audio_cache']:
                            with open(filepath, 'wb') as f: f.write(st.session_state['audio_cache'][cache_key])
                        else:
                            tts = gTTS(clean_text, lang=lang_code)
                            tts.save(filepath)
                            with open(filepath, 'rb') as f: st.session_state['audio_cache'][cache_key] = f.read()
                        media_files.append(filepath)
                        audio_field = f"[sound:{filename}]"
                except: pass 
            
            tags = [t.strip().replace("#", "") for t in str(c['Tags']).split() if t.strip()]
            
            # Simple models (Preserved logic)
            model = genanki.Model(1607392319, 'AI Anki', fields=[{'name': 'Q'}, {'name': 'A'}, {'name': 'C'}, {'name': 'S'}],
                                  templates=[{'name': 'C1', 'qfmt': '{{Q}}', 'afmt': '{{FrontSide}}<hr>{{A}}<br>{{S}}<div class="context">{{C}}</div>'}], css=ANKI_CSS)
            
            note = genanki.Note(model=model, fields=[str(c['Question']), str(c['Answer']), str(c['Context']), audio_field], tags=tags)
            deck.add_note(note)

        package = genanki.Package(deck)
        package.media_files = media_files
        temp_apkg = os.path.join(tmpdir, "export.apkg")
        package.write_to_file(temp_apkg)
        progress_bar.empty()
        with open(temp_apkg, "rb") as f: return f.read()

# ================================================
# SIDEBAR & MULTI-KEY (Idea 22)
# ================================================
with st.sidebar:
    st.title("⚙️ Config & Quota")
    raw_keys = st.text_input("Gemini API Keys (comma separated):", type="password")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    
    if api_keys:
        current_key = api_keys[st.session_state['active_key_index'] % len(api_keys)]
        rpd_val = load_rpd(current_key)
        
        # Auto-cycle logic
        if rpd_val >= 20 and len(api_keys) > 1:
            st.session_state['active_key_index'] += 1
            st.rerun()
            
        st.success(f"Key {st.session_state['active_key_index']+1} Active")
        st.progress(min(rpd_val / 20.0, 1.0))
        st.caption(f"Used: {rpd_val}/20 RPD on current key")
    else:
        current_key = None
        st.warning("Please enter at least one API Key")

    subject = st.text_input("Deck Name:", value="My Deck")
    complexity = st.select_slider("Target Level:", options=["ELI5", "High School", "College", "PhD"], value="College") # Idea 29
    language = st.selectbox("Mode:", ["English", "Bahasa Indonesia", "Bilingual (Translation)"]) # Idea 32
    cloze_mode = st.checkbox("Cloze Deletion")
    mcq_mode = st.checkbox("MCQ (Socratic)") # Idea 30

# ================================================
# PROMPT ENGINE
# ================================================
BASE_SYSTEM_INSTRUCTION = f"""Expert Anki Professor. Complexity: {complexity}.
- Rules: LaTeX via \\( \\) and \\[ \\]. No duplicate concepts.
- Context: If MCQ, briefly explain why distractors are wrong (Socratic).
- Bilingual Mode: If active, put native language on Front, Target on Back with phonetic guides.
"""

def process_super_batch(payloads, model, is_image=True):
    enforce_api_delay()
    content = payloads + ["Generate JSON flashcards."] if is_image else [payloads]
    response = model.generate_content(content)
    increment_rpd(current_key)
    try:
        return json.loads(re.sub(r'```json|```', '', response.text).strip())
    except:
        return []

# ================================================
# MAIN UI
# ================================================
st.title("🎓 AI Anki Generator PRO")

if current_key:
    genai.configure(api_key=current_key)
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash-lite', 
        system_instruction=BASE_SYSTEM_INSTRUCTION,
        generation_config=genai.GenerationConfig(response_mime_type="application/json")
    )

    t1, t2 = st.tabs(["📸 Images", "📝 Text"])
    with t1:
        files = st.file_uploader("Upload (Max 10)", accept_multiple_files=True)
        if files:
            with st.expander("🖼️ Preview"):
                cols = st.columns(5)
                for i, f in enumerate(files): cols[i%5].image(f)
            if st.button("Generate from Images"):
                cards = process_super_batch([enhance_image(Image.open(f), len(files)) for f in files], model)
                for c in cards:
                    # Idea 33: Stable UUID
                    c['id'] = str(uuid.uuid4())
                    st.session_state['generated_cards'].append({
                        "id": c['id'],
                        "Question": markdown_to_html(c.get('question', '')),
                        "Answer": markdown_to_html(c.get('answer', '')),
                        "Context": markdown_to_html(c.get('context', '')),
                        "Tags": "#AI_Generated"
                    })
                st.rerun()

    with t2:
        txt = st.text_area("Paste Notes:")
        if st.button("Generate from Text") and txt:
            for chunk in smart_chunk_text(txt):
                cards = process_super_batch(chunk, model, is_image=False)
                for c in cards:
                    c['id'] = str(uuid.uuid4())
                    st.session_state['generated_cards'].append({
                        "id": c['id'],
                        "Question": markdown_to_html(c.get('question', '')),
                        "Answer": markdown_to_html(c.get('answer', '')),
                        "Context": markdown_to_html(c.get('context', '')),
                        "Tags": "#AI_Generated"
                    })
            st.rerun()

if st.session_state['generated_cards']:
    # Idea 37: Collapsible Editor
    with st.expander("📝 Edit Generated Cards", expanded=True):
        df = pd.DataFrame(st.session_state['generated_cards'])
        edited_df = st.data_editor(df, key="main_editor", use_container_width=True)
        st.session_state['generated_cards'] = edited_df.to_dict('records')

    # Export Area
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Compile .apkg"):
            apkg = generate_apkg(st.session_state['generated_cards'], subject, True, "en")
            st.download_button("Download Now", apkg, file_name=f"{subject}.apkg")
    with c2:
        if st.button("🗑️ Clear All"):
            st.session_state['generated_cards'] = []
            st.rerun()

