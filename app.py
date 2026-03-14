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
    """Preserved from original script: Contrast + Sharpen."""
    img = img.convert("RGB")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(3.0)
    img = img.filter(ImageFilter.SHARPEN)
    # Performance Optimization: Resize for token efficiency
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
# CATEGORY A: PROMPT AUDIT & ENHANCEMENT
# ================================================
SYSTEM_INSTRUCTION = """You are an expert Anki flashcard creator.
IMAGE ANALYSIS: Transcribe and analyze the content.
CHAIN-OF-THOUGHT: First, identify the core concepts. Then, create cards.

CARD RULES:
- Questions test understanding/application.
- Answer uses ONLY <b> and <i> tags. NEVER ** or *.
- If notes are short, supplement with standard accurate knowledge.

OUTPUT RULES (STRICT JSON):
Return a JSON array of objects. Each object must have:
"question": "string",
"answer": "string",
"suggested_tags": ["list", "of", "strings"]

NEGATIVE CONSTRAINT: Do not include intro/outro text. Return ONLY the JSON array.
"""

# ================================================
# SIDEBAR CONFIGURATION
# ================================================
with st.sidebar:
    st.title("⚙️ Configuration")
    
    # API SECRETS INTEGRATION
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("API Key loaded from Secrets")
    except:
        api_key = st.text_input("Gemini API Key:", type="password", help="Add this to .streamlit/secrets.toml for auto-load")
    
    subject = st.text_input("Subject:", value="Biology")
    fixed_tag = st.text_input("Fixed Tag:", value="#Medical_2024")
    language = st.selectbox("Language:", ["English", "Bahasa Indonesia", "Bilingual (En+Indo)"])
    
    st.divider()
    st.info("Model: gemini-2.5-flash-lite (Stasis)")

# ================================================
# MAIN UI & LOGIC
# ================================================
st.title("🎓 AI Anki Flashcard Generator")
st.write("Upload notes (images) to generate high-quality CSV cards for Anki.")

uploaded_files = st.file_uploader("📸 Upload Image(s)", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

if uploaded_files:
    if not api_key:
        st.error("Please provide an API Key in the sidebar or secrets!")
    else:
        # Step 1: Pre-processing & OCR (CoT)
        if st.button("🚀 Process Images & Generate Cards"):
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
                
                # Image Prep
                raw_img = Image.open(file)
                enhanced_img = enhance_image(raw_img)
                
                # Prompt Construction
                lang_instr = f"Language: {language}. "
                prompt = f"Subject: {subject}. {lang_instr} Generate Anki cards from this image in JSON format."
                
                try:
                    # Async-like Status container
                    with st.spinner('AI is analyzing content...'):
                        response = model.generate_content([enhanced_img, prompt])
                        # Clean JSON string (remove markdown code blocks)
                        clean_json = re.sub(r'```json|```', '', response.text).strip()
                        cards_data = json.loads(clean_json)
                        
                        for card in cards_data:
                            # Apply HTML formatting
                            formatted_answer = markdown_to_html(card['answer'])
                            # Merge fixed tag with AI suggested tags
                            tags = [fixed_tag] + card.get('suggested_tags', [])
                            tag_str = " ".join([t.replace(" ", "_") for t in tags])
                            
                            all_generated_cards.append({
                                "q": card['question'],
                                "a": formatted_answer,
                                "tags": tag_str
                            })
                except Exception as e:
                    st.error(f"Error processing {file.name}: {e}")
                
                progress_bar.progress((idx + 1) / len(uploaded_files))
            
            status_text.text("✅ Generation Complete!")
            st.toast("Cards generated successfully!")
            
            # Step 2: Live Preview (Category B)
            if all_generated_cards:
                st.subheader("👀 Live Card Preview")
                for c in all_generated_cards[:5]: # Show first 5 as preview
                    st.markdown(f"""
                        <div class="anki-card">
                            <div class="anki-front">{c['q']}</div>
                            <div class="anki-back">{c['a']}</div>
                            <div style="margin-top:10px;"><span class="tag-pill">{c['tags']}</span></div>
                        </div>
                    """, unsafe_allow_html=True)
                
                if len(all_generated_cards) > 5:
                    st.write(f"... and {len(all_generated_cards)-5} more cards.")

                # Step 3: CSV Generation & Download
                output = io.StringIO()
                writer = csv.writer(output)
                for c in all_generated_cards:
                    writer.writerow([c['q'], c['a'], c['tags']])
                
                st.download_button(
                    label="📥 Download Anki CSV",
                    data=output.getvalue(),
                    file_name=f"{subject.lower()}_anki_cards.csv",
                    mime="text/csv"
                )

# ================================================
# INSTRUCTIONS
# ================================================
with st.expander("ℹ️ How to Import to Anki"):
    st.write("""
    1. Open Anki → **File** → **Import**.
    2. Select the downloaded `.csv` file.
    3. **Field Mapping**: 
       - Field 1: Front
       - Field 2: Back
       - Field 3: Tags
    4. Ensure **'Allow HTML in fields'** is checked.
    5. Click **Import**.
    """)

