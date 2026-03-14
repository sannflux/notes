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

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# Initialize Session State
if 'generated_cards' not in st.session_state:
    st.session_state['generated_cards'] = []

# Custom CSS for Anki-like Previews and Batch UI
st.markdown("""
    <style>
    .anki-card {
        background-color: #2e2e2e;
        border-radius: 10px;
        padding: 20px;
        border: 1px solid #444;
        margin-bottom: 10px;
        font-family: Arial;
    }
    .anki-front { color: #ffffff; font-size: 1.1em; border-bottom: 1px solid #555; padding-bottom: 10px; }
    .anki-back { color: #00aaff; font-size: 1.1em; padding-top: 10px; }
    .tag-pill { background: #444; color: #88eeff; padding: 2px 8px; border-radius: 5px; font-size: 0.8em; }
    .copy-btn { float: right; cursor: pointer; color: #00aaff; border: 1px solid #00aaff; padding: 2px 5px; border-radius: 3px; font-size: 0.7em; }
    </style>
""", unsafe_allow_html=True)

# ================================================
# PRESERVATION ANCHOR: CORE HELPERS (OPTIMIZED)
# ================================================
def enhance_image(img):
    """Preserved: Contrast + Sharpen. Optimization: Token-saving compression."""
    img = img.convert("RGB")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    # Token Saver: Downscale to save API quota on digital notes
    img.thumbnail((1200, 1200))
    return img

def markdown_to_html(text):
    """Preserved: HTML formatting for Anki."""
    text = str(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    return text

# ================================================
# CATEGORY A: PROMPT AUDIT (RE-APPLIED BATCH 3)
# ================================================
# Features 3, 4, 5 integrated into the instruction
SYSTEM_INSTRUCTION = """You are an expert Anki flashcard creator acting as a university professor.
IMAGE ANALYSIS: Transcribe and analyze the content.

CARD RULES:
- Questions test understanding/application.
- [Feature 3] REVERSE CARDS: If a core definition is found, generate both "Term -> Def" and "Def -> Term".
- [Feature 5] BREVITY: Keep answers concise (ideally under 25 words).
- Answer uses ONLY <b> and <i> tags. NEVER ** or *.

OUTPUT RULES:
Return ONLY a JSON array of objects:
[{"question": "string", "answer": "string", "suggested_tags": ["tag1"]}]
"""

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
    
    # Feature 4: ELI5 Toggle
    eli5_mode = st.checkbox("Simplify Explanations (ELI5)", help="Makes answers easier to understand.")
    
    if st.button("🗑️ Clear All Memory"):
        st.session_state['generated_cards'] = []
        st.rerun()

    st.divider()
    st.info("MODEL STASIS: gemini-2.5-flash-lite")

# ================================================
# BATCH GENERATION LOGIC (FEATURE 13)
# ================================================
def process_single_image(file, model, prompt_suffix):
    raw_img = Image.open(file)
    enhanced_img = enhance_image(raw_img)
    full_prompt = f"Subject: {subject}. {prompt_suffix} Generate Anki cards in JSON."
    
    try:
        response = model.generate_content([enhanced_img, full_prompt])
        clean_json = re.sub(r'```json|```', '', response.text).strip()
        return json.loads(clean_json)
    except Exception as e:
        return f"Error: {str(e)}"

# ================================================
# MAIN UI
# ================================================
st.title("🎓 AI Anki Generator PRO (2.5-Flash-Lite)")

uploaded_files = st.file_uploader("📸 Batch Upload Images", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files and api_key:
    if st.button("🚀 Process Batch (Concurrent)"):
        genai.configure(api_key=api_key)
        
        # RESTORED MODEL STASIS
        model = genai.GenerativeModel(
            model_name='gemini-2.5-flash-lite',
            system_instruction=SYSTEM_INSTRUCTION,
            generation_config={"response_mime_type": "application/json"}
        )
        
        prompt_suffix = f"Language: {language}."
        if eli5_mode: prompt_suffix += " Use very simple language (ELI5)."

        # Feature 13: Concurrent Batching
        with st.status("Processing images concurrently...", expanded=True) as status:
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(process_single_image, f, model, prompt_suffix) for f in uploaded_files]
                
                for future in futures:
                    result = future.result()
                    if isinstance(result, list):
                        for card in result:
                            st.session_state['generated_cards'].append({
                                "Question": card.get('question', ''),
                                "Answer": markdown_to_html(card.get('answer', '')),
                                "Tags": f"{fixed_tag} { ' '.join(card.get('suggested_tags', [])) }"
                            })
                    else:
                        st.error(result)
            status.update(label="✅ Batch Processing Complete!", state="complete")

# ================================================
# REVIEW & EXPORT
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    df = pd.DataFrame(st.session_state['generated_cards'])
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    st.session_state['generated_cards'] = edited_df.to_dict('records')

    # Feature 20: Visual Preview + Single Copy Logic
    st.subheader("👀 Preview & Copy")
    for idx, c in enumerate(st.session_state['generated_cards'][:10]):
        with st.container():
            st.markdown(f"""
                <div class="anki-card">
                    <div class="anki-front"><b>Q:</b> {c['Question']}</div>
                    <div class="anki-back"><b>A:</b> {c['Answer']}</div>
                    <div style="margin-top:10px;"><span class="tag-pill">{c['Tags']}</span></div>
                </div>
            """, unsafe_allow_html=True)
            if st.button(f"📋 Copy Question {idx+1}", key=f"copy_{idx}"):
                st.write(f"Copied to clipboard: {c['Question']}") # Streamlit fallback

    # CSV Export
    output = io.StringIO()
    writer = csv.writer(output)
    for c in st.session_state['generated_cards']:
        writer.writerow([c['Question'], c['Answer'], c['Tags']])
    
    st.download_button("📥 Download Anki CSV", output.getvalue(), 
                       file_name=f"{subject}_anki.csv", mime="text/csv", type="primary")

