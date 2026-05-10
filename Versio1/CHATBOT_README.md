# 🤖 Damm Logistics Chatbot - Integration Guide

This branch contains **only the chatbot/bot assistant** components. Merge this into your main project to add Gemini-powered logistics Q&A capability.

## 📦 What's Included

**Core Bot Files:**
- `bot_logic.py` - Gemini integration + logistics data parsing
- `api_server.py` - Flask API server (localhost:5000)
- `requirements.txt` - All Python dependencies
- `.env.example` - Template for API credentials

**Testing & Utilities:**
- `test_logistics.py` - Local bot testing (no server needed)
- `check_models.py` - Diagnose available Gemini models
- `start_bot_server.bat` - Windows launcher

**Documentation:**
- `SETUP.md` - Detailed setup instructions

## 🚀 Quick Integration

### 1. Merge this branch into your main code
```bash
git checkout main
git merge chatbot
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

Or on Windows if pip doesn't work:
```bash
python -m pip install -r requirements.txt
```

### 3. Setup API key
```bash
# Copy template
cp .env.example .env

# Edit .env and add your Gemini API key
GEMINI_API_KEY=your_key_here
```

Get a free API key: https://makersuite.google.com/app/apikey

### 4. Start the bot server
```bash
# Option A: Batch file (Windows)
.\start_bot_server.bat

# Option B: Direct command
python api_server.py
```

Server runs on: `http://localhost:5000`

## 🔌 How to Call the Bot

**From your code:**

```python
from bot_logic import parse_logistics_json, ask_logistics_bot

# Load and parse logistics data
logistics_data = parse_logistics_json("your_json_file.json")

# Ask a question
answer = ask_logistics_bot("¿Cuantas paradas hay?", logistics_data)
print(answer)
```

**From your frontend (JavaScript):**

```javascript
const question = "¿Cual es la parada más lejana?";

fetch('http://localhost:5000/api/ask-bot', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({question})
})
.then(r => r.json())
.then(data => console.log(data.answer));
```

## 📊 API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/ask-bot` | Send question, get answer |
| GET | `/api/logistics-data` | Get parsed logistics data |
| GET | `/api/health` | Check if server is running |

**Example POST request:**
```bash
curl -X POST http://localhost:5000/api/ask-bot \
  -H "Content-Type: application/json" \
  -d '{"question":"¿Cuantas paradas hay?"}'
```

## 📝 Configurable Parts

**Change the model** (bot_logic.py):
```python
model_name="gemini-2.5-flash"  # Change to any available Gemini model
```

**Change the system prompt** (bot_logic.py):
```python
system_instruction=(
    "Your custom instructions here..."
)
```

**Change the data source** (bot_logic.py):
Replace `parse_logistics_json("result.json")` with your data loading logic.

## 🧪 Testing

**Test without running server:**
```bash
python test_logistics.py
```

**Check available models:**
```bash
python check_models.py
```

## ⚙️ Dependencies

- `flask` - Web framework for API
- `flask-cors` - Cross-origin requests support
- `python-dotenv` - Environment variable management
- `google-generativeai` - Gemini API client

## 🔒 Security Notes

- ✅ `.env` is in `.gitignore` (API key won't be committed)
- ✅ `.env.example` shows team what's needed
- ✅ CORS enabled for dashboard integration
- ⚠️ Don't expose `api_server.py` to public internet without authentication

## 🐛 Troubleshooting

**"Model not found" error:**
```bash
python check_models.py  # See available models
```

**"Cannot import genai":**
```bash
python -m pip install google-generativeai
```

**Port 5000 already in use:**
Edit `api_server.py` line:
```python
app.run(debug=True, port=5001)  # Change to different port
```

## 📈 Next Steps

1. Integrate `api_server.py` into your main application server
2. Connect your frontend to `/api/ask-bot` endpoint
3. Replace `parse_logistics_json()` with your real data source
4. Add authentication for production use
5. Deploy to cloud (Heroku, AWS, Azure, etc.)

## 📞 Questions?

Check `SETUP.md` for more details.

---

**Branch Version:** Pilot v1.0  
**Last Updated:** May 2026  
**Status:** ✅ Ready for integration
