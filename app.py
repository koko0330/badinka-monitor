from flask import request

@app.route("/update", methods=["POST"])
def update():
    new_data = request.json
    if os.path.exists(MENTIONS_FILE):
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            mentions = json.load(f)
    else:
        mentions = []

    # Avoid duplicates
    existing_ids = {entry["id"] for entry in mentions}
    for item in new_data:
        if item["id"] not in existing_ids:
            mentions.append(item)

    with open(MENTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(mentions, f, ensure_ascii=False, indent=2)

    return {"status": "ok", "added": len(new_data)}, 200
