import os
import csv
import sqlite3
import base64
import gradio as gr
import json
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

HERE = Path(__file__).parent

# Load environment variables
load_dotenv(HERE / ".env")

API_KEY = os.getenv("API_KEY")
API_ENDPOINT = os.getenv("LLM_API_URL")
MODEL_NAME = os.getenv("MODEL_NAME")

client = OpenAI(
    api_key=API_KEY,
    base_url=API_ENDPOINT,
    timeout=60.0
)

DB_PATH = HERE / "cafe.db"


def build_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS cafe_info")
    cursor.execute("DROP TABLE IF EXISTS menu")

    cursor.execute("""
        CREATE TABLE cafe_info (
            topic TEXT PRIMARY KEY,
            content TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE menu (
            name TEXT PRIMARY KEY,
            category TEXT,
            price REAL,
            available TEXT
        )
    """)

    with open(HERE / "cafe_info.csv", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            cursor.execute(
                "INSERT INTO cafe_info (topic, content) VALUES (?, ?)",
                (row["topic"], row["content"])
            )

    with open(HERE / "menu.csv", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            cursor.execute(
                """
                INSERT INTO menu (name, category, price, available)
                VALUES (?, ?, ?, ?)
                """,
                (
                    row["name"],
                    row["category"],
                    float(row["price"]),
                    row["available"]
                )
            )

    conn.commit()
    conn.close()


build_database()
def get_cafe_info(topic):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT content FROM cafe_info WHERE topic = ?",
        (topic,)
    )

    row = cursor.fetchone()
    conn.close()

    if row is None:
        return {"found": False, "topic": topic}

    return {
        "found": True,
        "topic": topic,
        "content": row[0]
    }


def search_menu(query):
    if not query or not query.strip():
        return {
            "found": False,
            "query": query,
            "items": [],
            "error": "Please provide a menu item or category to search for."
        }

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT name, category, price, available
        FROM menu
        WHERE LOWER(name) LIKE LOWER(?)
        OR LOWER(category) LIKE LOWER(?)
        """,
        (f"%{query}%", f"%{query}%")
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            "found": False,
            "query": query,
            "items": []
        }

    items = []

    for row in rows:
        items.append({
            "name": row[0],
            "category": row[1],
            "price": row[2],
            "available": row[3]
        })

    return {
        "found": True,
        "query": query,
        "items": items
    }


def calculate_order_total(items):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    total = 0
    details = []

    for item in items:
        name = item["name"]
        quantity = item["quantity"]

        cursor.execute(
            """
            SELECT price, available
            FROM menu
            WHERE LOWER(name) = LOWER(?)
            """,
            (name,)
        )

        row = cursor.fetchone()

        if row is None:
            details.append({
                "name": name,
                "error": "Item not found"
            })
            continue

        price = row[0]
        available = row[1]

        if available.lower() != "yes":
            details.append({
                "name": name,
                "error": "Item is not available"
            })
            continue

        subtotal = price * quantity
        total += subtotal

        details.append({
            "name": name,
            "quantity": quantity,
            "price_each": price,
            "subtotal": subtotal
        })

    conn.close()

    return {
        "items": details,
        "total": total
    }


def transfer_to_human(reason):
    return {
        "transfer": True,
        "reason": reason,
        "message": "A staff member should handle this request."
    }

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_cafe_info",
            "description": (
                "Look up general information about the cafe, such as opening hours, "
                "address, Wi-Fi, payment, parking, reservations, takeaway, allergies, "
                "pets or student discounts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "The information topic. Examples: opening_hours, address, "
                            "wifi, payment, parking, reservations, takeaway, allergies, "
                            "pets, student_discount."
                        )
                    }
                },
                "required": ["topic"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "search_menu",
            "description": (
                "Search the cafe menu for a drink, food item or category and return "
                "matching items with price and availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The item or category to search for, for example coffee, "
                            "tea, cake, latte or sandwich."
                        )
                    }
                },
                "required": ["query"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "calculate_order_total",
            "description": (
                "Calculate the total price of an order. Use this tool whenever "
                "the customer asks how much an order costs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Exact menu item name."
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": "Number of this item."
                                }
                            },
                            "required": ["name", "quantity"]
                        }
                    }
                },
                "required": ["items"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": (
                "Transfer the customer to a human staff member when they ask to speak "
                "to a person, make a complaint, or ask something that cannot be answered "
                "from the available cafe data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason why a human staff member is needed."
                    }
                },
                "required": ["reason"]
            }
        }
    }
]

def execute_tool(tool_name, arguments):
    if tool_name == "get_cafe_info":
        return get_cafe_info(**arguments)

    elif tool_name == "search_menu":
        return search_menu(**arguments)

    elif tool_name == "calculate_order_total":
        return calculate_order_total(**arguments)

    elif tool_name == "transfer_to_human":
        return transfer_to_human(**arguments)

    else:
        return {
            "error": "Unknown tool",
            "tool_name": tool_name
        }


    import json

SYSTEM_PROMPT = """
You are the AI customer service agent for a cafe.

Rules:
1. Answer only from tool results. Never invent prices, opening hours, availability or other cafe information.
2. Only answer questions related to the cafe.
3. Use get_cafe_info for general cafe information.
4. Use search_menu for menu questions.
5. Use calculate_order_total whenever the customer asks for the total price of an order.
6. Use transfer_to_human for complaints, requests for a human, or questions that cannot be answered from the available data.
7. Answer in the same language as the customer.
8. Keep answers short and helpful.
9. When using search_menu, never use an empty query. Search only for the specific item or category the customer asked about.
10. If a question is not covered by the available cafe information, do not call unrelated tools. Call transfer_to_human directly.
"""



def run_agent(user_message, chat_history=None, max_steps=5):
    if chat_history is None:
        chat_history = []

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    for item in chat_history:
        if isinstance(item, dict):
            messages.append(item)

    messages.append({
        "role": "user",
        "content": user_message
    })

    tool_trace = []

    for step in range(max_steps):
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"
        )

        message = response.choices[0].message

        if not message.tool_calls:
            return {
                "answer": message.content,
                "tool_trace": tool_trace
            }

        messages.append(message)

        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name

            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            result = execute_tool(tool_name, arguments)

            tool_trace.append({
                "tool": tool_name,
                "arguments": arguments,
                "result": result
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result)
            })

    return {
        "answer": "Sorry, I could not complete the request.",
        "tool_trace": tool_trace
    }


def chat_with_agent(message, history):
    result = run_agent(message, history)

    answer = result["answer"]
    tool_trace = result["tool_trace"]

    trace_text = ""

    if tool_trace:
        trace_text += "\n\n---\n### Tools used\n"

        for step in tool_trace:
            trace_text += f"\n**Tool:** `{step['tool']}`\n"
            trace_text += f"**Arguments:** `{step['arguments']}`\n"

            if step["tool"] == "search_menu":
                items = step["result"].get("items", [])
                if items:
                    names = [item["name"] for item in items]
                    trace_text += f"**Result:** {', '.join(names)}\n"
                else:
                    trace_text += "**Result:** No matching items found.\n"

            elif step["tool"] == "calculate_order_total":
                total = step["result"].get("total")
                trace_text += f"**Result:** Total = {total}\n"

            elif step["tool"] == "get_cafe_info":
                content = step["result"].get("content", "No information found.")
                trace_text += f"**Result:** {content}\n"

            elif step["tool"] == "transfer_to_human":
                trace_text += "**Result:** Transfer requested.\n"

            else:
                trace_text += f"**Result:** {step['result']}\n"

    return answer + trace_text

def get_background_image():
    image_path = HERE / "background.png"

    if not image_path.exists():
        return ""

    with open(image_path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode()

    return f"data:image/png;base64,{encoded}"


background_image = get_background_image()


CUSTOM_CSS = f"""
html, body {{
    margin: 0 !important;
    min-height: 100% !important;
}}

body::before {{
    content: "";
    position: fixed;
    inset: 0;

    background:
        linear-gradient(
            rgba(35, 22, 15, 0.50),
            rgba(35, 22, 15, 0.50)
        ),
        url('{background_image}');

    background-size: cover;
    background-position: center center;
    background-repeat: no-repeat;

    z-index: 0;
    pointer-events: none;
}}

.gradio-container {{
    position: relative;
    z-index: 1;
    min-height: 100vh !important;
    background: transparent !important;
}}


#cafe-header {{
    max-width: 1325px;
    margin: 10px auto 20px auto;
    text-align: center;
    padding: 20px;
    background: rgba(250, 244, 235, 0.88);
    border-radius: 24px;
    box-shadow: 0 10px 35px rgba(0, 0, 0, 0.25);
    min-height: 130px;
}}
@import url('https://fonts.googleapis.com/css2?family=Great+Vibes&display=swap');

