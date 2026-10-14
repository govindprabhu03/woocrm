"""CRM agent. LLMBackend strategy: Claude tool-use loop when a key is set, rule-based fallback otherwise."""
import json
import re
from typing import Protocol

from sqlalchemy.orm import Session

from app.agent.tools import TOOL_SPECS, CrmTools, retention_run

SYSTEM = ("You are the CRM assistant for a WooCommerce store. Use the tools to answer from real data. "
          "Never claim to have sent email: drafts require human approval. Be concise.")


class Agent(Protocol):
    name: str

    def chat(self, message: str) -> dict: ...


class ClaudeAgent:
    name = "claude"

    def __init__(self, db: Session, api_key: str, model: str, client=None, max_steps: int = 6):
        import anthropic

        self.tools = CrmTools(db)
        self.model, self.max_steps = model, max_steps
        self.client = client or anthropic.Anthropic(api_key=api_key)

    def chat(self, message: str) -> dict:
        messages: list = [{"role": "user", "content": message}]
        trace = []
        for _ in range(self.max_steps):
            resp = self.client.messages.create(model=self.model, max_tokens=1024, system=SYSTEM,
                                               tools=TOOL_SPECS, messages=messages)
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text")
                return {"reply": text, "tool_calls": trace, "engine": self.name}
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    out = self.tools.call(block.name, block.input)
                    trace.append({"tool": block.name, "args": block.input})
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out, default=str)})
            messages.append({"role": "user", "content": results})
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
    return ClaudeAgent(db, api_key, model) if api_key else RuleBasedAgent(db)
