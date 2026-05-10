import os
import json
from dotenv import load_dotenv
import google.generativeai as genai

# 1. This line looks into your .env file and 'loads' the key
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# 2. Connect to the Google Gemini API
genai.configure(api_key=api_key)
client = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=(
        "You are a friendly logistics assistant for Damm. "
        "Answer questions about routes, stops, deliveries, and costs in a concise, human-friendly way. "
        "RULES: "
        "1. Provide ONLY the final answer - skip showing calculations, formulas, or reasoning. "
        "2. Format answers naturally (like talking to a colleague). "
        "3. When listing stops, use: Company Name, Address (City, Postal Code). "
        "4. Be brief and direct - one or two sentences when possible. "
        "5. Only respond to logistics questions. For technical questions, say: 'Solo puedo ayudarte con logística.'"
    )
)

# 3. Create chat session
chat = client.start_chat(history=[])

def parse_logistics_json(file_path):
    """
    Parse logistics JSON file and format it for the bot.
    
    Args:
        file_path: Path to the JSON file
        
    Returns:
        Formatted string with logistics data ready for the bot
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Build formatted output for the bot
        formatted_data = f"""
RUTA: {data.get('ruta', 'N/A')} | FECHA: {data.get('fecha', 'N/A')}
DEPÓSITO: {data['depot']['name']} | Coords: ({data['depot']['lat']}, {data['depot']['lon']})

PARADAS:
"""
        
        for idx, stop in enumerate(data.get('stops', []), 1):
            formatted_data += f"\n{idx}. {stop['name']} | {stop['address']}, {stop['city']} {stop['cp']} | Coords: ({stop['lat']}, {stop['lon']})"
            formatted_data += f"\n   Entrega: {stop['delivery_caj']} cajas{f', {stop['delivery_brl']} barriles' if stop['delivery_brl'] > 0 else ''} | Retorno: {stop['ret_caj']} cajas{f', {stop['ret_brl']} barriles' if stop['ret_brl'] > 0 else ''}"
        
        return formatted_data
    
    except FileNotFoundError:
        return f"Error: File '{file_path}' not found."
    except json.JSONDecodeError:
        return f"Error: Invalid JSON in '{file_path}'."
    except KeyError as e:
        return f"Error: Missing expected field in JSON - {e}"

def ask_logistics_bot(user_message, transport_data):
    """
    Pass the user message and the current transport data 
    (from your dashboard) to the bot.
    """
    full_prompt = f"Data: {transport_data}\n\nQuestion: {user_message}"
    response = chat.send_message(full_prompt)
    return response.text

# Example usage:
# logistics_data = parse_logistics_json("result.json")
# response = ask_logistics_bot("What are the total deliveries for this route?", logistics_data)
# print(response)