.logo-wrap {{
    display: inline-block;
    position: relative;
    overflow: hidden;
    padding: 5px 15px 15px 15px;
}}

.handwritten-logo {{
    font-family: 'Great Vibes', cursive;
    font-size: 86px;
    font-weight: 400;
    line-height: 1.1;
    color: #4b2e20;
    white-space: nowrap;

    clip-path: inset(0 100% 0 0);

    animation:
        handwriting 3.2s cubic-bezier(0.45, 0, 0.2, 1) forwards;
}}

.pen-line {{
    position: absolute;
    left: 10px;
    bottom: 8px;

    height: 2px;
    width: 0;

    background: #4b2e20;
    border-radius: 100%;

    transform: rotate(-2deg);

    animation:
        flourish 1.2s ease-out 2.6s forwards;
}}

@keyframes handwriting {{
    0% {{
        clip-path: inset(0 100% 0 0);
        transform: translateX(-5px);
        opacity: 0;
    }}

    10% {{
        opacity: 1;
    }}

    100% {{
        clip-path: inset(0 0 0 0);
        transform: translateX(0);
        opacity: 1;
    }}
}}

@keyframes flourish {{
    from {{
        width: 0;
        opacity: 0;
    }}

    to {{
        width: 95%;
        opacity: 1;
    }}
}}

