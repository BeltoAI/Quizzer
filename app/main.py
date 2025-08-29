from __future__ import annotations
import os, re, json, pathlib, traceback, random
from typing import List, Optional, Tuple, Any, Dict
from collections import Counter

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request
from pydantic import BaseModel

from app.models import Quiz, Midterm
from app.llm import chat_json
from app.canvas import CanvasClient

ART_DIR = os.environ.get("ART_DIR") or ("/tmp/artifacts" if os.environ.get("VERCEL") else "artifacts")
ART = pathlib.Path(ART_DIR); ART.mkdir(exist_ok=True, parents=True)
COLLECT_LOG = ART / "collect_last.txt"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
def root():
    return FileResponse("static/index.html")

@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}

@app.exception_handler(Exception)
async def all_exceptions_handler(request: Request, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    (ART / "server_errors.log").write_text(tb, encoding="utf-8")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)}"})

# -------------------- State / helpers --------------------
STATE: Dict[str, Any] = {"client": None}

def _update_state_if_provided(base: Optional[str], token: Optional[str]):
    b = (base or "").strip() or "https://canvas.instructure.com/"
    t = (token or "").strip()
    if t:
        STATE["client"] = CanvasClient(b, t)

def _client_or_401() -> CanvasClient:
    client = STATE.get("client")
    if not client:
        raise HTTPException(status_code=401, detail="Authenticate first.")
    return client

def _resolve_course_id(client: CanvasClient, cid: Optional[int]) -> int:
    if cid: return int(cid)
    courses = await client.list_courses()
    if not courses:
        raise HTTPException(status_code=404, detail="No courses found for this token.")
    return int(courses[0]["id"])

# -------------------- Bodies --------------------
class AuthBody(BaseModel):
    canvas_base_url: Optional[str] = None
    canvas_token: Optional[str] = None

class ModulesBody(BaseModel):
    canvas_base_url: Optional[str] = None
    canvas_token: Optional[str] = None
    course_id: Optional[int] = None

class GenerateQuizBody(BaseModel):
    canvas_base_url: Optional[str] = None
    canvas_token: Optional[str] = None
    course_id: Optional[int] = None
    module_ids: Optional[List[int]] = None
    page_urls: Optional[List[str]] = None
    file_ids: Optional[List[int]] = None
    assignment_ids: Optional[List[int]] = None
    quiz_count: Optional[int] = 20
_AUTH: dict = {}  # in-memory creds (stateless on serverless)

class GenerateMidtermBody(BaseModel):
    canvas_base_url: Optional[str] = None
    canvas_token: Optional[str] = None
    course_id: Optional[int] = None
    module_ids: Optional[List[int]] = None
    page_urls: Optional[List[str]] = None
    file_ids: Optional[List[int]] = None
    assignment_ids: Optional[List[int]] = None

class PublishQuizBody(BaseModel):
    canvas_base_url: Optional[str] = None
    canvas_token: Optional[str] = None
    course_id: Optional[int] = None
    quiz: Quiz
    settings: Optional[Dict[str, Any]] = None

class PublishMidtermBody(BaseModel):
    canvas_base_url: Optional[str] = None
    canvas_token: Optional[str] = None
    course_id: Optional[int] = None
    midterm: Midterm
    settings: Optional[Dict[str, Any]] = None

# -------------------- Text cleaning --------------------
_BULLETS = r"[•·▪︎►▶▪●◦∙•♦■□–—\-•\*]+"
def _cleanup_text(s: str) -> str:
    if not s: return ""
    t = s

    # Remove artificial file headers injected into corpus, keep content
    t = re.sub(r"^#+\s*File:\s*\d+\s*$", "", t, flags=re.M)

    # Normalize bullets and weird whitespace
    t = re.sub(_BULLETS, " ", t)
    t = t.replace("\u00a0", " ")  # nbsp

    # Join hard-wrapped lines (PDF artifacts): newline not ending a sentence -> space
    # Preserve paragraph breaks (double newline).
    def join_wraps(block: str) -> str:
        lines = block.split("\n")
        out = []
        for i, ln in enumerate(lines):
            ln = ln.strip()
            if not ln:
                out.append("")
                continue
            if i+1 < len(lines):
                nxt = lines[i+1].strip()
            else:
                nxt = ""
            # If line ends without end punctuation and next line starts lowercase/alnum, join
            if (not re.search(r"[.!?]([\"')\]]+)?\s*$", ln)) and nxt and re.match(r"[a-z0-9]", nxt):
                out.append(ln + " ")
            else:
                out.append(ln + "\n")
        joined = "".join(out)
        return re.sub(r"[ \t]+", " ", joined)
    # Work per paragraph block to keep structure
    blocks = re.split(r"\n{2,}", t)
    t = "\n\n".join(join_wraps(b).strip() for b in blocks if b.strip())

    # Collapse excessive whitespace
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

