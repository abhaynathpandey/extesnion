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
        model = genai.GenerativeModel('gemini-3.5-flash')
        
        contents = [prompt]
        for img in image_parts:
            contents.append(img)
            
        response = model.generate_content(contents)
        
        if not response.text:
            print("AI Raw Response: no response by ai")
            return {"status": "error", "message": "no response by ai"}
            
        # Clean up potential markdown formatting
        text = response.text.strip()
        print(f"AI Raw Response: \n{text}\n")
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

@app.route("/test-ai", methods=["GET"])
def test_ai():
    # Dummy data for testing the AI directly in the browser
    title = "Bulbasaur"
    attributes = {"Type": "Grass", "Color": "Blue"}
    imageUrls = ["https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/1.png"]
    
    result = asyncio.run(process_analysis(title, attributes, imageUrls))
    
    # Return formatted HTML to view easily in the browser
    html_response = f"""
    <html>
        <body style="font-family: monospace; background: #1e1e1e; color: #00ff00; padding: 20px;">
            <h2>AI Vision Test Result</h2>
            <pre>{json.dumps(result, indent=4)}</pre>
        </body>
    </html>
    """
    return html_response

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

async def process_batch_analysis(products):
    prompt = """
    You are a strict data consistency and duplicate checker.
    I am providing you with multiple product listings. Each product has a product_id, title, description, text attributes, and images.
    
    Task Phase 1: Vertical Check (Bad Data)
    For each product individually, compare its visually extracted specifications (from its images) against its 'Text Attributes', 'Product Title', and 'Description'.
    Identify any clear contradictions (e.g., image shows "2-Pack" but text says "Count: 1", or description mentions "Stainless Steel" but specs say "Plastic"). If there is a contradiction between any of these fields (image vs text, or description vs specs), flag it as having 'bad data'.

    Task Phase 2: Horizontal Check (Duplicate Clustering)
    For all products that DO NOT have bad data (i.e., they passed Phase 1), compare them against each other using their Titles, Descriptions, Attributes, and Images.
    Determine if they are identical items (duplicates), variants, or completely different items.
    Group similar/identical items into clusters. If a product is unique, it goes into its own cluster.
    
    Respond STRICTLY with a JSON object in the following format, with no markdown formatting:
    {
      "vertical_checks": [
        {
          "product_id": "string",
          "has_bad_data": boolean,
          "reason": "string (brief explanation if bad data is found, else empty)",
          "mismatch_details": [
            {
              "field": "string (the attribute in question)",
              "imageValue": "string",
              "textValue": "string"
            }
          ]
        }
      ],
      "horizontal_clustering": [
        {
          "cluster_name": "string (e.g., 'Cluster 1')",
          "product_ids": ["string", "string"],
          "reason": "string (explain why these products are grouped together)"
        }
      ]
    }
    """
    
    contents = [prompt]
    
    async with httpx.AsyncClient() as client:
        for p in products:
            prod_id = p.get('id', 'Unknown')
            prod_text = f"\n\n--- PRODUCT ID: {prod_id} ---\nTitle: {p.get('title')}\nDescription: {p.get('description', '')}\nAttributes: {json.dumps(p.get('attributes', {}), indent=2)}\nImages for {prod_id}:"
            contents.append(prod_text)
            
            urls = p.get('imageUrls', [])
            tasks = [fetch_image(client, url) for url in urls]
            fetched_images = await asyncio.gather(*tasks)
            
            has_img = False
            for img in fetched_images:
                if img:
                    contents.append(img)
                    has_img = True
            
            if not has_img:
                contents.append("[No Images Provided for this product]")

    try:
        model = genai.GenerativeModel('gemini-3.5-flash')
        response = model.generate_content(contents)
        
        if not response.text:
            return {"status": "error", "message": "no response by ai"}
            
        text = response.text.strip().replace("```json", "").replace("```", "").strip()
        print(f"AI Batch Response: \n{text}\n")
        return {"status": "success", "data": json.loads(text)}
        
    except json.JSONDecodeError:
        print("Failed to parse JSON:", text)
        return {"status": "error", "message": "Invalid JSON response from AI"}
    except Exception as e:
        print("Batch Analysis Error:", e)
        return {"status": "error", "message": str(e)}

@app.route("/api/analyze-batch", methods=["POST"])
def analyze_batch():
    req_data = request.get_json()
    products = req_data.get("products", [])
    
    print(f"Received batch analysis request for {len(products)} products")
    
    if not products:
        return jsonify({"status": "error", "message": "No products provided for analysis."}), 400
        
    result = asyncio.run(process_batch_analysis(products))
    
    if result.get("status") == "error":
        return jsonify(result), 500
        
    return jsonify(result)

if __name__ == "__main__":
    print("DupCheck backend running on http://localhost:8000")
    # Run Flask on port 8000 to match previous FastAPI config
    app.run(host="0.0.0.0", port=8000, debug=True)