#cafe-header h1 {{
    color: #4b2e20;
    font-size: 42px;
    margin-bottom: 5px;
}}

#cafe-header p {{
    color: #6f5140;
    font-size: 17px;
}}

#chat-area {{
    max-width: 1400px;
    width: 90%;
    margin: auto;
    padding: 20px;
    background: rgba(250, 244, 235, 0.92);
    border-radius: 24px;
    box-shadow: 0 10px 35px rgba(0, 0, 0, 0.25);

    min-height: 600px;
}}

button {{
    border-radius: 14px !important;
}}

textarea {{
    border-radius: 14px !important;
}}
"""

if __name__ == "__main__":
    with gr.Blocks(css=CUSTOM_CSS) as demo:

        gr.HTML("""
        <div id="cafe-header">

            <div class="logo-wrap">
                <div class="handwritten-logo">Rast Cafe</div>
                <div class="pen-line"></div>
            </div>

            <p>Your cozy coffee companion</p>
            <p>Ask about our menu, prices, opening hours and café services.</p>

        </div>
        """)

        with gr.Column(elem_id="chat-area"):
            gr.ChatInterface(
                fn=chat_with_agent,
                examples=[
                    "Do you have cappuccino?",
                    "How much are two cappuccinos and one cheesecake?",
                    "What are your opening hours?",
                    "Do you have parking?",
                    "I want to speak to a staff member."
                ]
            )

    demo.launch()