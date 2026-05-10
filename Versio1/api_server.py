"""
Simple Flask API to expose the logistics bot to the web dashboard
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import json
from bot_logic import parse_logistics_json, ask_logistics_bot

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Load the logistics data once on startup
try:
    logistics_data = parse_logistics_json("result.json")
except Exception as e:
    logistics_data = f"Error loading logistics data: {e}"

@app.route('/api/ask-bot', methods=['POST'])
def ask_bot():
    """
    Endpoint for dashboard to ask questions to the logistics bot
    Expected JSON: {"question": "¿Cuantas paradas hay?"}
    """
    try:
        data = request.json
        user_question = data.get('question', '')
        
        if not user_question:
            return jsonify({"error": "No question provided"}), 400
        
        # Get bot response
        response = ask_logistics_bot(user_question, logistics_data)
        
        return jsonify({
            "question": user_question,
            "answer": response,
            "success": True
        })
    
    except Exception as e:
        return jsonify({
            "error": str(e),
            "success": False
        }), 500

@app.route('/api/logistics-data', methods=['GET'])
def get_logistics_data():
    """
    Endpoint to retrieve parsed logistics data
    """
    try:
        return jsonify({
            "data": logistics_data,
            "success": True
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "success": False
        }), 500

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    print("🚚 Logistics Bot API starting...")
    print("📍 Available at: http://localhost:5000")
    print("🔗 API Endpoints:")
    print("   POST /api/ask-bot - Send a question to the bot")
    print("   GET  /api/logistics-data - Get parsed logistics data")
    print("   GET  /api/health - Health check")
    app.run(debug=True, port=5000)
