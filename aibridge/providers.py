"""More AIs: run either seat on any OpenAI-compatible server, not only Claude and ChatGPT.

Profiles (Settings > AI models & sign-in > Other AIs) are saved in <data>/ai_profiles.json (app-owned; the AIs can't
write it). A key, if the server needs one, goes in .env as AIBRIDGE_AI_<ID>_KEY and is never sent to the page or the AIs.

Presets: Ollama and LM Studio (free, run on this PC, no key), OpenRouter and Gemini (need a key), or any custom URL.
A model that can't use tools still works: it gets a talk-only turn and you're told once.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import core

PRESETS = {
    "ollama": {"label": "Ollama", "base_url": "http://localhost:11434/v1", "needs_key": False, "local": True,
               "help": "Free, runs on this PC. Install from ollama.com, then e.g.  ollama pull qwen2.5  (pick a model that supports tools: qwen2.5, llama3.1, mistral-nemo). "
                       "Ollama gives models a small memory by default; for coding, set OLLAMA_CONTEXT_LENGTH=32768 (Windows: System > Environment Variables) and restart Ollama."},
    "lmstudio": {"label": "LM Studio", "base_url": "http://localhost:1234/v1", "needs_key": False, "local": True,
                 "help": "Free, runs on this PC. In LM Studio, load a model and start the Local Server (Developer tab)."},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True, "local": False,
                   "help": "One key for many models (some free). Make a key at openrouter.ai/keys."},
    "gemini": {"label": "Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "needs_key": True, "local": False,
               "help": "Google's OpenAI-compatible endpoint. Make a key in Google AI Studio (aistudio.google.com/apikey)."},
    "custom": {"label": "Custom", "base_url": "", "needs_key": False, "local": False,
               "help": "Any server that speaks the OpenAI chat-completions API (vLLM, llama.cpp server, Together, Groq, ...)."},
}
_ID = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
MAX_PROFILES = 20
TIMEOUT = 20


class ProviderError(ValueError):
    pass


def _path():
    return core.DATA / "ai_profiles.json"


def key_env(pid):
    return f"AIBRIDGE_AI_{pid.upper()}_KEY"


def _is_local(url):
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0)(:\d+)?(/|$)", url or ""))


def profiles():
    out = []
    for p in core._read_json(_path(), []) or []:
        if isinstance(p, dict) and _ID.match(str(p.get("id", ""))) and p.get("preset") in PRESETS:
            out.append({"id": p["id"], "label": str(p.get("label") or PRESETS[p["preset"]]["label"])[:40], "preset": p["preset"],
                        "base_url": str(p.get("base_url") or ""), "model": str(p.get("model") or "")[:120],
                        "vision": bool(p.get("vision", False)), "tools": p.get("tools", True) is not False,
                        "has_key": bool(os.getenv(key_env(p["id"])))})
    return out


def get(pid):
    p = next((x for x in profiles() if x["id"] == pid), None)
    if not p:
        raise ProviderError(f"there's no AI profile called {pid!r}. Add one in Settings > AI models & sign-in.")
    return p


def _clean_url(url):
    url = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", url):
        raise ProviderError("the server address must start with http:// or https://")
    if url.startswith("http://") and not _is_local(url):
        raise ProviderError("use https:// for a server that isn't on this PC (http would send your key in the clear)")
    return url


def save(b):
    """Add or edit a profile. b: {id?, preset, label, base_url, model, vision, tools, key?}. Returns (id, env updates)."""
    preset = b.get("preset")
    if preset not in PRESETS:
        raise ProviderError("pick a type: " + ", ".join(PRESETS))
    cur = [p for p in core._read_json(_path(), []) or [] if isinstance(p, dict)]
    pid = str(b.get("id") or "")
    if pid and not any(p.get("id") == pid for p in cur):
        raise ProviderError("that profile doesn't exist")
    label = re.sub(r"\s+", " ", str(b.get("label") or PRESETS[preset]["label"])).strip()[:40]
    base = _clean_url(b.get("base_url") or PRESETS[preset]["base_url"])
    model = str(b.get("model") or "").strip()[:120]
    if not pid:
        if len(cur) >= MAX_PROFILES:
            raise ProviderError(f"you can keep up to {MAX_PROFILES} AI profiles")
        stem = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:18] or preset
        stem = stem if stem[0].isalpha() else "ai_" + stem
        pid, n = stem, 2
        while any(p.get("id") == pid for p in cur) or len(pid) < 2:
            pid, n = f"{stem}_{n}", n + 1
    rec = {"id": pid, "preset": preset, "label": label, "base_url": base, "model": model,
           "vision": bool(b.get("vision", False)), "tools": b.get("tools", True) is not False}
    cur = [p for p in cur if p.get("id") != pid] + [rec]
    core._write_json(_path(), cur)
    env = {}
    key = str(b.get("key") or "").strip()
    if key:
        if any(c in key for c in "\r\n") or len(key) > 2000:
            raise ProviderError("that key doesn't look right")
        env[key_env(pid)] = key
    if b.get("clear_key"):
        env[key_env(pid)] = None
    return pid, env


def remove(pid):
    cur = [p for p in core._read_json(_path(), []) or [] if isinstance(p, dict)]
    if not any(p.get("id") == pid for p in cur):
        raise ProviderError("that profile doesn't exist")
    core._write_json(_path(), [p for p in cur if p.get("id") != pid])
    return {key_env(pid): None}


def _key(p):
    k = os.getenv(key_env(p["id"]), "")
    if PRESETS[p["preset"]]["needs_key"] and not k:
        raise ProviderError(f"{p['label']} needs an API key (Settings > AI models & sign-in > Other AIs).")
    return k


def _get_json(url, key="", timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Bridge", **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def list_models(p, timeout=TIMEOUT):
    """Model ids the server offers (OpenAI-style /models; Ollama's own /api/tags as a fallback)."""
    key = _key(p)
    try:
        d = _get_json(p["base_url"] + "/models", key, timeout)
        items = d.get("data") or d.get("models") or []
        ids = [str(m.get("id") or m.get("name") or "").replace("models/", "") for m in items if isinstance(m, dict)]
        ids = [i for i in ids if i]
        if ids:
            return sorted(set(ids))
    except (urllib.error.URLError, ValueError, OSError) as e:
        last = e
    else:
        last = None
    if p["preset"] == "ollama":
        try:
            root = re.sub(r"/v1$", "", p["base_url"])
            d = _get_json(root + "/api/tags", "", timeout)
            return sorted({m.get("name") for m in d.get("models", []) if m.get("name")})
        except (urllib.error.URLError, ValueError, OSError) as e:
            last = e
    raise ProviderError(_friendly(p, last))


def _friendly(p, e):
    msg = core.redact(f"{type(e).__name__}: {e}") if e else "the server returned no models"
    if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
        return f"{p['label']} refused the key ({e.code}). Check it in Settings."
    if isinstance(e, (urllib.error.URLError, ConnectionError, OSError)) and PRESETS[p["preset"]]["local"]:
        return f"Couldn't reach {p['label']} at {p['base_url']}. Is it running? ({msg})"
    return f"Couldn't talk to {p['label']}: {msg}"


def _post_json(url, body, key="", timeout=TIMEOUT):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": "AI-Bridge", "Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


SMALL_CONTEXT = 16_000


def capabilities(p, model):
    """What this model can do, as far as the server tells us: tools, images, context size. None = the server didn't say."""
    caps = {"tools": None, "vision": None, "context": None, "source": ""}
    key = _key(p)
    root = re.sub(r"/v1$", "", p["base_url"])
    try:
        if p["preset"] == "ollama":
            d = _post_json(root + "/api/show", {"model": model}, "")
            c = d.get("capabilities")
            if isinstance(c, list):
                caps["tools"], caps["vision"] = "tools" in c, "vision" in c
            ctx = [v for k, v in (d.get("model_info") or {}).items() if k.endswith(".context_length") and isinstance(v, int)]
            caps["context"] = ctx[0] if ctx else None
            caps["source"] = "ollama"
            return caps
        if p["preset"] == "lmstudio":
            d = _get_json(root + "/api/v0/models/" + urllib.request.quote(model, safe=""), "")
            c = d.get("capabilities")
            if isinstance(c, list):
                caps["tools"] = "tool_use" in c
            caps["vision"] = True if d.get("type") == "vlm" else (False if d.get("type") == "llm" else None)
            caps["context"] = d.get("max_context_length") if isinstance(d.get("max_context_length"), int) else None
            caps["source"] = "lmstudio"
            return caps
        d = _get_json(p["base_url"] + "/models", key)  # OpenRouter and some others describe each model here
        m = next((x for x in (d.get("data") or []) if isinstance(x, dict) and str(x.get("id", "")).replace("models/", "") == model), None)
        if m:
            sp = m.get("supported_parameters")
            if isinstance(sp, list):
                caps["tools"] = "tools" in sp
            mods = ((m.get("architecture") or {}).get("input_modalities"))
            if isinstance(mods, list):
                caps["vision"] = "image" in mods
            for k in ("context_length", "context_window", "max_context_length"):
                if isinstance(m.get(k), int):
                    caps["context"] = m[k]
            caps["source"] = "server"
    except (urllib.error.URLError, ValueError, OSError):
        pass
    return caps


def preflight(p, model):
    """Before a session starts: is the server up, is this model on it, and what can it do. Raises ProviderError with a fix."""
    if not model:
        raise ProviderError(f"pick a model for {p['label']}.")
    models = list_models(p)  # raises a friendly 'is it running?' error
    if models and model not in models and not (p["preset"] == "ollama" and model + ":latest" in models):
        near = ", ".join(models[:8]) + (" …" if len(models) > 8 else "")
        hint = f"  (download it with:  ollama pull {model})" if p["preset"] == "ollama" else ""
        raise ProviderError(f"{p['label']} doesn't have the model '{model}'.{hint} It has: {near}")
    return capabilities(p, model)


def reachable(p):
    try:
        list_models(p, timeout=3)
        return True
    except (ProviderError, OSError):
        return False


def test(pid):
    """Settings 'Test': can we reach it, which models, and does a tiny reply come back."""
    p = get(pid)
    t0 = time.time()
    try:
        models = list_models(p)
    except ProviderError as e:
        return {"ok": False, "error": str(e), "help": PRESETS[p["preset"]]["help"], "seconds": round(time.time() - t0, 1)}
    model = p["model"] or (models[0] if models else "")
    reply, err = "", ""
    if model:
        try:
            import openai
            c = openai.OpenAI(base_url=p["base_url"], api_key=_key(p) or "not-needed", timeout=60)
            r = c.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply with just: OK"}], max_tokens=8)
            reply = (r.choices[0].message.content or "").strip()[:40]
        except Exception as e:  # noqa: BLE001 - show whatever the server said
            err = core.redact(f"{type(e).__name__}: {e}")[:300]
    return {"ok": not err, "models": models[:200], "model": model, "reply": reply, "error": err,
            "seconds": round(time.time() - t0, 1), "help": PRESETS[p["preset"]]["help"] if err else ""}


class CompatAgent(core.ChatGPTAgent):
    """An AI on an OpenAI-compatible server, sitting in either seat. Same tools and rules as the others."""

    def __init__(self, profile, model=None, seat="chatgpt", client=None):
        p = profile
        if client is None:
            import openai
            client = openai.OpenAI(base_url=p["base_url"], api_key=_key(p) or "not-needed", timeout=300)
        super().__init__(client=client, model=model or p["model"], seat=seat)
        if not self.model:
            raise ProviderError(f"pick a model for {p['label']} (Settings > AI models & sign-in > Other AIs, then Test to list them).")
        self.profile, self.provider, self.vision = p, p["label"], p["vision"]
        self.tools_ok = p["tools"]
        self.caps, self.why_no_tools = {}, "" if p["tools"] else "its profile is set to talk-only"

    def apply_caps(self, caps):
        """Use what the server says the model can do (it beats the profile's checkboxes when it knows)."""
        self.caps = caps or {}
        if self.caps.get("tools") is False and self.tools_ok:
            self.tools_ok, self.why_no_tools = False, "the server says this model doesn't support tools"
        if self.caps.get("vision") is not None:
            self.vision = bool(self.caps["vision"])
        return self

    def turn(self, system, prompt, tools, max_steps=10):
        if not self.tools_ok:
            if self.why_no_tools:
                core.append_transcript("system", f"{core.PRETTY[self.name]} ({self.model}) is talk-only this session ({self.why_no_tools}): "
                                                 "it can plan and review but can't change files. For building, pick a model with tools (qwen2.5, llama3.1).")
                self.why_no_tools = ""
            return super().turn(system + "\n\n(You have no tools in this session: talk, plan and review in plain text.)", prompt, [], max_steps)
        try:
            return super().turn(system, prompt, tools, max_steps)
        except Exception as e:  # many small local models can't do tool calling: fall back to talking
            m = str(e).lower()
            if tools and any(w in m for w in ("tool", "function", "does not support", "not supported")):
                self.tools_ok = False
                core.append_transcript("system", f"{core.PRETTY[self.name]} ({self.model}) can't use tools, so it's talking only "
                                                 "from now on. For building, pick a model that supports tools (e.g. llama3.1, qwen2.5).")
                return super().turn(system + "\n\n(You have no tools: talk, plan and review in plain text.)", prompt, [], max_steps)
            raise
