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
import random

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# Initialize Session State
if 'generated_cards' not in st.session_state: st.session_state['generated_cards'] = []
if 'preview_page' not in st.session_state: st.session_state['preview_page'] = 0
if 'audio_cache' not in st.session_state: st.session_state['audio_cache'] = {}
if 'total_api_calls' not in st.session_state: st.session_state['total_api_calls'] = 0

# Premium Fonts & Adaptive CSS
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    .anki-card { 
        background-color: rgba(128, 128, 128, 0.1); 
        border-radius: 12px; 
        padding: 20px; 
        border: 2px solid #444444; 
        margin-bottom: 15px;
        font-family: 'Inter', sans-serif;
    }
    .anki-front { font-size: 1.2em; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; margin-bottom: 10px; }
    .anki-back { color: #00aaff; font-size: 1.1em; font-weight: 600; }
    .anki-context { color: gray; font-size: 0.9em; padding-top: 10px; font-style: italic; }
    .tag-pill { background: rgba(0, 170, 255, 0.2); color: #00aaff; padding: 2px 10px; border-radius: 15px; font-size: 0.8em; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { border: 1px solid rgba(255,255,255,0.2); padding: 8px; text-align: left; }
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
    text = re.sub(r'\$\$(.*?)\$\$', r'\\[ \1 \\]', text, flags=re.DOTALL)
    text = re.sub(r'\$([^\$]+)\$', r'\\( \1 \\)', text)
    return text

def is_duplicate(new_q, existing_cards, threshold=0.85):
    for c in existing_cards:
        if difflib.SequenceMatcher(None, new_q.lower(), str(c['Question']).lower()).ratio() > threshold:
            return True
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
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
.card.nightMode th, .card.nightMode td { border: 1px solid #555; }
"""

BASIC_MODEL_ID = 1607392319
CLOZE_MODEL_ID = 1607392320

anki_basic_model = genanki.Model(
    BASIC_MODEL_ID, 'AI Anki PRO Basic',
    fields=[{'name': 'Question'}, {'name': 'Answer'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{'name': 'Card 1', 'qfmt': '{{Question}}', 'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}<br><br>{{Audio}}<div class="context">{{Context}}</div>'}],
    css=ANKI_CSS
)

anki_cloze_model = genanki.Model(
    CLOZE_MODEL_ID, 'AI Anki PRO Cloze',
    model_type=genanki.Model.CLOZE,
    fields=[{'name': 'Text'}, {'name': 'Context'}, {'name': 'Audio'}],
    templates=[{'name': 'Cloze', 'qfmt': '{{cloze:Text}}', 'afmt': '{{cloze:Text}}<br><br>{{Audio}}<div class="context">{{Context}}</div>'}],
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
# PROMPT LOGIC & THREAD ENGINE (Rate-Limit Safe)
# ================================================
BASE_SYSTEM_INSTRUCTION = """You are an expert Anki professor. Transcribe and analyze the input. 

CHEMISTRY/MATH RULES:
- Use LaTeX for ALL math. Inline: $...$, Block: $$. . .$$
- Chemistry: Use \\ce{...} for equations.

CARD RULES:
- Atomic facts only. 
- [REVERSE CARDS]: If definition, generate Term->Def and Def->Term.
- [BREVITY]: Answers < 15 words.
- [CONTEXT]: Elaboration goes here. If tabular data exists, format it using HTML <table>, <tr>, <td> tags here.
- [HIGHLIGHT]: Wrap the single most critical keyword in the 'answer' field with <span style="color: #ffeb3b;">.

OUTPUT: JSON array [{"question", "answer", "context", "distractors": ["wrong1", "wrong2", "wrong3"], "suggested_tags", "confidence_score"}]
"""

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def process_payload(payload, model, prompt_suffix, is_image=True):
    full_prompt = f"{prompt_suffix}"
    content = [payload, full_prompt] if is_image else [full_prompt, payload]
    response = model.generate_content(content)
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
    
    subject = st.text_input("Subject (use :: for sub-decks):", value="Science::Biology")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual"])
    LANG_MAP = {"English": "en", "Bahasa Indonesia": "id", "Bilingual": "id"}
    current_lang_code = LANG_MAP.get(language, "en")

    st.divider()
    cloze_mode = st.checkbox("Enable Cloze Deletions")
    mcq_mode = st.checkbox("Enable Multiple Choice (MCQ)") 
    eli5_mode = st.checkbox("ELI5 (Simple Context)")
    
    st.divider()
    st.subheader("📊 Session Analytics")
    st.metric("Total Cards Generated", len(st.session_state['generated_cards']))
    st.metric("API Calls Made", st.session_state['total_api_calls'])
    
    st.divider()
    if st.button("🗑️ Reset Application"):
        st.session_state['generated_cards'] = []
        st.session_state['audio_cache'] = {}
        st.session_state['total_api_calls'] = 0
        st.rerun()

# ================================================
# MAIN PROCESSING (Multi-Modal Tabs)
# ================================================
st.title("🎓 AI Anki Generator PRO")

# Setup Model Logic
instruction = BASE_SYSTEM_INSTRUCTION
if cloze_mode: instruction += "\nCLOZE MODE: 'question' must contain {{c1::...}}."
if mcq_mode: instruction += "\nMULTIPLE CHOICE MODE: You MUST provide 3 realistic wrong answers in the 'distractors' array."

model = None
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash-lite', 
        system_instruction=instruction,
        generation_config={"response_mime_type": "application/json"}
    )

prompt_suffix = f"Subject: {subject}. Language: {language}."
tab_img, tab_txt = st.tabs(["📸 Image Batch (OCR/Math)", "📝 Text/Notes Batch"])

# TAB 1: IMAGES
with tab_img:
    uploaded_files = st.file_uploader("Upload Images", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)
    if uploaded_files and api_key:
        if st.button("🚀 Generate from Images", type="primary"):
            with st.status("Processing Images...", expanded=True) as status:
                st.session_state['total_api_calls'] += len(uploaded_files)
                # Max workers capped at 5 to prevent ResourceExhausted
                with ThreadPoolExecutor(max_workers=min(len(uploaded_files), 5)) as executor:
                    futures = []
                    for f in uploaded_files:
                        img = enhance_image(Image.open(f))
                        futures.append(executor.submit(process_payload, img, model, prompt_suffix, True))
                    
                    for future in futures:
                        try:
                            cards = future.result()
                            for card in cards:
                                new_q = card.get('question', '')
                                # MCQ Logic
                                if mcq_mode and card.get('distractors'):
                                    options = [card.get('answer')] + card.get('distractors')
                                    random.shuffle(options)
                                    letters = ['A', 'B', 'C', 'D', 'E']
                                    mcq_html = "<br><br><div style='text-align:left; margin-left: 20px;'>"
                                    for i, opt in enumerate(options[:4]):
                                        mcq_html += f"<b>{letters[i]})</b> {opt}<br>"
                                    mcq_html += "</div>"
                                    new_q += mcq_html

                                if not is_duplicate(new_q, st.session_state['generated_cards']):
                                    st.session_state['generated_cards'].append({
                                        "Question": markdown_to_html(new_q),
                                        "Answer": markdown_to_html(card.get('answer', '')),
                                        "Context": markdown_to_html(card.get('context', '')),
                                        "Tags": f"#AI_Generated {' '.join(card.get('suggested_tags', []))}",
                                        "Confidence": card.get('confidence_score', 0)
                                    })
                        except Exception as e:
                            st.error(f"Error processing image: {str(e)}")
                status.update(label="✅ Image Processing Finished!", state="complete")

# TAB 2: TEXT
with tab_txt:
    pasted_text = st.text_area("Paste Lecture Notes, Transcripts, or PDF Text:", height=200)
    if pasted_text and api_key:
        if st.button("🚀 Generate from Text", type="primary"):
            with st.status("Processing Text...", expanded=True) as status:
                chunk_size = 3000 # Split text into chunks to act like batch images
                text_chunks = [pasted_text[i:i+chunk_size] for i in range(0, len(pasted_text), chunk_size)]
                st.session_state['total_api_calls'] += len(text_chunks)
                
                with ThreadPoolExecutor(max_workers=min(len(text_chunks), 5)) as executor:
                    futures = [executor.submit(process_payload, chunk, model, prompt_suffix, False) for chunk in text_chunks]
                    for future in futures:
                        try:
                            cards = future.result()
                            for card in cards:
                                new_q = card.get('question', '')
                                # MCQ Logic
                                if mcq_mode and card.get('distractors'):
                                    options = [card.get('answer')] + card.get('distractors')
                                    random.shuffle(options)
                                    letters = ['A', 'B', 'C', 'D', 'E']
                                    mcq_html = "<br><br><div style='text-align:left; margin-left: 20px;'>"
                                    for i, opt in enumerate(options[:4]):
                                        mcq_html += f"<b>{letters[i]})</b> {opt}<br>"
                                    mcq_html += "</div>"
                                    new_q += mcq_html

                                if not is_duplicate(new_q, st.session_state['generated_cards']):
                                    st.session_state['generated_cards'].append({
                                        "Question": markdown_to_html(new_q),
                                        "Answer": markdown_to_html(card.get('answer', '')),
                                        "Context": markdown_to_html(card.get('context', '')),
                                        "Tags": f"#AI_Generated {' '.join(card.get('suggested_tags', []))}",
                                        "Confidence": card.get('confidence_score', 0)
                                    })
                        except Exception as e:
                            st.error(f"Error processing text chunk: {str(e)}")
                status.update(label="✅ Text Processing Finished!", state="complete")

# ================================================
# PREVIEW, EDITS & TTS
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    df = pd.DataFrame(st.session_state['generated_cards'])
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    st.session_state['generated_cards'] = edited_df.to_dict('records')

    # Bulk Tag Manager
    with st.expander("🏷️ Bulk Tag Manager"):
        b_col1, b_col2, b_col3 = st.columns(3)
        bulk_tag = b_col1.text_input("Tag (e.g., #Exam1):").replace(" ", "_")
        if b_col2.button("➕ Add to All") and bulk_tag:
            for c in st.session_state['generated_cards']:
                if bulk_tag not in c['Tags']: c['Tags'] += f" {bulk_tag}"
            st.rerun()
        if b_col3.button("➖ Remove from All") and bulk_tag:
            for c in st.session_state['generated_cards']:
                c['Tags'] = c['Tags'].replace(bulk_tag, "").strip()
            st.rerun()

    st.subheader("👀 Card Preview")
    total = len(st.session_state['generated_cards'])
    page = st.number_input("Preview Page", min_value=1, max_value=max(1, (total // 5) + (1 if total % 5 > 0 else 0)), step=1) - 1
    
    for idx, c in enumerate(st.session_state['generated_cards'][page*5 : page*5 + 5]):
        real_idx = page * 5 + idx
        with st.container():
            st.markdown(f"""
                <div class="anki-card">
                    <div class="anki-front">{c['Question']}</div>
                    <div class="anki-back">Ans: {c['Answer']}</div>
                    <div class="anki-context">{c['Context']}</div>
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
                    st.rerun()

    # ================================================
    # EXPORT
    # ================================================
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📦 Finalize Deck")
        include_audio = st.toggle("Include Answer TTS in Export", value=True)
        if st.button("📥 Download .apkg", type="primary", use_container_width=True):
            with st.spinner("Compiling Deck..."):
                apkg = generate_apkg(st.session_state['generated_cards'], subject, include_audio, current_lang_code)
                st.download_button("💾 Save Anki Deck", apkg, file_name=f"{subject.replace('::', '_')}.apkg", mime="application/octet-stream", use_container_width=True)
    with col2:
        st.subheader("📄 Backup")
        csv_data = df.to_csv(index=False)
        st.download_button("📥 Download CSV", csv_data, file_name=f"{subject.replace('::', '_')}.csv", mime="text/csv", use_container_width=True)
