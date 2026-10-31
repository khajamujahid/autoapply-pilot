"""Answer application questions: profile rules first, then the user's AI, with a per-run cache."""
from .ai import Provider, AIError
from .profile import rule_answer, profile_summary


class Answerer:
    def __init__(self, prof, provider: Provider, cover_letter=""):
        self.prof, self.ai, self.cl = prof, provider, cover_letter
        self.summary = profile_summary(prof)
        self.used = []   # [{question, answer, via}]

    def _rec(self, q, a, via):
        self.used.append({"question": q[:200], "answer": a if isinstance(a, str) else ", ".join(a), "via": via})
        return a

    def text(self, question: str) -> str | None:
        ql = question.lower()
        if "cover letter" in ql:
            return self._rec(question, self.cl, "cover_letter")
        r = rule_answer(self.prof, question)
        if r:
            return self._rec(question, r, "profile")
        try:
            a = self.ai.answer(question, None, self.summary)
        except AIError as e:
            self.used.append({"question": question[:200], "answer": "", "via": f"ai_error: {e}"}); return None
        if a and a.strip().upper() != "N/A":
            return self._rec(question, a.strip(), "ai")
        return None

    def choose(self, question: str, options: list[str], multi=False):
        opts = [o for o in options if o and o.strip() and not o.lower().startswith("select")]
        if not opts:
            return None
        r = rule_answer(self.prof, question, opts)
        if r:
            return self._rec(question, [r] if multi else r, "profile")
        try:
            a = self.ai.answer(question, opts, self.summary, multi)
        except AIError as e:
            self.used.append({"question": question[:200], "answer": "", "via": f"ai_error: {e}"}); return None
        if multi:
            a = [x for x in (a if isinstance(a, list) else [a]) if x in opts] or None
        else:
            a = a if a in opts else next((o for o in opts if a and a.lower() in o.lower()), None)
        return self._rec(question, a, "ai") if a else None
