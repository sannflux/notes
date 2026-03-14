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
if 'preview_page' not in st.session_state:
    st.session_state['preview_page'] = 0

# Custom CSS for Anki-like Previews and Batch UI
st.markdown("""
    <style>
    .anki-card { background-color: #2e2e2e; border-radius: 10px; padding: 20px; border: 1px solid #444; margin-bottom: 10px; font-family: Arial; }
    .anki-front { color: #ffffff; font-size: 1.1em; border-bottom: 1px solid #555; padding-bottom: 10px; }
    .anki-back { color: #00aaff; font-size: 1.1em; padding-top: 10px; }
    .anki-context { color: #bbbbbb; font-size: 0.9em; padding-top: 10px; font-style: italic; }
    .tag-pill { background: #444; color: #88eeff; padding: 2px 8px; border-radius: 5px; font-size: 0.8em; }
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
    # Feature 4: MathJax/LaTeX Anki Escaping
    text = re.sub(r'\$\$(.*?)\$\$', r'\\[\1\\]', text, flags=re.DOTALL)
    text = re.sub(r'\$(.*?)\$', r'\\(\1\\)', text)
    return text

def is_duplicate(new_q, existing_cards, threshold=0.85):
    """Feature 13: Deduplication Engine"""
    for c in existing_cards:
        if difflib.SequenceMatcher(None, new_q.lower(), c['Question'].lower()).ratio() > threshold:
            return True
    return False

# ================================================
# ANKI .APKG EXPORT ENGINE (Features 1, 3, 5, 15)
# ================================================
ANKI_CSS = """
.card { font-family: Arial; font-size: 20px; text-align: center; color: black; background-color: white; }
.card.nightMode { background-color: #272828; color: #e2e2e2; }
.context { font-size: 16px; color: #555; margin-top: 20px; font-style: italic; border-top: 1px solid #ccc; padding-top: 10px; }
.card.nightMode .context { color: #aaa; border-top: 1px solid #555; }
.cloze { font-weight: bold; color: blue; }
.card.nightMode .cloze { color: #00aaff; }
"""

# Static IDs to prevent deck duplication in Anki
BASIC_MODEL_ID = 1607392319
CLOZE_MODEL_ID = 1607392320

anki_basic_model = genanki.Model(
    BASIC_MODEL_ID, 'AI Anki PRO Basic',
    fields=[{'name': 'Question'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{
        'name': 'Card 1',
        'qfmt': '{{Question}}<br><br>{{Audio}}',
        'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}<div class="context">{{Context}}</div>',
    }],
    css=ANKI_CSS
)

anki_cloze_model = genanki.Model(
    CLOZE_MODEL_ID, 'AI Anki PRO Cloze',
    model_type=genanki.Model.CLOZE,
    fields=[{'name': 'Text'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{
        'name': 'Cloze',
        'qfmt': '{{cloze:Text}}<br><br>{{Audio}}',
        'afmt': '{{cloze:Text}}<br><div class="context">{{Context}}</div>',
    }],
    css=ANKI_CSS
)

def generate_apkg(cards, deck_name, include_audio):
    deck_id = hash(deck_name) % (10**10) # Stable deck ID based on name
    deck = genanki.Deck(deck_id, deck_name)
    media_files = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, c in enumerate(cards):
            audio_field = ""
            
            # Feature 15: Automated Audio/TTS
            if include_audio:
                try:
                    # Read answer or cloze text for audio
                    text_to_read = c['Answer'] if 'Answer' in c and c['Answer'] else c['Question']
                    # strip html tags for tts
                    clean_text = re.sub(r'<[^>]+>', '', text_to_read)
                    tts = gTTS(clean_text, lang='en')
                    filename = f"anki_audio_{deck_id}_{idx}.mp3"
                    filepath = os.path.join(tmpdir, filename)
                    tts.save(filepath)
                    media_files.append(filepath)
                    audio_field = f"[sound:{filename}]"
                except Exception as e:
                    pass # Silently fail TTS on individual card to avoid crashing batch

            # Format Tags
            tags = [t.strip().replace("#", "") for t in c['Tags'].split() if t.strip()]

            # Determine Note Type (Cloze vs Basic)
            if "{{c" in c['Question']:
                note = genanki.Note(
                    model=anki_cloze_model,
                    fields=[c['Question'], c['Context'], audio_field],
                    tags=tags
                )
            else:
                note = genanki.Note(
                    model=anki_basic_model,
                    fields=[c['Question'], c['Answer'], c['Context'], audio_field],
                    tags=tags
                )
            deck.add_note(note)

        package = genanki.Package(deck)
        package.media_files = media_files
        
        output_bytes = io.BytesIO()
        # genanki writes to file, so we write to temp, then read to bytes
        temp_apkg = os.path.join(tmpdir, "export.apkg")
        package.write_to_file(temp_apkg)
        with open(temp_apkg, "rb") as f:
            output_bytes.write(f.read())
            
        return output_bytes.getvalue()

# ================================================
# PROMPT LOGIC & BATCH ENGINE
# ================================================

# Features 3, 5, 6, 7, 10
BASE_SYSTEM_INSTRUCTION = """You are an expert Anki flashcard creator acting as a university professor.
IMAGE ANALYSIS: Transcribe and analyze the content accurately.

CARD RULES (MINIMUM INFORMATION PRINCIPLE):
- Facts must be atomic, singular, and impossible to misunderstand. Do not create "walls of text".
- [REVERSE CARDS]: If a core definition is found, generate both "Term -> Def" and "Def -> Term".
- [BREVITY]: Answers must be extremely concise (under 15 words).
- [CONTEXT]: Put all explanatory background information, formulas, or elaboration into the "context" field.
- [CONFIDENCE]: Provide a confidence_score (0-100) based on image legibility and certainty.

OUTPUT RULES:
Return ONLY a JSON array of objects following this schema:
[{"question": "string", "answer": "string", "context": "string", "suggested_tags": ["tag1"], "confidence_score": integer}]
"""

# Feature 16: API Retry Logic
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def process_single_image(file, model, prompt_suffix):
    raw_img = Image.open(file)
    enhanced_img = enhance_image(raw_img)
    full_prompt = f"Subject: {subject}. {prompt_suffix} Generate Anki cards in JSON."
    
    response = model.generate_content([enhanced_img, full_prompt])
    clean_json = re.sub(r'```json|```', '', response.text).strip()
    return json.loads(clean_json)

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
    
    subject = st.text_input("Subject:", value="Biology")
    fixed_tag = st.text_input("Fixed Tag:", value="#Medical_2024").replace(" ", "_")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual"])
    
    eli5_mode = st.checkbox("Simplify Explanations (ELI5)", help="Makes context easier to understand.")
    cloze_mode = st.checkbox("Enable Cloze Deletions", help="Generates fill-in-the-blank cards {{c1::like this}}.")
    
    if st.button("🗑️ Clear All Memory"):
        st.session_state['generated_cards'] = []
        st.session_state['preview_page'] = 0
        st.rerun()

    st.divider()
    st.info("MODEL STASIS: gemini-2.5-flash-lite")

# ================================================
# MAIN UI
# ================================================
st.title("🎓 AI Anki Generator PRO (2.5-Flash-Lite)")

# Feature 20: Token Safety Estimator
uploaded_files = st.file_uploader("📸 Batch Upload Images", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)
if uploaded_files:
    est_tokens = len(uploaded_files) * 258 + 500 # Images + Prompt approx
    if est_tokens > 20000:
        st.warning(f"⚠️ Token Estimate: ~{est_tokens}. Consider batching in smaller chunks to avoid API limits.")
    else:
        st.info(f"ℹ️ Token Estimate: ~{est_tokens} (Safe)")

if uploaded_files and api_key:
    if st.button("🚀 Process Batch (Concurrent)"):
        genai.configure(api_key=api_key)
        
        # Cloze Modifier
        instruction = BASE_SYSTEM_INSTRUCTION
        if cloze_mode:
            instruction += "\nCLOZE MODE ACTIVE: Instead of question/answer, format 'question' as a cloze sentence with {{c1::hidden text}} and leave 'answer' empty."

        model = genai.GenerativeModel(
            model_name='gemini-2.5-flash-lite',
            system_instruction=instruction,
            generation_config={"response_mime_type": "application/json"}
        )
        
        prompt_suffix = f"Language: {language}."
        if eli5_mode: prompt_suffix += " Use very simple language (ELI5) for the context."

        # Feature 17: Dynamic Thread Scaling
        max_threads = min(len(uploaded_files), 5)
        
        with st.status(f"Processing {len(uploaded_files)} images concurrently (Threads: {max_threads})...", expanded=True) as status:
            with ThreadPoolExecutor(max_workers=max_threads) as executor:
                futures = [executor.submit(process_single_image, f, model, prompt_suffix) for f in uploaded_files]
                
                cards_added = 0
                duplicates_skipped = 0
                
                for future in futures:
                    try:
                        result = future.result()
                        for card in result:
                            new_q = card.get('question', '')
                            # Deduplication check
                            if not is_duplicate(new_q, st.session_state['generated_cards']):
                                st.session_state['generated_cards'].append({
                                    "Question": markdown_to_html(new_q),
                                    "Answer": markdown_to_html(card.get('answer', '')),
                                    "Context": markdown_to_html(card.get('context', '')),
                                    "Tags": f"{fixed_tag} { ' '.join(card.get('suggested_tags', [])) }",
                                    "Confidence": card.get('confidence_score', 0)
                                })
                                cards_added += 1
                            else:
                                duplicates_skipped += 1
                    except Exception as e:
                        st.error(f"Failed processing image: {str(e)}")
            
            status.update(label=f"✅ Complete! Added {cards_added} cards (Skipped {duplicates_skipped} duplicates).", state="complete")

# ================================================
# REVIEW & EXPORT
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    st.subheader("📝 Edit Cards")
    df = pd.DataFrame(st.session_state['generated_cards'])
    
    # Sort by confidence so users can review low confidence first
    df = df.sort_values(by="Confidence", ascending=True).reset_index(drop=True)
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    st.session_state['generated_cards'] = edited_df.to_dict('records')

    # Feature 19: Real-Time Interactive Pagination
    st.subheader("👀 Visual Preview")
    
    total_cards = len(st.session_state['generated_cards'])
    cards_per_page = 5
    max_pages = max(1, (total_cards + cards_per_page - 1) // cards_per_page)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("⬅️ Previous") and st.session_state['preview_page'] > 0:
            st.session_state['preview_page'] -= 1
            st.rerun()
    with col2:
        st.write(f"<div style='text-align: center'>Page {st.session_state['preview_page'] + 1} of {max_pages}</div>", unsafe_allow_html=True)
    with col3:
        if st.button("Next ➡️") and st.session_state['preview_page'] < max_pages - 1:
            st.session_state['preview_page'] += 1
            st.rerun()

    start_idx = st.session_state['preview_page'] * cards_per_page
    end_idx = start_idx + cards_per_page

    for idx, c in enumerate(st.session_state['generated_cards'][start_idx:end_idx]):
        st.markdown(f"""
            <div class="anki-card">
                <div class="anki-front"><b>Q:</b> {c['Question']}</div>
                <div class="anki-back"><b>A:</b> {c['Answer']}</div>
                <div class="anki-context"><b>Context:</b> {c['Context']}</div>
                <div style="margin-top:10px;">
                    <span class="tag-pill">{c['Tags']}</span>
                    <span style="float:right; font-size:0.8em; color:{'#ff4444' if c['Confidence'] < 80 else '#44ff44'};">
                        Confidence: {c['Confidence']}%
                    </span>
                </div>
            </div>
        """, unsafe_allow_html=True)

    st.divider()
    st.subheader("📥 Export Options")
    
    export_col1, export_col2 = st.columns(2)
    
    with export_col1:
        # CSV Export (Preserved)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Question', 'Answer', 'Context', 'Tags']) # Header
        for c in st.session_state['generated_cards']:
            writer.writerow([c['Question'], c['Answer'], c['Context'], c['Tags']])
        st.download_button("📥 Download Standard CSV", output.getvalue(), file_name=f"{subject}_anki.csv", mime="text/csv", type="secondary", use_container_width=True)

    with export_col2:
        # APKG Export Options
        include_audio = st.checkbox("Generate Text-to-Speech Audio (Increases export time)", value=False)
        if st.button("📦 Package .apkg (Anki Native)", type="primary", use_container_width=True):
            with st.spinner("Compiling .apkg deck..."):
                apkg_bytes = generate_apkg(st.session_state['generated_cards'], subject, include_audio)
                st.download_button(
                    label="Download Ready .apkg",
                    data=apkg_bytes,
                    file_name=f"{subject}_AI_Deck.apkg",
                    mime="application/octet-stream",
                    use_container_width=True
                )
