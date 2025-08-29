from __future__ import annotations
from typing import Any, Dict, List, Optional
import io
import json
import httpx
from bs4 import BeautifulSoup

def _norm_base(url: Optional[str]) -> str:
    base = (url or "https://canvas.instructure.com/").strip()
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    if not base.endswith("/"):
        base += "/"
    return base

class CanvasError(Exception):
    pass

class CanvasClient:
    def __init__(self, base_url: Optional[str], token: str, timeout: float = 20.0):
        self.base = _norm_base(base_url)
        self.headers = {"Authorization": f"Bearer {token}"}
        self._client = httpx.AsyncClient(
            base_url=self.base,
            headers=self.headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # -------- Auth / sanity --------
    async def validate_token(self) -> None:
        r = await self._client.get("api/v1/courses", params={"per_page": 1})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            raise CanvasError(f"Canvas error: {r.text}")

    
    async def ping(self) -> None:
        """Alias used by /auth; validates token by hitting Canvas."""
        return await self.validate_token()

# -------- Modules + items --------
    async def list_modules_with_items(self, course_id: int) -> List[Dict[str, Any]]:
        r = await self._client.get(f"api/v1/courses/{course_id}/modules", params={"per_page": 100})
        r.raise_for_status()
        modules = r.json()
        for m in modules:
            mid = m.get("id")
            ri = await self._client.get(
                f"api/v1/courses/{course_id}/modules/{mid}/items",
                params={"per_page": 200},
            )
            ri.raise_for_status()
            m["items"] = ri.json()
        return modules

    # -------- Content collection --------
    async def get_page_text(self, course_id: int, page_url: str) -> str:
        r = await self._client.get(f"api/v1/courses/{course_id}/pages/{page_url}")
        r.raise_for_status()
        body = (r.json() or {}).get("body") or ""
        soup = BeautifulSoup(body, "html.parser")
        return soup.get_text(" ", strip=True)

    async def get_file_text(self, file_id: int) -> str:
        """Download file and extract text. Lazy-import parsers so cold start never fails."""
        # 1) Resolve file
        meta = await self._client.get(f"api/v1/files/{file_id}")
        meta.raise_for_status()
        j = meta.json()
        name = (j.get("filename") or "").lower()
        url = j.get("url") or j.get("download_url") or j.get("preview_url")
        if not url:
            return ""

        # 2) Download
        fr = await self._client.get(url)
        fr.raise_for_status()
        data = fr.content

        # 3) Detect by extension; parsers are optional
        try:
            if name.endswith(".pdf"):
                try:
                    from pypdf import PdfReader  # lazy import
                    reader = PdfReader(io.BytesIO(data))
                    return "\n".join((p.extract_text() or "") for p in reader.pages)
                except Exception:
                    return ""

            if name.endswith(".docx"):
                try:
                    import docx  # lazy import
                    doc = docx.Document(io.BytesIO(data))
                    return "\n".join(p.text for p in doc.paragraphs)
                except Exception:
                    return ""

            if name.endswith(".pptx"):
                try:
                    from pptx import Presentation  # lazy import
                    prs = Presentation(io.BytesIO(data))
                    out: List[str] = []
                    for slide in prs.slides:
                        for shape in slide.shapes:
                            if hasattr(shape, "text") and shape.text:
                                out.append(shape.text)
                    return "\n".join(out)
                except Exception:
                    return ""

            if name.endswith((".csv", ".tsv", ".txt", ".md")):
                try:
                    return data.decode("utf-8", errors="ignore")
                except Exception:
                    return ""

            # Fallback: best-effort UTF-8
            try:
                return data.decode("utf-8", errors="ignore")
            except Exception:
                return ""
        except Exception:
            # Absolutely never crash the function
            return ""

    # -------- Classic quiz publishing --------
    async def create_quiz(self, course_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        headers = self.headers | {"Content-Type": "application/x-www-form-urlencoded"}
        # Canvas expects form-encoded "quiz[...]" keys
        r = await self._client.post(
            f"api/v1/courses/{course_id}/quizzes",
            data=fields,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()

    async def create_quiz_question(self, course_id: int, quiz_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        headers = self.headers | {"Content-Type": "application/x-www-form-urlencoded"}
        r = await self._client.post(
            f"api/v1/courses/{course_id}/quizzes/{quiz_id}/questions",
            data=fields,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()
