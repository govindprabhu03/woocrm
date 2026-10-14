"""CRM agent. Strategy: Gemini function-calling loop when a key is set, rule-based fallback otherwise (or on any API failure)."""
import logging
import re
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.agent.tools import TOOL_SPECS, CrmTools, retention_run

log = logging.getLogger("woocrm.agent")
API = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_URL = API + "/models/{model}:generateContent"
_MODEL_CACHE: dict[str, str] = {}  # configured model -> model actually usable with this key
SYSTEM = ("You are the CRM assistant for a WooCommerce store. Use the tools to answer from real data. "
          "Never claim to have sent email: drafts require human approval. Be concise.")


class Agent(Protocol):
    name: str

    def chat(self, message: str) -> dict: ...


def _schema(prop: dict) -> dict:
    out = {"type": prop["type"].upper()}
    for k in ("enum", "description"):
        if k in prop:
            out[k] = prop[k]
    return out


def gemini_declarations() -> list[dict]:
    """Translate the shared tool specs to Gemini functionDeclarations (no `parameters` when a tool takes none)."""
    decls = []
    for t in TOOL_SPECS:
        d = {"name": t["name"], "description": t["description"]}
        props = t["input_schema"].get("properties") or {}
        if props:
            d["parameters"] = {"type": "OBJECT", "properties": {k: _schema(v) for k, v in props.items()}}
            if t["input_schema"].get("required"):
                d["parameters"]["required"] = t["input_schema"]["required"]
        decls.append(d)
    return decls


def rank_models(models: list[dict]) -> list[str]:
    """Usable chat models from a ListModels response, best first: stable full-size Flash, newest version."""
    def score(m: dict):
        name = m["name"].removeprefix("models/")
        ver = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return (("flash" in name and "lite" not in name), not re.search(r"preview|exp", name),
                float(ver.group(1)) if ver else 0.0, name)

    banned = ("image", "tts", "live", "embed", "audio", "vision", "robotics", "computer", "aqa")
    ok = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])
          and m["name"].startswith("models/gemini") and not any(b in m["name"] for b in banned)]
    return [m["name"].removeprefix("models/") for m in sorted(ok, key=score, reverse=True)]


def pick_model(models: list[dict]) -> str | None:
    ranked = rank_models(models)
    return ranked[0] if ranked else None


class GeminiAgent:
    name = "gemini"

    def __init__(self, db: Session, api_key: str, model: str, http: httpx.Client | None = None, max_steps: int = 6):
        self.db, self.tools = db, CrmTools(db)
        self._key, self.configured_model, self.max_steps = api_key, model, max_steps
        self._http = http or httpx.Client(timeout=30)

    @property
    def model(self) -> str:
        return _MODEL_CACHE.get(self.configured_model, self.configured_model)

    def _alternatives(self, tried: set[str], limit: int = 3) -> list[str]:
        """Other models this key can use (ListModels), best first, excluding ones already tried."""
        r = self._http.get(f"{API}/models", params={"pageSize": 200}, headers={"x-goog-api-key": self._key})
        r.raise_for_status()
        return [m for m in rank_models(r.json().get("models", [])) if m not in tried][:limit]

    def _post(self, model: str, body: dict):
        return self._http.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": self._key}, json=body)

    def _generate(self, contents: list):
        body = {"system_instruction": {"parts": [{"text": SYSTEM}]}, "contents": contents,
                "tools": [{"functionDeclarations": gemini_declarations()}]}
        r = self._post(self.model, body)
        if r.status_code in (404, 429):  # model missing for this key, or its free quota is exhausted: try the next best
            for alt in self._alternatives({self.model}):
                r2 = self._post(alt, body)
                if r2.status_code == 200:
                    log.warning("gemini model %s unavailable (HTTP %s); using %s", self.model, r.status_code, alt)
                    _MODEL_CACHE[self.configured_model] = alt
                    r = r2
                    break
        r.raise_for_status()
        return r

    def chat(self, message: str) -> dict:
        try:
            return self._loop(message)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            reason, detail = type(e).__name__, ""
            if isinstance(e, httpx.HTTPStatusError):
                reason = f"HTTP {e.response.status_code}"
                try:
                    detail = " - " + e.response.json()["error"]["message"][:200]
                except (ValueError, KeyError, TypeError):
                    pass
            log.warning("gemini unavailable (%s%s) - using rule-based fallback", reason, detail)  # key is in a header, never logged
            out = RuleBasedAgent(self.db).chat(message)
            out["engine"] = f"rules (gemini unavailable: {reason})"
            return out

    def _loop(self, message: str) -> dict:
        contents: list = [{"role": "user", "parts": [{"text": message}]}]
        trace: list = []
        for _ in range(self.max_steps):
            r = self._generate(contents)
            candidates = r.json().get("candidates") or []
            if not candidates:
                raise ValueError("empty response")
            content = candidates[0].get("content") or {"role": "model", "parts": []}
            parts = content.get("parts", [])
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                return {"reply": text or "I couldn't produce an answer.", "tool_calls": trace, "engine": f"{self.name} ({self.model})"}
            contents.append(content)  # verbatim: Gemini needs its own parts (incl. thought signatures) echoed back
            results = []
            for c in calls:
                args = c.get("args") or {}
                trace.append({"tool": c["name"], "args": args})
                results.append({"functionResponse": {"name": c["name"], "response": {"result": self.tools.call(c["name"], args)}}})
            contents.append({"role": "user", "parts": results})
        return {"reply": "Stopped after max steps.", "tool_calls": trace, "engine": self.name}


class RuleBasedAgent:
    """Offline fallback: keyword intents routed to the same tools, so the demo never depends on network/keys."""

    name = "rules"

    def __init__(self, db: Session):
        self.db, self.tools = db, CrmTools(db)

    def chat(self, message: str) -> dict:
        m, trace = message.lower(), []

        def call(name, **args):
            trace.append({"tool": name, "args": args})
            return self.tools.call(name, args)

        if re.search(r"draft|win.?back|retention|re-?engage", m):
            runs = retention_run(self.db, limit=5)
            trace.append({"tool": "retention_run", "args": {"limit": 5}})
            reply = (f"Drafted {len(runs)} win-back email(s) awaiting your approval: "
                     + ", ".join(r["customer"] for r in runs)) if runs else "No at-risk customers need a new draft."
        elif seg := next((s for s in ["vip", "at_risk", "churned", "new", "prospect"] if s.replace("_", " ") in m or s in m), None):
            rows = call("find_customers", segment=seg, limit=5)
            reply = f"Top {seg.replace('_', ' ')} customers:\n" + "\n".join(
                f"• {r['name']} — {r['orders']} orders, ₹{r['spent']:.0f}" for r in rows) if rows else f"No {seg} customers."
        else:
            s = call("segment_summary")
            reply = "Segment overview:\n" + "\n".join(f"• {k.replace('_', ' ')}: {v['customers']} customers, ₹{v['revenue']:.0f}"
                                                      for k, v in sorted(s.items()))
        return {"reply": reply, "tool_calls": trace, "engine": self.name}


def build_agent(db: Session, api_key: str, model: str) -> Agent:
    return GeminiAgent(db, api_key, model) if api_key else RuleBasedAgent(db)
