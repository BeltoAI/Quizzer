from __future__ import annotations
import os, json, re, httpx, pathlib

CHAT_BASE = os.getenv("CHAT_BASE")
CHAT_PATH = os.getenv("CHAT_PATH", "/v1/chat/completions")
MODEL_NAME = os.getenv("MODEL_NAME", "local")
API_KEY = os.getenv("API_KEY")

ART = pathlib.Path("artifacts"); ART.mkdir(exist_ok=True, parents=True)
LAST = ART / "llm_last.txt"

def _strip(txt: str) -> str:
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?", "", txt, flags=re.I).strip()
    txt = re.sub(r"```$", "", txt).strip()
    return txt

def _coerce_json2(txt: str) -> dict:
    s = _strip(txt)
    s = s.replace("True","true").replace("False","false").replace("None","null")
    s = re.sub(r",\s*([}\]])", r"\1", s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError("Could not parse JSON from model output")

def chat_json(system: str, user: str, max_tokens=2000, temperature=0.15) -> dict:
    if not CHAT_BASE or not API_KEY:
        fallback = {
            "title": "Tiny Fallback Quiz",
            "questions": [
                {"type":"truefalse","prompt":"This quiz is generated without an LLM.","answer":True,"points":1},
                {"type":"short","prompt":"Name one concept from the provided materials.","points":1},
            ],
        }
        LAST.write_text("FALLBACK MODE (missing CHAT_BASE/API_KEY)\n", encoding="utf-8")
        return fallback

    url = CHAT_BASE.rstrip("/") + (CHAT_PATH or "/v1/chat/completions")
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL_NAME, "messages":[{"role":"system","content":system},{"role":"user","content":user}],
               "max_tokens": max_tokens, "temperature": temperature}
    with httpx.Client(timeout=60) as s:
        r = s.post(url, json=payload, headers=headers)
    r.raise_for_status()
    data = r.json()
    txt = ""
    if data.get("choices"):
        ch = data["choices"][0]
        txt = ch.get("message",{}).get("content") or ch.get("text","")
    if not txt:
        # /v1/completions fallback
        comp = CHAT_BASE.rstrip("/") + "/v1/completions"
        with httpx.Client(timeout=60) as s:
            r2 = s.post(comp, json={"model":MODEL_NAME,"prompt":f"{system}\n\nUSER:\n{user}\nReturn JSON only.","max_tokens":max_tokens,"temperature":temperature}, headers=headers)
        r2.raise_for_status()
        data = r2.json()
        txt = data.get("choices",[{}])[0].get("text","")
    LAST.write_text(txt or "", encoding="utf-8")
    return _coerce_json2(txt or "{}")
