"""
Quick test script for the parse_logistics_json function
Run this to test before integrating into the dashboard
"""

from bot_logic import parse_logistics_json, ask_logistics_bot

# Test the parser
print("=" * 60)
print("TESTING: parse_logistics_json()")
print("=" * 60)

formatted_data = parse_logistics_json("result.json")
print(formatted_data)

print("\n" + "=" * 60)
print("TESTING: ask_logistics_bot() with parsed data")
print("=" * 60)

# Test a few sample questions
questions = [
    "¿Cuantas paradas hay en la ruta?",
    "¿Cual es la parada con más cajas a entregar?",
    "¿Cuantos barriles totales hay que retornar?",
]

for q in questions:
    print(f"\nQ: {q}")
    try:
        response = ask_logistics_bot(q, formatted_data)
        print(f"A: {response}\n")
    except Exception as e:
        print(f"Error: {e}\n")
