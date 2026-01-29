import json
import datetime
import random

HISTORY_FILE = "price_history.json"

def inject_mock_data():
    history = {}
    today = datetime.date.today()
    
    # SEA-SFO (sf_weekend)
    sf_history = []
    for i in range(14, -1, -1):
        d = today - datetime.timedelta(days=i)
        price = 180 + random.randint(-20, 20)
        sf_history.append({
            "date": d.isoformat(),
            "price": price,
            "carrier": random.choice(["Alaska", "Delta"])
        })
    
    # SEA-PSP (desert_escape)
    psp_history = []
    for i in range(14, -1, -1):
        d = today - datetime.timedelta(days=i)
        price = 450 + random.randint(-50, 50)
        psp_history.append({
            "date": d.isoformat(),
            "price": price,
            "carrier": random.choice(["Alaska", "Delta"])
        })
    
    history = {
        "sf_weekend": {
            "latest": sf_history[-1],
            "history": sf_history
        },
        "desert_escape": {
            "latest": psp_history[-1],
            "history": psp_history
        }
    }
    
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)
    print("Mock history injected successfully.")

if __name__ == "__main__":
    inject_mock_data()
