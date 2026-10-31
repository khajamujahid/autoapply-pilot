"""Tiny OpenAI-compatible server used to test Bring-Your-Own-AI end to end without a real key.
Run: python tests/mock_ai_server.py  (listens on :8099, accepts any key)"""
import json
import re
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()
CALLS = []


@app.post("/v1/chat/completions")
async def chat(req: Request):
    b = await req.json()
    sys, user = b["messages"][0]["content"], b["messages"][-1]["content"]
    CALLS.append(sys[:60])
    if "connectivity" in sys:
        out = "OK"
    elif "Extract a structured candidate profile" in sys:
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", user)
        first = user.strip().split()[0]; last = user.strip().split()[1]
        out = json.dumps({"first_name": first, "last_name": last, "email": email.group(0) if email else "",
                          "phone": "+1 555 010 0000", "location": "New York, NY", "linkedin": "https://www.linkedin.com/in/placeholder-test",
                          "github": "https://github.com/placeholder-test", "website": "", "current_title": "Software Engineer",
                          "current_company": "Example Corp", "years_experience": 5,
                          "skills": "Python, Go, TypeScript, React, PostgreSQL, AWS, Docker, Kubernetes",
                          "summary": "[mock-ai] Backend engineer.", "experience": [{"company": "Example Corp", "title": "Software Engineer", "start": "2021", "end": "present", "highlights": []}],
                          "education": [{"school": "Example University", "degree": "B.S. CS", "year": "2019"}]})
    elif "OPTIONS:" in user:
        opts = json.loads(user.split("OPTIONS:", 1)[1].strip())
        pick = next((o for o in opts if re.search(r"decline|prefer not|don.t wish", o, re.I)), opts[0])
        out = json.dumps({"answer": [pick] if "list of exact" in sys else pick})
    elif "tailor resumes" in sys:
        out = "[mock-ai tailored]\n" + user.split("CANDIDATE RESUME:", 1)[-1].strip()
    elif "cover letter" in sys:
        out = "[mock-ai] Dear team,\n\nI'd love to join.\n\nAlex"
    elif "bullet points" in sys:
        out = "- [mock-ai] good fit\n- strong Python\n- gap: domain"
    else:
        q = user.split("QUESTION:", 1)[-1].strip()
        out = f"[mock-ai answer] Placeholder response to: {q[:60]}"
    return {"choices": [{"message": {"role": "assistant", "content": out}}]}


@app.get("/calls")
def calls():
    return CALLS


if __name__ == "__main__":
    uvicorn.run(app, port=8099)
