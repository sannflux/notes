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

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# Initialize Session State for preserving cards across Streamlit reruns
if 'generated_cards' not in st.session_state:
    st.session_state['generated_cards'] = []

# Custom CSS for Anki-like Previews
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
    </style>
""", unsafe_allow_html=True)

# ================================================
# PRESERVATION ANCHOR: CORE HELPERS
# ================================================
def enhance_image(img):
    """Preserved logic: Contrast + Sharpen + Token Optimization."""
    img = img.convert("RGB")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    img.thumbnail((1600, 1600))
    return img

def markdown_to_html(text):
    """Preserved: HTML formatting for Anki."""
    text = str(text) # Safety cast
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    return text

# ================================================
# CATEGORY A: PROMPT AUDIT (NATIVE JSON ENFORCED)
# ================================================
SYSTEM_INSTRUCTION = """You are an expert Anki flashcard creator acting as a university professor.
IMAGE ANALYSIS: Transcribe and analyze the content.
CHAIN-OF-THOUGHT: Identify core concepts, then generate cards.

CARD RULES:
- Questions test understanding/application.
- Answer uses ONLY <b> and <i> tags. NEVER ** or *.
- If notes are short, supplement with standard accurate knowledge.

OUTPUT RULES:
You must return ONLY a JSON array of objects. Exactly like this:
[
  {"question": "What is X?", "answer": "X is <b>Y</b>.", "suggested_tags": ["tag1"]}
]
"""

# ================================================
# SIDEBAR CONFIGURATION
# ================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ API Key loaded from Secrets")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")
        st.info("💡 Tip: Add 'GEMINI_API_KEY' to your Streamlit Secrets.")
    
    subject = st.text_input("Subject:", value="Biology")
    fixed_tag = st.text_input("Fixed Tag (Auto-sanitized):", value="#Medical_2024").replace(" ", "_")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual (En+Indo)"])
    card_target = st.slider("Target Cards per Image:", min_value=5, max_value=30, value=15)
    
    if st.button("🗑️ Clear Memory & Reset"):
        st.session_state['generated_cards'] = []
        st.rerun()

    st.divider()
    st.info("Model: gemini-2.5-flash-lite")

# ================================================
# MAIN UI & LOGIC
# ================================================
st.title("🎓 AI Anki Flashcard Generator PRO")

uploaded_files = st.file_uploader("📸 Upload Image(s)", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files:
    if not api_key:
        st.warning("⚠️ Please provide an API Key in the sidebar or secrets to continue.")
    else:
        if st.button("🚀 Process & Generate Cards"):
            genai.configure(api_key=api_key)
            
            # Category A: Native JSON Mode enabled here
            model = genai.GenerativeModel(
                model_name='gemini-2.5-flash-lite',
                system_instruction=SYSTEM_INSTRUCTION,
                generation_config={"response_mime_type": "application/json"}
            )
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            new_cards_count = 0
            
            for idx, file in enumerate(uploaded_files):
                status_text.text(f"Processing image {idx+1}/{len(uploaded_files)}...")
                
                raw_img = Image.open(file)
                enhanced_img = enhance_image(raw_img)
                
                lang_instr = f"Language: {language}."
                prompt = f"Subject: {subject}. {lang_instr} Generate approximately {card_target} Anki cards based on this image."
                
                try:
                    with st.spinner(f'AI analyzing {file.name}...'):
                        response = model.generate_content([enhanced_img, prompt])
                        
                        # Because we used Native JSON mode, response.text is guaranteed to be a JSON string
                        cards_data = json.loads(response.text)
                        
                        for card in cards_data:
                            formatted_answer = markdown_to_html(card.get('answer', ''))
                            tags = [fixed_tag] + card.get('suggested_tags', [])
                            tag_str = " ".join([t.replace(" ", "_") for t in tags])
                            
                            # Append to SESSION STATE (Lifesaver)
                            st.session_state['generated_cards'].append({
                                "Question": card.get('question', ''),
                                "Answer": formatted_answer,
                                "Tags": tag_str
                            })
                            new_cards_count += 1
                            
                except Exception as e:
                    st.error(f"Error in {file.name}: {str(e)}")
                
                progress_bar.progress((idx + 1) / len(uploaded_files))
                
                # Category C: API Rate Guard (Protect free tier on multiple images)
                if idx < len(uploaded_files) - 1:
                    time.sleep(2)
            
            status_text.text("✅ Generation Complete!")
            st.toast(f"{new_cards_count} new cards added to memory!")

# ================================================
# REVIEW & EXPORT SECTION
# ================================================
if st.session_state['generated_cards']:
    st.divider()
    st.subheader(f"📝 Review & Edit ({len(st.session_state['generated_cards'])} Cards Total)")
    
    # Category B: Interactive Data Editor
    st.info("💡 You can click inside the table below to edit typos or change tags before downloading!")
    
    # Convert session state to pandas dataframe for the editor
    df = pd.DataFrame(st.session_state['generated_cards'])
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    
    # Sync edits back to session state in case they add/delete rows
    st.session_state['generated_cards'] = edited_df.to_dict('records')

    # Visual Preview (Top 3)
    with st.expander("👀 View Visual Anki Card Preview"):
        for c in st.session_state['generated_cards'][:3]:
            st.markdown(f"""
                <div class="anki-card">
                    <div class="anki-front">{c['Question']}</div>
                    <div class="anki-back">{c['Answer']}</div>
                    <div style="margin-top:10px;"><span class="tag-pill">{c['Tags']}</span></div>
                </div>
            """, unsafe_allow_html=True)
            
    # CSV Preparation
    output = io.StringIO()
    writer = csv.writer(output)
    for c in st.session_state['generated_cards']:
        writer.writerow([c['Question'], c['Answer'], c['Tags']])
    
    # Category D: Dynamic Filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_subject = re.sub(r'[^a-zA-Z0-9]', '_', subject.lower())
    filename = f"{safe_subject}_anki_{timestamp}.csv"
    
    col1, col2 = st.columns([1, 3])
    with col1:
        st.download_button(
            label="📥 Download Anki CSV",
            data=output.getvalue(),
            file_name=filename,
            mime="text/csv",
            type="primary"
        )
    with col2:
        with st.popover("ℹ️ How to Import into Anki"):
            st.write("1. File → Import → select the downloaded `.csv`")
            st.write("2. Field 1=Front, Field 2=Back, Field 3=Tags")
            st.write("3. **Crucial:** Check 'Allow HTML in fields'")

