import uvicorn

if __name__ == "__main__":
    print("==============================================================")
    print("  UT BOT ANTIGRAVITY — Web Dashboard Server")
    print("  URL: http://127.0.0.1:8002")
    print("==============================================================")
    uvicorn.run("server:app", host="127.0.0.1", port=8002, reload=False, access_log=False)
