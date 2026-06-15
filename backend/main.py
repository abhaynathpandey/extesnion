import os
import json
import base64
import asyncio
import httpx
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    print("WARNING: GEMINI_API_KEY not found in .env file")

app = Flask(__name__)
# Allow CORS for Chrome Extension
CORS(app)

async def fetch_image(client: httpx.AsyncClient, url: str) -> dict:
    try:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        mime_type = response.headers.get("content-type", "image/jpeg")
        base64_data = base64.b64encode(response.content).decode('utf-8')
        return {
            "mime_type": mime_type,
            "data": base64_data
        }
    except Exception as e:
        print(f"Error fetching image {url}: {e}")
        return None

async def process_analysis(title, attributes, imageUrls):
    prompt = f"""
      You are a strict data consistency checker.
      I am providing you with the text attributes and title for a product, along with its images.
      
      Product Title: {title}
      Text Attributes: {json.dumps(attributes, indent=2)}
      
      Task:
      1. Carefully examine the provided images to extract any readable specifications (e.g., size, weight, quantity/pack size, color, brand, model).
      2. Compare these visually extracted specifications against the provided 'Text Attributes' and 'Product Title'.
      3. Identify any clear contradictions. (e.g., if the image shows a "2-Pack" but the text says "Count: 1", that is a contradiction).
      4. DO NOT flag missing information as a contradiction. Only flag direct contradictions.
      
      Respond STRICTLY with a JSON object in the following format, with no markdown formatting or backticks:
      {{
        "hasInconsistency": boolean,
        "inconsistencies": [
          {{
            "field": "string (the attribute or spec in question)",
            "imageValue": "string (what the image shows)",
            "textValue": "string (what the text says)",
            "reason": "string (brief explanation)"
          }}
        ]
      }}
    """

    # Fetch images concurrently
    image_parts = []
    async with httpx.AsyncClient() as client:
        tasks = [fetch_image(client, url) for url in imageUrls]
        fetched_images = await asyncio.gather(*tasks)
        
        for img in fetched_images:
            if img:
                image_parts.append(img)

    if not image_parts:
        return {"status": "error", "message": "Could not fetch any images."}

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        contents = [prompt]
        for img in image_parts:
            contents.append(img)
            
        response = model.generate_content(contents)
        
        # Clean up potential markdown formatting
        text = response.text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        
        return {"status": "success", "data": json.loads(text)}
        
    except json.JSONDecodeError:
        print("Failed to parse JSON:", text)
        return {"status": "error", "message": "Invalid JSON response from AI"}
    except Exception as e:
        print("Analysis Error:", e)
        return {"status": "error", "message": str(e)}

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "running", "message": "DupCheck Backend is active and listening."})

@app.route("/api/analyze-column", methods=["POST"])
def analyze_column():
    req_data = request.get_json()
    title = req_data.get("title", "")
    attributes = req_data.get("attributes", {})
    imageUrls = req_data.get("imageUrls", [])

    if not imageUrls:
        return jsonify({"status": "no_images", "message": "No image URLs provided for analysis."})
        
    # Run async logic inside Flask's sync route
    result = asyncio.run(process_analysis(title, attributes, imageUrls))
    
    if result.get("status") == "error":
        return jsonify(result), 500
        
    return jsonify(result)

if __name__ == "__main__":
    print("DupCheck backend running on http://localhost:8000")
    # Run Flask on port 8000 to match previous FastAPI config
    app.run(host="0.0.0.0", port=8000, debug=True)
