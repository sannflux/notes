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

# Persistent RPD (Requests Per Day) Tracker
TRACKER_FILE = "rpd_tracker.json"
def load_rpd():
    today = str(datetime.date.today())
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, 'r') as f:
            data = json.load(f)
            if data.get('date') == today: return data.get('calls', 0)
    return 0

def increment_rpd(calls=1):
    today = str(datetime.date.today())
    current = load_rpd() + calls
    with open(TRACKER_FILE, 'w') as f:
        json.dump({'date': today, 'calls': current}, f)
    return current

if 'rpd_used' not in st.session_state:
    st.session_state['rpd_used'] = load_rpd()

# Premium Fonts & Adaptive CSS (Includes Idea 20: Anki Night Mode Preview Override)
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    
    /* Native Streamlit Elements */
    .tag-pill { background: rgba(0, 170, 255, 0.2); color: #00aaff; padding: 2px 10px; border-radius: 15px; font-size: 0.8em; }
    
    /* True Anki Night Mode Preview */
    .anki-preview-container { 
        background-color: #272828; 
        color: #e2e2e2; 
        border-radius: 12px; 
        padding: 20px; 
        border: 2px solid #444; 
        margin-bottom: 15px; 
        font-family: 'Inter', Arial, sans-serif; 
        text-align: center;
        font-size: 18px;
    }
    .anki-preview-container hr { border: 0; border-top: 1px solid #555; margin: 15px 0; }
    .anki-preview-answer { color: #00aaff; font-weight: 600; }
    .anki-preview-context { color: #aaa; font-size: 0.85em; padding-top: 10px; font-style: italic; border-top: 1px dashed #555; margin-top: 15px; }
    .anki-preview-mcq { text-align: left; display: inline-block; margin: 10px auto; background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; border: 1px solid #444; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { border: 1px solid #555; padding: 8px; text-align: left; }
    </style>
""", unsafe_allow_html=True)

# ================================================
# CORE HELPERS & SAFEGUARDS
# ================================================
def enforce_api_delay():
    """Idea 1: Strict Token Bucket Delay for Free Tier 5 RPM"""
    elapsed = time.time() - st.session_state['last_api_call']
    required_wait = 13.0 # Strict 13s gap for safety
    if elapsed < required_wait:
        wait_time = required_wait - elapsed
        with st.spinner(f"⏳ API Rate Limit Safeguard: Waiting {wait_time:.1f}s..."):
            time.sleep(wait_time)
    st.session_state['last_api_call'] = time.time()

def smart_chunk_text(text, max_chars=50000):
    """Idea 5: Semantic Chunking to preserve context"""
    chunks = []
    while len(text) > max_chars:
        split_idx = text.rfind('\n\n', 0, max_chars)
        if split_idx == -1: split_idx = text.rfind('. ', 0, max_chars)
        if split_idx == -1: split_idx = text.rfind(' ', 0, max_chars)
        if split_idx == -1: split_idx = max_chars
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    if text: chunks.append(text)
    return chunks

def enhance_image(img):
    img = img.convert("RGB")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    img.thumbnail((768, 768)) # Token downscaling
    return img

def markdown_to_html(text):
    text = str(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    return text

def is_duplicate(new_q, existing_cards, threshold=0.85):
    for c in existing_cards:
        if difflib.SequenceMatcher(None, new_q.lower(), str(c['Question']).lower()).ratio() > threshold: return True
    return False

# ================================================
# ANKI .APKG EXPORT ENGINE
# ================================================
ANKI_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
.card { font-family: 'Inter', Arial, sans-serif; font-size: 20px; text-align: center; color: black; background-color: white; }
.card.nightMode { background-color: #272828; color: #e2e2e2; }
.context { font-size: 16px; color: #777; margin-top: 20px; font-style: italic; border-top: 1px solid #ccc; padding-top: 10px; }
.card.nightMode .context { color: #aaa; border-top: 1px solid #555; }
.mcq-options { text-align: left; display: inline-block; margin: 15px auto; padding: 15px; border: 1px solid #ccc; border-radius: 8px; background-color: #fafafa; }
.card.nightMode .mcq-options { background-color: #333; border: 1px solid #555; }
.mcq-answer { color: #00aaff; font-weight: bold; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
.card.nightMode th, .card.nightMode td { border: 1px solid #555; }
"""

BASIC_MODEL_ID = 1607392319
CLOZE_MODEL_ID = 1607392320
MCQ_MODEL_ID = 1607392321

anki_basic_model = genanki.Model(
    BASIC_MODEL_ID, 'AI Anki PRO', fields=[{'name': 'Question'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{'name': 'Card 1', 'qfmt': '{{Question}}', 'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}<br><br>{{Audio}}<div class="context">{{Context}}</div>'}], css=ANKI_CSS
)

anki_cloze_model = genanki.Model(
    CLOZE_MODEL_ID, 'AI Anki Cloze', model_type=genanki.Model.CLOZE, fields=[{'name': 'Text'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{'name': 'Cloze', 'qfmt': '{{cloze:Text}}', 'afmt': '{{cloze:Text}}<br><br>{{Audio}}<div class="context">{{Context}}</div>'}], css=ANKI_CSS
)

anki_mcq_model = genanki.Model(
    MCQ_MODEL_ID, 'AI Anki MCQ', fields=[{'name': 'Question'}, {'name': 'Options'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{'name': 'MCQ Card', 
                'qfmt': '{{Question}}<br><br><div class="mcq-options">{{Options}}</div>', 
                'afmt': '{{Question}}<br><br><div class="mcq-options">{{Options}}</div><hr id="answer"><span class="mcq-answer">{{Answer}}</span><br><br>{{Audio}}<div class="context">{{Context}}</div>'}], 
    css=ANKI_CSS
)

def generate_apkg(cards, deck_name, include_audio, lang_code):
    # Idea 6: Deterministic Hash for permanent Deck ID
    deck_id = int(hashlib.sha256(deck_name.encode('utf-8')).hexdigest(), 16) % (10**10) 
    deck = genanki.Deck(deck_id, deck_name)
    media_files = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        for c in cards:
            audio_field = ""
            if include_audio:
                try:
                    text_to_read = c['Answer'] if (c.get('Answer')) else c['Question']
                    clean_text = re.sub(r'<[^>]+>', '', str(text_to_read))
                    clean_text = re.sub(r'\\\[|\\\]|\\\(|\\\)', '', clean_text).strip()
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
                        
                        # Idea 9: Only active audio appended (No orphaned media bloat)
                        media_files.append(filepath) 
                        audio_field = f"[sound:{filename}]"
                except: pass 
            
            tags = [t.strip().replace("#", "") for t in str(c['Tags']).split() if t.strip()]
            
            # Idea 7: Native MCQ routing
            if c.get('Options'):
                note = genanki.Note(model=anki_mcq_model, fields=[str(c['Question']), str(c['Options']), str(c['Answer']), str(c['Context']), audio_field], tags=tags)
            elif "{{c" in str(c['Question']): 
                note = genanki.Note(model=anki_cloze_model, fields=[str(c['Question']), str(c['Context']), audio_field], tags=tags)
            else: 
                note = genanki.Note(model=anki_basic_model, fields=[str(c['Question']), str(c['Answer']), str(c['Context']), audio_field], tags=tags)
            deck.add_note(note)

        package = genanki.Package(deck)
        package.media_files = media_files
        temp_apkg = os.path.join(tmpdir, "export.apkg")
        package.write_to_file(temp_apkg)
        with open(temp_apkg, "rb") as f: return f.read()

# ================================================
# SUPER-BATCH PROMPT LOGIC
# ================================================
# Ideas 8, 11, 14: Prompt Upgrades (Native JSON, MathJax strict, Deduplication, Few-Shot)
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
    cards = []
    matches = re.findall(r'\{[^{}]*\}', text_response)
    for match in matches:
        try:
            card = json.loads(match)
            if 'question' in card and 'answer' in card: cards.append(card)
        except json.JSONDecodeError: pass
    return cards

def process_super_batch(payloads, model, prompt_suffix, is_image=True):
    enforce_api_delay() # Safeguard Trigger
    full_prompt = f"{prompt_suffix}\nExtract flashcards from ALL provided {'images' if is_image else 'text'}."
    content = payloads + [full_prompt] if is_image else [full_prompt, payloads]
    
    response = model.generate_content(content)
    clean_json = re.sub(r'```json|```', '', response.text).strip()
    
    try: return json.loads(clean_json)
    except json.JSONDecodeError:
        st.warning("API output reached token limits. Salvaging structurally valid cards...")
        return extract_partial_json(clean_json)

# ================================================
# SIDEBAR CONFIGURATION
# ================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ API Key loaded")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")
    
    subject = st.text_input("Subject (use :: for sub-decks):", value="Science::Biology")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual"])
    LANG_MAP = {"English": "en", "Bahasa Indonesia": "id", "Bilingual": "id"}
    current_lang_code = LANG_MAP.get(language, "en")

    st.divider()
    cloze_mode = st.checkbox("Enable Cloze Deletions")
    mcq_mode = st.checkbox("Enable Multiple Choice (MCQ)") 
    
    st.divider()
    st.subheader("📊 API Quota Tracker")
    rpd_val = st.session_state['rpd_used']
    st.progress(min(rpd_val / 20.0, 1.0))
    st.markdown(f"**Used Today:** {rpd_val} / 20 Requests")
    
    # Idea 18: Safe Read-Only Mode Switch
    quota_reached = (rpd_val >= 20)
    if quota_reached:
        st.error("🚨 Daily Limit Exceeded. App is now in Edit/Export Read-Only mode.")
    
    st.divider()
    if st.button("🗑️ Reset Memory"):
        st.session_state['generated_cards'] = []
        st.session_state['audio_cache'] = {}
        st.session_state['apkg_cache'] = None
        st.rerun()

# ================================================
# MAIN PROCESSING 
# ================================================
st.title("🎓 AI Anki Generator PRO")

instruction = BASE_SYSTEM_INSTRUCTION
# Ideas 12 & 13: Prompt Enhancements for specific modes
if cloze_mode: instruction += "\nCLOZE MODE: 'question' must contain {{c1::...}}. Occlude ONLY the highest-yield noun/concept (e.g., 'The mitochondria is the {{c1::powerhouse}}', not 'The {{c1::mitochondria}} is the powerhouse')."
if mcq_mode: instruction += "\nMULTIPLE CHOICE MODE: You MUST provide exactly 3 realistic wrong answers in the 'distractors' array. Distractors must be contextually and computationally similar to the real answer to prevent obvious guessing."

model = None
if api_key:
    genai.configure(api_key=api_key)
    # Idea 3: Native JSON via generation_config
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash-lite', 
        system_instruction=instruction,
        generation_config=genai.GenerationConfig(response_mime_type="application/json")
    )

prompt_suffix = f"Subject: {subject}. Language: {language}."
tab_img, tab_txt = st.tabs(["📸 Image Super-Batch", "📝 Text/Notes Super-Batch"])

# TAB 1: IMAGES
with tab_img:
    uploaded_files = st.file_uploader("Upload Images (Groups of 10 max)", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)
    if uploaded_files:
        # Idea 19: Thumbnail Upload Preview Expander
        with st.expander("🖼️ Preview Uploaded Images"):
            cols = st.columns(5)
            for i, f in enumerate(uploaded_files): cols[i%5].image(f, use_column_width=True)

        if api_key:
            req_needed = max(1, len(uploaded_files) // 10 + (1 if len(uploaded_files) % 10 > 0 else 0))
            st.info(f"ℹ️ Sending {len(uploaded_files)} images consumes **{req_needed} API Request(s)**.")
            
            if st.button("🚀 Generate from Images", type="primary", disabled=quota_reached):
                with st.status(f"Processing in {req_needed} super-batch request(s)...", expanded=True) as status:
                    chunk_size = 10
                    for i in range(0, len(uploaded_files), chunk_size):
                        chunk = uploaded_files[i:i + chunk_size]
                        processed_imgs = [enhance_image(Image.open(f)) for f in chunk]
                        
                        try:
                            cards = process_super_batch(processed_imgs, model, prompt_suffix, is_image=True)
                            st.session_state['rpd_used'] = increment_rpd(1)
                            
                            for card in cards:
                                new_q = card.get('question', '')
                                mcq_html = ""
                                if mcq_mode and card.get('distractors'):
                                    options = [card.get('answer')] + card.get('distractors')
                                    random.shuffle(options)
                                    letters = ['A', 'B', 'C', 'D', 'E']
                                    for j, opt in enumerate(options[:4]): mcq_html += f"<b>{letters[j]})</b> {opt}<br>"

                                if not is_duplicate(new_q, st.session_state['generated_cards']):
                                    st.session_state['generated_cards'].append({
                                        "Question": markdown_to_html(new_q),
                                        "Options": mcq_html,
                                        "Answer": markdown_to_html(card.get('answer', '')),
                                        "Context": markdown_to_html(card.get('context', '')),
                                        "Tags": f"#AI_Generated {' '.join(card.get('suggested_tags', []))}",
                                        "Confidence": card.get('confidence_score', 0)
                                    })
                        except Exception as e: st.error(f"Batch Error: {str(e)}")
                    status.update(label="✅ Image Processing Finished!", state="complete")
                    st.rerun()

# TAB 2: TEXT
with tab_txt:
    pasted_text = st.text_area("Paste Lecture Notes, Transcripts, or PDF Text:", height=200)
    if pasted_text and api_key:
        text_chunks = smart_chunk_text(pasted_text) # Idea 5 implementation
        req_needed = len(text_chunks)
        st.info(f"ℹ️ Sending {len(pasted_text)} characters chunked smartly consumes **{req_needed} API Request(s)**.")
        
        if st.button("🚀 Generate from Text", type="primary", disabled=quota_reached):
            with st.status("Processing Text in massive chunks...", expanded=True) as status:
                for chunk in text_chunks:
                    try:
                        cards = process_super_batch(chunk, model, prompt_suffix, is_image=False)
                        st.session_state['rpd_used'] = increment_rpd(1)
                        
                        for card in cards:
                            new_q = card.get('question', '')
                            mcq_html = ""
                            if mcq_mode and card.get('distractors'):
                                options = [card.get('answer')] + card.get('distractors')
                                random.shuffle(options)
                                letters = ['A', 'B', 'C', 'D', 'E']
                                for j, opt in enumerate(options[:4]): mcq_html += f"<b>{letters[j]})</b> {opt}<br>"

                            if not is_duplicate(new_q, st.session_state['generated_cards']):
                                st.session_state['generated_cards'].append({
                                    "Question": markdown_to_html(new_q),
                                    "Options": mcq_html,
                                    "Answer": markdown_to_html(card.get('answer', '')),
                                    "Context": markdown_to_html(card.get('context', '')),
                                    "Tags": f"#AI_Generated {' '.join(card.get('suggested_tags', []))}",
                                    "Confidence": card.get('confidence_score', 0)
                                })
                    except Exception as e: st.error(f"Text Error: {str(e)}")
                status.update(label="✅ Text Processing Finished!", state="complete")
                st.rerun()

# ================================================
# PREVIEW, EDITS & TTS
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    df = pd.DataFrame(st.session_state['generated_cards'])
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    st.session_state['generated_cards'] = edited_df.fillna("").to_dict('records')

    with st.expander("🏷️ Bulk Tag Manager"):
        b_col1, b_col2, b_col3 = st.columns(3)
        bulk_tag = b_col1.text_input("Tag (e.g., #Exam1):").replace(" ", "_")
        if b_col2.button("➕ Add to All") and bulk_tag:
            for c in st.session_state['generated_cards']:
                if bulk_tag not in c['Tags']: c['Tags'] += f" {bulk_tag}"
            st.rerun()
        if b_col3.button("➖ Remove from All") and bulk_tag:
            for c in st.session_state['generated_cards']: c['Tags'] = c['Tags'].replace(bulk_tag, "").strip()
            st.rerun()

    st.subheader("👀 Night Mode Card Preview")
    total = len(st.session_state['generated_cards'])
    page = st.number_input("Preview Page", min_value=1, max_value=max(1, (total // 5) + (1 if total % 5 > 0 else 0)), step=1) - 1
    
    for idx, c in enumerate(st.session_state['generated_cards'][page*5 : page*5 + 5]):
        real_idx = page * 5 + idx
        with st.container():
            # True Anki Night Mode styling (Idea 20)
            mcq_block = f"<div class='anki-preview-mcq'>{c.get('Options', '')}</div>" if c.get('Options') else ""
            st.markdown(f"""
                <div class="anki-preview-container">
                    <div>{c['Question']}</div>
                    {mcq_block}
                    <hr>
                    <div class="anki-preview-answer">{c['Answer']}</div>
                    <div class="anki-preview-context">{c['Context']}</div>
                </div>
            """, unsafe_allow_html=True)
            
            p_col1, p_col2 = st.columns([1, 4])
            with p_col1:
                if st.button(f"🔊 Listen", key=f"tts_{hash(c['Question'])}_{real_idx}"):
                    clean_ans = re.sub(r'<[^>]+>|\\\[|\\\]|\\\(|\\\)', '', str(c['Answer']))
                    tts_preview = gTTS(clean_ans, lang=current_lang_code)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                        tts_preview.save(fp.name)
                        st.audio(fp.name)
            with p_col2:
                if st.button("🗑️ Delete Card", key=f"del_{hash(c['Question'])}_{real_idx}"):
                    st.session_state['generated_cards'].pop(real_idx)
                    st.session_state['apkg_cache'] = None # Invalidate cache
                    st.rerun()

    # ================================================
    # EXPORT
    # ================================================
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📦 Finalize Deck")
        include_audio = st.toggle("Include Answer TTS in Export", value=True)
        
        # Idea 16: Instant APKG Byte Caching
        current_data_hash = hash(str(st.session_state['generated_cards']) + str(include_audio) + subject)
        
        if st.session_state['apkg_hash'] != current_data_hash or st.session_state['apkg_cache'] is None:
            if st.button("⚡ Compile Anki Deck"):
                with st.spinner("Compiling Media and Deck..."):
                    apkg = generate_apkg(st.session_state['generated_cards'], subject, include_audio, current_lang_code)
                    st.session_state['apkg_cache'] = apkg
                    st.session_state['apkg_hash'] = current_data_hash
                    st.rerun()
        else:
            st.success("✅ Compilation Complete!")
            st.download_button("💾 Download .apkg", st.session_state['apkg_cache'], file_name=f"{subject.replace('::', '_')}.apkg", mime="application/octet-stream", use_container_width=True)

    with col2:
        st.subheader("📄 Backup")
        csv_data = df.to_csv(index=False)
        st.download_button("📥 Download CSV", csv_data, file_name=f"{subject.replace('::', '_')}.csv", mime="text/csv", use_container_width=True)
