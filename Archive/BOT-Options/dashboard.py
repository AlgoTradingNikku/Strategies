import uvicorn

# Equivalent to `python app.py` (the primary, recommended way to run this bot)
# or `uvicorn server:app --port 9000` — kept as a third option for anyone
# with a habit of running `python dashboard.py`.
if __name__ == "__main__":
    print("==============================================================")
    print("  UT BOT ANTIGRAVITY — Web Dashboard Server")
    print("  URL: http://127.0.0.1:9000")
    print("==============================================================")
    uvicorn.run("server:app", host="127.0.0.1", port=9000, reload=False, access_log=False)
