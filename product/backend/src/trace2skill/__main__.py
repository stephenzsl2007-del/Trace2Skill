import uvicorn


if __name__ == "__main__":
    uvicorn.run("trace2skill.api:app", host="127.0.0.1", port=8086, reload=False)
