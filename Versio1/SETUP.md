# Logistics Bot Integration Guide

## Setup Steps

### 1. **Install Flask** (for the API)
```bash
pip install flask
```

### 2. **Test the parsing function** (optional but recommended)
```bash
python test_logistics.py
```
This will show you the parsed JSON output and test the bot with sample questions.

### 3. **Start the API server**
Open a terminal in the `Versio1/` directory and run:
```bash
python api_server.py
```

You should see:
```
🚚 Logistics Bot API starting...
📍 Available at: http://localhost:5000
```

### 4. **Open the dashboard**
Open `index.html` in your browser and navigate to the **Dashboard** tab.

You'll see a new **"Logistics Bot"** section at the bottom with:
- Chat messages area
- Input field to ask questions
- Status indicator (✓ Connected / ✗ Desconectado)

### 5. **Test the bot**
Try asking questions like:
- "¿Cuantas paradas hay en la ruta?"
- "¿Cual es la parada con más cajas a entregar?"
- "¿Cuanto tiempo ahorra la ruta optimizada?"
- "¿Cuál es la distancia total de la ruta?"

---

## Architecture

```
Dashboard (index.html)
    ↓
JavaScript API Client (bot chat widget)
    ↓
Flask API Server (api_server.py)
    ↓
Python Logic (bot_logic.py)
    ↓
Gemini API (your .env GEMINI_API_KEY)
```

---

## Files Created/Modified

1. **bot_logic.py** (modified)
   - Added `parse_logistics_json()` function
   - Existing `ask_logistics_bot()` function

2. **api_server.py** (new)
   - Flask API with 3 endpoints
   - Loads logistics data on startup
   - Serves bot responses

3. **test_logistics.py** (new)
   - Standalone test script
   - Tests parsing and bot Q&A

4. **index.html** (modified)
   - Added bot chat CSS styles
   - Added bot HTML widget
   - Added JavaScript functions to call API

---

## Troubleshooting

### "Cannot connect to API"
- Make sure `api_server.py` is running
- Check the port (default: 5000)
- Firewall may be blocking localhost:5000

### Bot gives generic/unhelpful answers
- The bot uses your GEMINI_API_KEY from .env
- Make sure your API key is valid
- The system prompt limits it to logistics questions only

### CORS Issues (if accessing from different domain)
Add to `api_server.py`:
```python
from flask_cors import CORS
CORS(app)
```

---

## Next Steps

You can now:
✅ Parse logistics JSON and format for the bot
✅ Ask questions about routes, stops, deliveries
✅ Display bot responses in the dashboard
✅ Expand with more dashboard integrations

Future enhancements:
- Upload new JSON files and re-parse
- Export bot conversation as PDF
- Integration with optimization engine
- Multi-language support (ask in Spanish, Catalan, etc.)
