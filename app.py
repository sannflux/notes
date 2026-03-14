import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageEnhance, ImageFilter
import csv
import io
import re
import json
import time
from datetime import datetime
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import difflib
from tenacity import retry, stop_after_attempt, wait_exponential
import genanki
from gtts import gTTS
import tempfile
import os

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# Initialize Session State
if 'generated_cards' not in st.session_state:
    st.session_state['generated_cards'] = []
if 'last_batch_errors' not in st.session_state:
    st.session_state['last_batch_errors'] = []
if 'preview_page' not in st.session_state:
    st.session_state['preview_page'] = 0

# Idea 20: Theme-Adaptive CSS
st.markdown("""
    <style>
    .anki-card { 
        background-color: rgba(128, 128, 128, 0.1); 
        border-radius: 12px; 
        padding: 20px; 
        border: 2px solid #444444; 
        margin-bottom: 15px;
    }
    .anki-front { font-size: 1.2em; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; margin-bottom: 10px; }
    .anki-back { color: #00aaff; font-size: 1.1em; font-weight: bold; }
    .anki-context { color: gray; font-size: 0.9em; padding-top: 10px; font-style: italic; }
    .tag-pill { background: rgba(0, 170, 255, 0.2); color: #00aaff; padding: 2px 10px; border-radius: 15px; font-size: 0.8em; }
    </style>
""", unsafe_allow_html=True)

# ================================================
# CORE HELPERS & ANKI LOGIC
# ================================================
def enhance_image(img):
    img = img.convert("RGB")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    img.thumbnail((1200, 1200))
    return img

