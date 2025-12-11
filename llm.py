from openai import OpenAI

SCHEMA = {
  "name": "PageAudit",
  "schema": {
    "type": "object",
    "properties": {
      "page_title": {"type": "string"},
      "summary": {"type": "string"},
      "issues": {"type": "array", "items": {"type": "string"}},
      "recommendations": {"type": "array", "items": {"type": "string"}},
      "evidence": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["page_title", "summary", "recommendations"]
  },
  "strict": True
}

SYSTEM = "You are an SEO & UX auditor. Return strict JSON only."

def page_audit(client: OpenAI, url: str, title: str, text: str, max_chars=5000):
    # チャンク化（超シンプル：文字数でカット）
    chunks = []
    buf = []
    length = 0
    for para in text.split("\n\n"):
        if length + len(para) > max_chars:
            chunks.append("\n\n".join(buf)); buf=[para]; length=len(para)
        else:
            buf.append(para); length += len(para)
    if buf: chunks.append("\n\n".join(buf))

    partials = []
    for i, ch in enumerate(chunks, 1):
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type":"json_schema","json_schema":SCHEMA},
            messages=[
                {"role":"system","content": SYSTEM},
                {"role":"user","content": f"URL: {url}\nTITLE: {title}\nCHUNK {i}/{len(chunks)}:\n{ch}"}
            ],
            temperature=0.1
        )
        partials.append(resp.choices[0].message.parsed)

    # reduce（集約）
    summary = " ".join(p.get("summary","") for p in partials)[:2000]
    issues = []
    recs = []
    for p in partials:
        issues.extend(p.get("issues",[]))
        recs.extend(p.get("recommendations",[]))
    # 重複削り（ざっくり）
    def uniq(xs): 
        seen=set(); out=[]
        for x in xs:
            k=x.strip()
            if k and k.lower() not in seen:
                out.append(x); seen.add(k.lower())
        return out
    return {
        "page_title": title,
        "summary": summary,
        "issues": uniq(issues)[:20],
        "recommendations": uniq(recs)[:20],
        "evidence": [url]
    }
