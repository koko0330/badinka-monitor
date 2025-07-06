from flask import Flask, render_template, jsonify, send_file, request
import json
import pandas as pd
from datetime import datetime
import os

app = Flask(__name__)

MENTIONS_FILE = "mentions.json"
CSV_FILE = "mentions.csv"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def data():
    if os.path.exists(MENTIONS_FILE):
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route("/download")
def download():
    if os.path.exists(MENTIONS_FILE):
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        df.to_csv(CSV_FILE, index=False)
        return send_file(CSV_FILE, as_attachment=True)
    return "No data available", 404

@app.route("/update", methods=["POST"])
def update():
    new_data = request.json
    if os.path.exists(MENTIONS_FILE):
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            mentions = json.load(f)
    else:
        mentions = []

    existing_ids = {entry["id"] for entry in mentions}
    for item in new_data:
        if item["id"] not in existing_ids:
            mentions.append(item)

    with open(MENTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(mentions, f, ensure_ascii=False, indent=2)

    return {"status": "ok", "added": len(new_data)}, 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