def markdown_to_html(text):
    text = str(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    # LaTeX & mhchem (Idea 7) support
    text = re.sub(r'\$\$(.*?)\$\$', r'\\[ \1 \\]', text, flags=re.DOTALL)
    text = re.sub(r'\$([^\$]+)\$', r'\\( \1 \\)', text)
    return text

def is_duplicate(new_q, existing_cards, threshold=0.85):
    for c in existing_cards:
        if difflib.SequenceMatcher(None, new_q.lower(), str(c['Question']).lower()).ratio() > threshold:
            return True
    return False

# ================================================
# ANKI .APKG EXPORT ENGINE (Fix: TTS on Back)
# ================================================
ANKI_CSS = """
.card { font-family: Arial; font-size: 20px; text-align: center; color: black; background-color: white; }
.card.nightMode { background-color: #272828; color: #e2e2e2; }
.context { font-size: 16px; color: #777; margin-top: 20px; font-style: italic; border-top: 1px solid #ccc; padding-top: 10px; }
.card.nightMode .context { color: #aaa; border-top: 1px solid #555; }
"""

BASIC_MODEL_ID = 1607392319
CLOZE_MODEL_ID = 1607392320

anki_basic_model = genanki.Model(
    BASIC_MODEL_ID, 'AI Anki PRO Basic',
    fields=[{'name': 'Question'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{
        'name': 'Card 1',
        'qfmt': '{{Question}}',
        'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}<br><br>{{Audio}}<div class="context">{{Context}}</div>',
    }],
    css=ANKI_CSS
)

anki_cloze_model = genanki.Model(
    CLOZE_MODEL_ID, 'AI Anki PRO Cloze',
    model_type=genanki.Model.CLOZE,
    fields=[{'name': 'Text'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{
        'name': 'Cloze',
        'qfmt': '{{cloze:Text}}',
        'afmt': '{{cloze:Text}}<br><br>{{Audio}}<div class="context">{{Context}}</div>',
    }],
    css=ANKI_CSS
)

def generate_apkg(cards, deck_name, include_audio, lang_code):
    deck_id = hash(deck_name) % (10**10) 
    deck = genanki.Deck(deck_id, deck_name)
    media_files = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, c in enumerate(cards):
            audio_field = ""
            if include_audio:
                try:
                    text_to_read = c['Answer'] if (c.get('Answer')) else c['Question']
                    clean_text = re.sub(r'<[^>]+>', '', str(text_to_read))
                    clean_text = re.sub(r'\\\[|\\\]|\\\(|\\\)', '', clean_text)
                    if clean_text.strip():
                        tts = gTTS(clean_text, lang=lang_code)
                        filename = f"audio_{idx}_{int(time.time())}.mp3"
                        filepath = os.path.join(tmpdir, filename)
                        tts.save(filepath)
                        media_files.append(filepath)
                        audio_field = f"[sound:{filename}]"
                except: pass 
            
            tags = [t.strip().replace("#", "") for t in str(c['Tags']).split() if t.strip()]
            if "{{c" in str(c['Question']):
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
# PROMPT LOGIC & BATCH ENGINE (Idea 7: Chemistry)
# ================================================
BASE_SYSTEM_INSTRUCTION = """You are an expert Anki professor.
IMAGE ANALYSIS: Transcribe accurately. 

CHEMISTRY/MATH RULES:
- IMPORTANT: Use LaTeX for ALL math.
- For Chemistry: Use \\ce{...} for chemical equations and reactions (e.g. \\ce{H2O}).
- Inline: $...$, Block: $$. . .$$

CARD RULES:
- Facts must be atomic. 
- [REVERSE CARDS]: If definition, generate Term->Def and Def->Term.
- [BREVITY]: Answers < 15 words.
- [CONTEXT]: Elaboration goes in 'context'.

OUTPUT: JSON array [{"question", "answer", "context", "suggested_tags", "confidence_score"}]
"""

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
def process_single_image(file, model, prompt_suffix):
    raw_img = Image.open(file)
    enhanced_img = enhance_image(raw_img)
    full_prompt = f"Subject: {subject}. {prompt_suffix}"
    response = model.generate_content([enhanced_img, full_prompt])
    clean_json = re.sub(r'```json|```', '', response.text).strip()
    return json.loads(clean_json), file

# ================================================
# SIDEBAR & UI
# ================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ API Key loaded")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")
    
    subject = st.text_input("Subject:", value="General Science")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual"])
    LANG_MAP = {"English": "en", "Bahasa Indonesia": "id", "Bilingual": "id"}
    current_lang_code = LANG_MAP.get(language, "en")

    eli5_mode = st.checkbox("ELI5 (Simple Context)")
    cloze_mode = st.checkbox("Enable Cloze Deletions")
    
    if st.button("🗑️ Reset Application"):
        st.session_state['generated_cards'] = []
        st.session_state['last_batch_errors'] = []
        st.rerun()

# ================================================
# MAIN PROCESSING (Idea 18: Error Recovery)
# ================================================
st.title("🎓 AI Anki Generator PRO")

uploaded_files = st.file_uploader("📸 Upload Image Batch", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

files_to_process = []
if uploaded_files:
    files_to_process = uploaded_files
elif st.session_state['last_batch_errors']:
    if st.button("🔄 Retry Failed Images Only"):
        files_to_process = st.session_state['last_batch_errors']

if files_to_process and api_key:
    if st.button("🚀 Run AI Generation"):
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name='gemini-2.5-flash-lite', 
            system_instruction=BASE_SYSTEM_INSTRUCTION + ("\nCLOZE: use {{c1::...}}" if cloze_mode else ""),
            generation_config={"response_mime_type": "application/json"}
        )
        
        st.session_state['last_batch_errors'] = [] # Reset errors
        
        with st.status("Processing Batch...", expanded=True) as status:
            with ThreadPoolExecutor(max_workers=min(len(files_to_process), 5)) as executor:
                futures = [executor.submit(process_single_image, f, model, f"Language: {language}") for f in files_to_process]
                for future in futures:
                    try:
                        cards, original_file = future.result()
                        for card in cards:
                            if not is_duplicate(card.get('question', ''), st.session_state['generated_cards']):
                                st.session_state['generated_cards'].append({
                                    "Question": markdown_to_html(card.get('question', '')),
                                    "Answer": markdown_to_html(card.get('answer', '')),
                                    "Context": markdown_to_html(card.get('context', '')),
                                    "Tags": f"#AI_Generated {' '.join(card.get('suggested_tags', []))}",
                                    "Confidence": card.get('confidence_score', 0)
                                })
                    except Exception as e:
                        st.error(f"Error processing a file. Logged for retry.")
                        # Extract the file from the future if possible or store in error state
                        st.session_state['last_batch_errors'].append(files_to_process[futures.index(future)])
            status.update(label="✅ Processing Finished!", state="complete")

# ================================================
# PREVIEW & LIVE TTS (Idea 19 & 20)
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    df = pd.DataFrame(st.session_state['generated_cards'])
    edited_df = st.data_editor(df.sort_values(by="Confidence"), use_container_width=True, num_rows="dynamic")
    st.session_state['generated_cards'] = edited_df.to_dict('records')

    st.subheader("👀 Card Preview & Voice Check")
    
    # Pagination Logic
    total = len(st.session_state['generated_cards'])
    page = st.number_input("Preview Page", min_value=1, max_value=(total // 5) + 1, step=1) - 1
    
    for c in st.session_state['generated_cards'][page*5 : page*5 + 5]:
        with st.container():
            st.markdown(f"""
                <div class="anki-card">
                    <div class="anki-front">{c['Question']}</div>
                    <div class="anki-back">Ans: {c['Answer']}</div>
                    <div class="anki-context">{c['Context']}</div>
                </div>
            """, unsafe_allow_html=True)
            
            # Idea 19: Live TTS Preview for the Answer
            if st.button(f"🔊 Listen to Answer", key=f"tts_{hash(c['Question'])}"):
                clean_ans = re.sub(r'<[^>]+>|\\\[|\\\]|\\\(|\\\)', '', str(c['Answer']))
                tts_preview = gTTS(clean_ans, lang=current_lang_code)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                    tts_preview.save(fp.name)
                    st.audio(fp.name)

    # ================================================
    # EXPORT
    # ================================================
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📦 Finalize Deck")
        include_audio = st.toggle("Include Answer TTS in Export", value=True)
        if st.button("📥 Download .apkg", type="primary"):
            apkg = generate_apkg(st.session_state['generated_cards'], subject, include_audio, current_lang_code)
            st.download_button("💾 Save Anki Deck", apkg, file_name=f"{subject}.apkg", mime="application/octet-stream")
    with col2:
        st.subheader("📄 Backup")
        csv_data = df.to_csv(index=False)
        st.download_button("📥 Download CSV", csv_data, file_name=f"{subject}.csv", mime="text/csv")
