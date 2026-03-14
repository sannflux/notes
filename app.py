import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageEnhance, ImageFilter
import csv
import io
import re
import json

# ================================================
# CONFIGURATION & THEME
# ================================================
st.set_page_config(page_title="AI Anki Generator PRO", page_icon="🎓", layout="wide")

# Custom CSS for Anki-like Previews and Dark Mode UI
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
    """Preserved logic: Contrast + Sharpen."""
    img = img.convert("RGB")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    # Optimization: Resize for token efficiency
    img.thumbnail((1600, 1600))
    return img

def markdown_to_html(text):
    """Preserved: HTML formatting for Anki."""
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    return text

# ================================================
# CATEGORY A: PROMPT AUDIT (JSON + COT)
# ================================================
SYSTEM_INSTRUCTION = """You are an expert Anki flashcard creator.
IMAGE ANALYSIS: Transcribe and analyze the content.
CHAIN-OF-THOUGHT: Identify core concepts, then generate cards.

CARD RULES:
- Questions test understanding/application.
- Answer uses ONLY <b> and <i> tags. NEVER ** or *.
- If notes are short, supplement with standard accurate knowledge.

OUTPUT RULES (STRICT JSON):
Return a JSON array of objects ONLY.
[{"question": "string", "answer": "string", "suggested_tags": ["tag1"]}]
"""

# ================================================
# SIDEBAR CONFIGURATION
# ================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    
    # API SECRETS INTEGRATION
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ API Key loaded from Secrets")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")
        st.info("💡 Tip: Add 'GEMINI_API_KEY' to your Streamlit Secrets to skip this.")
    
    subject = st.text_input("Subject:", value="Biology")
    fixed_tag = st.text_input("Fixed Tag:", value="#Medical_2024")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual (En+Indo)"])
    
    st.divider()
    st.info("Model: gemini-2.5-flash-lite")

# ================================================
# MAIN UI & LOGIC
# ================================================
st.title("🎓 AI Anki Flashcard Generator")

uploaded_files = st.file_uploader("📸 Upload Image(s)", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files:
    if not api_key:
        st.warning("⚠️ Please provide an API Key in the sidebar or secrets to continue.")
    else:
        if st.button("🚀 Process & Generate Cards"):
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name='gemini-2.5-flash-lite',
                system_instruction=SYSTEM_INSTRUCTION
            )
            
            all_generated_cards = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for idx, file in enumerate(uploaded_files):
                status_text.text(f"Processing image {idx+1}/{len(uploaded_files)}...")
                
                raw_img = Image.open(file)
                enhanced_img = enhance_image(raw_img)
                
                lang_instr = f"Language: {language}."
                prompt = f"Subject: {subject}. {lang_instr} Generate Anki cards in JSON."
                
                try:
                    with st.spinner('Analyzing content...'):
                        response = model.generate_content([enhanced_img, prompt])
                        # Clean JSON and parse
                        clean_json = re.sub(r'```json|```', '', response.text).strip()
                        cards_data = json.loads(clean_json)
                        
                        for card in cards_data:
                            formatted_answer = markdown_to_html(card['answer'])
                            tags = [fixed_tag] + card.get('suggested_tags', [])
                            tag_str = " ".join([t.replace(" ", "_") for t in tags])
                            
                            all_generated_cards.append({
                                "q": card['question'],
                                "a": formatted_answer,
                                "tags": tag_str
                            })
                except Exception as e:
                    st.error(f"Error in {file.name}: {str(e)}")
                
                progress_bar.progress((idx + 1) / len(uploaded_files))
            
            status_text.text("✅ Finished!")
            st.toast("Generation Complete!")
            
            if all_generated_cards:
                st.subheader("👀 Preview (Top 5)")
                for c in all_generated_cards[:5]:
                    st.markdown(f"""
                        <div class="anki-card">
                            <div class="anki-front">{c['q']}</div>
                            <div class="anki-back">{c['a']}</div>
                            <div style="margin-top:10px;"><span class="tag-pill">{c['tags']}</span></div>
                        </div>
                    """, unsafe_allow_html=True)

                # CSV Preparation
                output = io.StringIO()
                writer = csv.writer(output)
                for c in all_generated_cards:
                    writer.writerow([c['q'], c['a'], c['tags']])
                
                st.download_button(
                    label="📥 Download Anki CSV",
                    data=output.getvalue(),
                    file_name=f"{subject.lower()}_anki.csv",
                    mime="text/csv"
                )

# Instructions for Import
with st.expander("ℹ️ How to Import into Anki"):
    st.write("1. File → Import → select the .csv")
    st.write("2. Field 1=Front, Field 2=Back, Field 3=Tags")
    st.write("3. Check 'Allow HTML in fields'")