# -------------------- Question normalization --------------------
def _normalize_question(q: Dict[str, Any]) -> Dict[str, Any]:
    t = str(q.get("type") or "").strip().lower()
    prompt = re.sub(r"\s+", " ", str(q.get("prompt") or "").strip())
    points = int(q.get("points") or 1)
    if not prompt:
        return {"type":"short","prompt":"Explain one key concept from the materials.","points":1}

    if t == "mcq":
        choices = q.get("choices") or []
        try:
            ans = int(q.get("answer"))
        except Exception:
            ans = -1
        if isinstance(q.get("answer"), str) and len(str(q["answer"]))==1 and str(q["answer"]).isalpha():
            ans = ord(str(q["answer"]).upper()) - ord("A")
        if not isinstance(choices, list): choices = []
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) >= 2 and 0 <= ans < len(choices):
            return {"type":"mcq","prompt":prompt,"choices":choices,"answer":ans,"points":points}
        return {"type":"short","prompt":prompt,"points":points}

    if t == "truefalse":
        ans = q.get("answer")
        if isinstance(ans, str):
            ans = ans.strip().lower() in {"true","t","1","yes","y"}
        if isinstance(ans, bool):
            return {"type":"truefalse","prompt":prompt,"answer":bool(ans),"points":points}
        return {"type":"short","prompt":prompt,"points":points}

    if t == "fillblank":
        ans = str(q.get("answer") or "").strip()
        if ans:
            return {"type":"fillblank","prompt":prompt,"answer":ans,"points":points}
        return {"type":"short","prompt":prompt,"points":points}

    return {"type":"short","prompt":prompt,"points":points}

def _pack_questions(data: Dict[str, Any], default_title: str) -> Tuple[str, List[Dict[str, Any]]]:
    title = str(data.get("title") or default_title).strip()
    pool: List[Dict[str, Any]] = []
    if isinstance(data.get("questions"), list):
        pool = data["questions"]
    elif isinstance(data.get("sections"), list):
        for sec in data["sections"]:
            pool.extend(sec.get("questions") or [])
    elif isinstance(data, list):
        pool = data
    elif isinstance(data, dict) and all(k in data for k in ("type","prompt")):
        pool = [data]
    out: List[Dict[str, Any]] = []
    seen = set()
    for q in pool:
        qq = _normalize_question(q)
        key = (qq.get("type"), (qq.get("prompt") or "").strip())
        if key[1] and key not in seen:
            seen.add(key); out.append(qq)
    return title or default_title, out

# -------------------- Offline generator (no LLM creds needed) --------------------
_STOPWORDS = set("""
a an the and or but if then else for to in on at of by with from into over under through as is are was were be been being this that these those it its it's
you your yours we us our they them their i me my mine he she his her hers which who whom whose what when where why how not no yes true false very more most
""".split())

def _keywords(text: str, k: int = 40) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-']{2,}", text)
    words = [w.lower() for w in words]
    words = [w for w in words if w not in _STOPWORDS and not w.isdigit() and len(w) > 3]
    cnt = Counter(words)
    # prefer mixed case occurrences (proper nouns) by boosting tokens that appear capitalized in original
    caps = set(re.findall(r"\b([A-Z][a-zA-Z]{2,})\b", text))
    for c in caps:
        lc = c.lower()
        if lc in cnt:
            cnt[lc] += 2
    return [w for w, _ in cnt.most_common(k)]

