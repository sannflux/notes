import streamlit as st
import google.generativeai as genai
import json
import io
from PIL import Image

# --- CONFIGURATION & SESSION STATE ---
st.set_page_config(page_title="Gemini Anki Optimizer", page_icon="🎴", layout="wide")

if "generated_cards" not in st.session_state:
    st.session_state.generated_cards = []

# --- PRESERVATION ANCHOR: MODEL STASIS ---
# Keeping Gemini 1.5 Flash as the core engine
MODEL_NAME = "gemini-1.5-flash"

def initialize_gemini(api_key):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL_NAME)

def compress_image(image_file):
    """Reduces image size to save API tokens and prevent exhaustion."""
    img = Image.open(image_file)
    # Convert to RGB if necessary
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    # Resize if too large (maintains aspect ratio)
    max_size = 1024
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG', quality=80)
    return img_byte_arr.getvalue()

# --- FEATURE 13: OPTIMIZED PROMPT ARCHITECTURE ---
SYSTEM_PROMPT = """
Target: Senior Anki Content Creator.
Task: Extract educational data from the image/text and format into Anki Flashcards.
Output Format: STRICT JSON ARRAY of objects.
[{"front": "string", "back": "string"}]

Rules:
1. Identify the core concept (e.g., Algebra, Variables).
2. Create "Atomic" cards (one idea per card).
3. For math: Use LaTeX style if needed or clear plain text.
4. Language: Match the input language (e.g., Malay/Indonesian for this user).
5. Chain-of-Thought: First, identify the rules mentioned (like 'symbols must represent the same value'), then create the card.
"""

# --- UI INTERFACE ---
st.title("🚀 Expert Gemini Anki Generator")
st.markdown("### Feature-Enhanced Version (v2.0)")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Enter Gemini API Key", type="password")
    st.info("Optimization: Images are compressed before being sent to save API tokens.")

uploaded_file = st.file_uploader("Upload your notes (Image)", type=["png", "jpg", "jpeg"])

if uploaded_file and api_key:
    if st.button("Generate Flashcards"):
        model = initialize_gemini(api_key)
        
        # FEATURE 4: PROGRESS ORCHESTRATOR
        with st.status("Processing your notes...", expanded=True) as status:
            st.write("Compressing image for token efficiency...")
            optimized_image_bytes = compress_image(uploaded_file)
            
            st.write("Gemini 1.5 Flash analyzing content...")
            # Preparing the payload
            contents = [
                SYSTEM_PROMPT,
                {"mime_type": "image/jpeg", "data": optimized_image_bytes}
            ]
            
            try:
                response = model.generate_content(contents)
                
                # FEATURE 3: DATA PERSISTENCE
                raw_text = response.text
                # Clean JSON in case model adds backticks
                clean_json = raw_text.replace("```json", "").replace("```", "").strip()
                st.session_state.generated_cards = json.loads(clean_json)
                
                status.update(label="Flashcards Generated!", state="complete", expanded=False)
            except Exception as e:
                st.error(f"API Error: {str(e)}")
                status.update(label="Error Occurred", state="error")

# --- FEATURE 5 & 20: OUTPUT & BATCH DISPLAY ---
if st.session_state.generated_cards:
    st.divider()
    st.subheader("Generated Anki Cards")
    
    # Show cards in a clean grid
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state.generated_cards):
        with cols[idx % 2]:
            with st.container(border=True):
                st.markdown(f"**Front:** {card['front']}")
                st.markdown(f"**Back:** {card['back']}")

    # Export Options
    st.divider()
    col_dl1, col_dl2 = st.columns(2)
    
    # Create TXT for Anki Import
    anki_txt = "\n".join([f"{c['front']};{c['back']}" for c in st.session_state.generated_cards])
    
    col_dl1.download_button(
        label="Download for Anki (.txt import)",
        data=anki_txt,
        file_name="anki_cards.txt",
        mime="text/plain"
    )
    
    if col_dl2.button("Clear All Cards"):
        st.session_state.generated_cards = []
        st.rerun()

elif uploaded_file:
    st.info("Click 'Generate Flashcards' to begin analysis.")