def _pick_distractors(answer: str, vocab: List[str], n: int = 3) -> List[str]:
    pool = [w for w in vocab if w != answer.lower()]
    random.shuffle(pool)
    out = []
    for w in pool:
        if w.lower() != answer.lower() and w not in out:
            out.append(w)
        if len(out) >= n: break
    if len(out) < n:
        # fabricate short distractors from answer morphology
        out.extend([answer[:max(3, len(answer)//2)]+"ing", answer[:max(3, len(answer)//2)]+"ness", answer[:max(3, len(answer)//2)]+"ity"])
        out = out[:n]
    return out

def _sentences(text: str) -> List[str]:
    # split on sentence boundaries but keep long lines together
    parts = re.split(r"(?<=[.!?])\s+", text)
    parts = [re.sub(r"\s+", " ", p).strip() for p in parts]
    return [p for p in parts if len(p.split()) >= 5]

def _offline_generate(corpus: str, n: int) -> List[Dict[str, Any]]:
    text = _cleanup_text(corpus)
    sents = _sentences(text)[:200]
    vocab = _keywords(text, k=80)
    rng = random.Random(42)

    want_mcq = max(4, int(n*0.45))
    want_tf  = max(3, int(n*0.25))
    want_fb  = max(3, int(n*0.20))
    want_sh  = max(2, n - (want_mcq + want_tf + want_fb))

    out: List[Dict[str, Any]] = []

    # MCQs: choose a sentence with a salient keyword and blank it
    candidates = [s for s in sents if any(k in s.lower() for k in vocab[:50])]
    rng.shuffle(candidates)
    for s in candidates:
        toks = re.findall(r"[A-Za-z][A-Za-z\-']+", s)
        toks_l = [t.lower() for t in toks]
        target = None
        for k in vocab:
            if k in toks_l and len(k) > 3:
                target = toks[toks_l.index(k)]
                break
        if not target: continue
        stem = re.sub(r"\b" + re.escape(target) + r"\b", "____", s, flags=re.I)
        correct = target
        distractors = _pick_distractors(correct, vocab, 3)
        choices = [correct] + distractors
        rng.shuffle(choices)
        ans = choices.index(correct)
        out.append({"type":"mcq","prompt":stem,"choices":choices,"answer":ans,"points":1})
        if len(out) >= want_mcq: break

    # True/False: use declaratives; flip some with a subtle negation
    tf_candidates = [s for s in sents if len(s.split()) <= 30]
    rng.shuffle(tf_candidates)
    for i, s in enumerate(tf_candidates):
        make_false = (i % 3 == 0)  # ~33% false
        p = s
        ans = True
        if make_false:
            # crude negation: swap "is/are/can/will" with "is not/are not/cannot/won't" when possible
            repls = [
                (r"\bis\b", "is not"), (r"\bare\b", "are not"),
                (r"\bcan\b", "cannot"), (r"\bwill\b", "will not"),
                (r"\bdoes\b", "does not"), (r"\bdo\b", "do not"),
            ]
            for pat, rep in repls:
                if re.search(pat, p, flags=re.I):
                    p = re.sub(pat, rep, p, flags=re.I); ans = False; break
        out.append({"type":"truefalse","prompt":p,"answer":ans,"points":1})
        if sum(1 for q in out if q["type"]=="truefalse") >= want_tf: break

    # Fill-in-the-blank: blank a mid-sentence noun-ish token
    fb_sents = [s for s in sents if 8 <= len(s.split()) <= 30]
    rng.shuffle(fb_sents)
    for s in fb_sents:
        toks = re.findall(r"[A-Za-z][A-Za-z\-']+", s)
        if len(toks) < 5: continue
        # pick a mid token that's not a stopword
        idxs = [i for i,t in enumerate(toks[1:-1], start=1) if t.lower() not in _STOPWORDS and len(t) > 3]
        if not idxs: continue
        idx = rng.choice(idxs)
        ans = toks[idx]
        stem = " ".join(toks[:idx] + ["____"] + toks[idx+1:])
        out.append({"type":"fillblank","prompt":stem,"answer":ans,"points":1})
        if sum(1 for q in out if q["type"]=="fillblank") >= want_fb: break

    # Short answers: ask to explain/compare top keywords
    for kw in vocab[:want_sh*2]:
        out.append({"type":"short","prompt":f"In 1–2 sentences, explain '{kw}' in the context of the materials.","points":1})
        if sum(1 for q in out if q["type"]=="short") >= want_sh: break

    # Deduplicate by (type,prompt)
    seen=set(); ded=[]
    for q in out:
        key=(q["type"], q["prompt"])
        if key not in seen:
            seen.add(key); ded.append(q)
    # Top up if needed
    while len(ded) < n:
        ded.append({"type":"short","prompt":"Name one concrete fact from the materials and why it matters.","points":1})
    return ded[:n]

# -------------------- Collection --------------------
def _collect_content(client: CanvasClient, course_id: int,
                     module_ids: Optional[List[int]],
                     page_urls: Optional[List[str]],
                     file_ids: Optional[List[int]],
                     assignment_ids: Optional[List[int]]) -> Tuple[str, List[str], List[str]]:
    warns: List[str] = []
    titles: List[str] = []
    pset = set(page_urls or [])
    fset = set(file_ids or [])
    aset = set(assignment_ids or [])

    if module_ids:
        try:
            all_mods = client.list_modules(course_id)
            wanted = set(module_ids)
            for m in all_mods:
                if m["id"] in wanted:
                    for it in m.get("items", []):
                        if it["type"] == "Page" and it.get("page_url"): pset.add(it["page_url"])
                        elif it["type"] == "File" and it.get("file_id"): fset.add(int(it["file_id"]))
                        elif it["type"] == "Assignment" and it.get("assignment_id"): aset.add(int(it["assignment_id"]))
        except Exception as e:
            warns.append(f"Module expansion error: {e}")

    parts: List[str] = []
    for u in sorted(pset):
        try:
            txt = client.get_page_body(course_id, u)
            titles.append(f"Page: {u}")
            if txt: parts.append(f"### Page: {u}\n{txt}")
        except Exception as e:
            warns.append(f"Page {u}: {e}")
    for fid in sorted(fset):
        try:
            txt, w = client.get_file_text(int(fid))
            if w: warns.append(w)
            titles.append(f"File: {fid}")
            if txt: parts.append(f"### File: {fid}\n{txt}")
        except Exception as e:
            warns.append(f"File {fid}: {e}")
    for aid in sorted(aset):
        try:
            txt = client.get_assignment_text(course_id, int(aid))
            titles.append(f"Assignment: {aid}")
            if txt: parts.append(f"### Assignment: {aid}\n{txt}")
        except Exception as e:
            warns.append(f"Assignment {aid}: {e}")

    raw = "\n\n".join(parts).strip()
    corpus = _cleanup_text(raw)

    # Log what we ingested
    with COLLECT_LOG.open("w", encoding="utf-8") as f:
        f.write("=== COLLECTION LOG ===\n")
        f.write(f"Modules: {sorted(module_ids or [])}\n")
        f.write(f"Pages:   {sorted(pset)}\n")
        f.write(f"Files:   {sorted(fset)}\n")
        f.write(f"Assigns: {sorted(aset)}\n")
        f.write(f"SOURCES ({len(titles)}):\n")
        for t in titles: f.write(f"- {t}\n")
        f.write(f"\nTOTAL CORPUS CHARS (raw/clean): {len(raw)} / {len(corpus)}\n")
        if warns:
            f.write("\nWARNINGS:\n")
            for w in warns: f.write(f"- {w}\n")
    return corpus, warns, titles

# -------------------- Generation --------------------
def _generate_from_corpus(corpus: str, want: int, default_title: str, system_prompt: str) -> Tuple[str, List[Dict[str, Any]]]:
    # Try LLM first
    try:
        data = chat_json(system_prompt, f"Create exactly {want} questions grounded ONLY in this text:\n\"\"\"{corpus[:20000]}\"\"\"", max_tokens=2400, temperature=0.15)
        title, packed = _pack_questions(data, default_title=default_title)
        # If LLM under-delivered, top up with offline
        if len(packed) < want:
            packed.extend(_offline_generate(corpus, want - len(packed)))
    except Exception:
        title, packed = default_title, _offline_generate(corpus, want)

    # Deduplicate and clamp
    seen=set(); ded=[]
    for q in packed:
        key=(q.get("type"), (q.get("prompt") or "").strip())
        if key[1] and key not in seen:
            seen.add(key); ded.append(q)
    return (title or default_title), ded[:want]

# -------------------- Endpoints --------------------
@app.post("/auth")
async def auth(payload: dict):
    """
    Body: {"canvas_base_url"?, "canvas_token"}
    - defaults base to https://canvas.instructure.com/
    - validates token
    - returns minimal course list for UI selection
    """
    from fastapi import HTTPException
    from app.canvas import CanvasClient, CanvasError

    base = (payload or {}).get("canvas_base_url") or "https://canvas.instructure.com/"
    token = ((payload or {}).get("canvas_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="canvas_token is required")

    client = CanvasClient(base, token)
    try:
        await client.validate_token()
        # also return courses for the UI
        courses = await client.list_courses()
    except CanvasError as e:
        raise HTTPException(status_code=401, detail=str(e))
    finally:
        try:
            await client.close()
        except Exception:
            pass

    # store creds in-process (serverless note: ephemeral; UI must send on each call)
    global _AUTH
    _AUTH = {"canvas_base_url": base, "canvas_token": token}

    # return plain JSON (no pydantic model to avoid serialization surprises)
    return {"ok": True, "canvas_base_url": base, "courses": courses}
@app.post("/modules")
def modules(body: ModulesBody):
    _update_state_if_provided(body.canvas_base_url, body.canvas_token)
    client = _client_or_401()
    cid = _resolve_course_id(client, body.course_id)
    try:
        mods = client.list_modules(cid)
        return {"modules": mods}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Canvas error: {e}")

@app.post("/generate/quiz")
def generate_quiz(body: GenerateQuizBody):
    _update_state_if_provided(body.canvas_base_url, body.canvas_token)
    client = _client_or_401()
    cid = _resolve_course_id(client, body.course_id)
    corpus, warns, titles = _collect_content(client, cid, body.module_ids, body.page_urls, body.file_ids, body.assignment_ids)

    if not corpus and (body.module_ids or body.page_urls or body.file_ids or body.assignment_ids):
        corpus = "\n".join(titles)
        if corpus:
            warns.append("No Page/File text extracted; fell back to module/item titles.")

    if not corpus:
        raise HTTPException(status_code=422, detail="No course materials extracted. Select Page/File/Assignment items or tick a module header, then try again.")

    want = max(1, int(body.quiz_count or 20))
    title, packed = _generate_from_corpus(
        corpus, want, "Generated Quiz",
        "You are an exam writer. Only use the provided text. Output strict JSON {title, questions:[...]}. Use mcq,truefalse,short,fillblank. Each item has 'points'."
    )
    quiz = Quiz(title=(title or "Generated Quiz"), questions=packed)  # type: ignore[arg-type]
    return {"warnings": warns, "quiz": quiz, "course_id": cid}

@app.post("/generate/midterm")
def generate_midterm(body: GenerateMidtermBody):
    _update_state_if_provided(body.canvas_base_url, body.canvas_token)
    client = _client_or_401()
    cid = _resolve_course_id(client, body.course_id)
    corpus, warns, titles = _collect_content(client, cid, body.module_ids, body.page_urls, body.file_ids, body.assignment_ids)

    if not corpus and (body.module_ids or body.page_urls or body.file_ids or body.assignment_ids):
        corpus = "\n".join(titles)
        if corpus:
            warns.append("No Page/File text extracted; fell back to module/item titles.")

    if not corpus:
        raise HTTPException(status_code=422, detail="No course materials extracted. Select Page/File/Assignment items or tick a module header, then try again.")

    want = 30
    title, packed = _generate_from_corpus(
        corpus, want, "Generated Midterm",
        "You design midterms strictly from provided text. Output strict JSON {title, questions:[...]}. Include mixed types. Each has 'points'."
    )
    mid = Midterm(title=(title or "Generated Midterm"), questions=packed)  # type: ignore[arg-type]
    return {"warnings": warns, "midterm": mid, "course_id": cid}

@app.post("/publish/quiz")
def publish_quiz(body: PublishQuizBody):
    _update_state_if_provided(body.canvas_base_url, body.canvas_token)
    client = _client_or_401()
    cid = _resolve_course_id(client, body.course_id)
    quiz = body.quiz.model_dump()
    settings = body.settings or {}
    try:
        qres = client.create_quiz(cid, quiz.get("title") or "Generated Quiz", settings)
        qid = qres.get("id")
        if not qid:
            raise HTTPException(status_code=400, detail=f"Canvas did not return quiz id: {qres}")
        for q in quiz.get("questions", []):
            client.create_quiz_question(cid, qid, q)
        return {"quiz_id": qid, "html_url": qres.get("html_url")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/publish/midterm")
def publish_midterm(body: PublishMidtermBody):
    _update_state_if_provided(body.canvas_base_url, body.canvas_token)
    client = _client_or_401()
    cid = _resolve_course_id(client, body.course_id)
    mid = body.midterm.model_dump()
    settings = body.settings or {}
    try:
        qres = client.create_quiz(cid, mid.get("title") or "Generated Midterm", settings)
        qid = qres.get("id")
        if not qid:
            raise HTTPException(status_code=400, detail=f"Canvas did not return quiz id: {qres}")
        for q in mid.get("questions", []):
            client.create_quiz_question(cid, qid, q)
        return {"quiz_id": qid, "html_url": qres.get("html_url")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))