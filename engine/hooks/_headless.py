"""Asks the AI the user chose one question, with nothing but the question.

~/.brain-in-a-box/agents.json (_agents.py) is the only source of who may be asked: the preferred
engine first, then the other one only when it is listed, as a fallback; never a program found on
the disk. Without the file, Claude alone, asked exactly as before the choice existed: the same
call, and nothing in the outputs names an engine. A file the kit cannot trust (_agents.AUCUN)
gives no engine at all: nothing is asked (HeadlessError "agents") until the install is run
again, since whoever chose Codex alone excluded Claude.

Each engine has its own launcher, cage and checks: _headless_claude.py, _headless_codex.py
(loaded only when Codex is listed). This module picks, tries again and falls back:
- a second try (essais=2) only after what a second try can fix: a timeout or a service error for
  Claude, a service error only for Codex, which has no spending cap (a call that timed out may
  have cost, docs/proposals/codex.md §2.3);
- the other engine only when the error shows the first one cannot answer now: it does not start,
  is not supported or is paused, is not logged in with the subscription, is at its usage limit,
  or its service failed again; after a timeout, only towards Claude. Never after init (it did not
  run as required) nor invalid (its answer was off): anomalies are shown, not covered.

    answer = run(prompt, schema, system_prompt, claude={...}, codex={...})
    answer.output    # the JSON object the engine returned, already parsed
    answer.moteur    # "claude" or "codex", named only when Codex is one of the agents, else None
    check(claude={...}, codex={...})   # (moteur, version) of the first engine that may be asked

`claude` holds _headless_claude.run's settings (budget_usd, timeout, max_turns, model, effort,
command, work_root, git), `codex` _headless_codex.run's (timeout, effort, command, work_root):
Codex's folder is agents.json's, its model BRAIN_CODEX_MODEL's, else Codex's own default.
Used by daily-reflection.py and the night check's review (_controle_judge.py). Not a hook.
"""
import os
import time
from collections import namedtuple

import _agents
import _headless_claude as claude_engine
from _headless_claude import HeadlessError

# secours: what the preferred engine said, when the other one answered instead.
Answer = namedtuple("Answer", "output moteur version cost_usd num_turns tokens secours")
# Errors that show an engine cannot answer now, so that the other one listed may.
SECOURS = ("start", "unsupported", "auth", "quota", "api")
MODEL_VAR = "BRAIN_CODEX_MODEL"


def lire():
    """agents.json, as _agents reads it: (Choix, etat). A task reads it once and passes it on, so
    that what it gathers and who it asks come from the same reading."""
    return _agents.lire()


def moteurs(agents=None):
    """(the engines in the order they are asked, etat): the preferred one, then the other one
    listed; none when agents.json cannot be trusted."""
    choix, etat = agents or lire()
    return tuple(sorted(choix.agents, key=lambda a: a != choix.prefere)), etat


def texte(e):
    """An error as a run reports it: "kind: detail", after the engine's name when it is named."""
    moteur = getattr(e, "moteur", None)
    return ("%s: " % moteur if moteur else "") + "%s: %s" % (e.kind, e.detail)


def _codex_engine():
    import _headless_codex   # here: Claude alone never loads it
    return _headless_codex


def _model():
    model = os.environ.get(MODEL_VAR, "").strip() or None
    if model is not None and not _codex_engine().MODEL_NAME.fullmatch(model):
        raise HeadlessError("unsupported", "%s is not a model name" % MODEL_VAR, "model")
    return model


def _retry(moteur, e):
    return e.retryable and not (moteur == "codex" and e.kind == "timeout")


def _fallback(e, moteur):
    return e.kind in SECOURS or (e.kind == "timeout" and moteur == "claude")


def _in_order(agents, ask, essais=1, pause=0, journal=None):
    """ask(moteur, choix) with each engine in order, tries and fallbacks as above. Returns
    (moteur or None, result, the preferred's error said when the other one answered)."""
    choix, etat = agents or lire()
    order, _ = moteurs((choix, etat))
    if not order:
        raise HeadlessError("agents", "~/.brain-in-a-box/agents.json cannot be trusted (%s): no AI is asked "
                                      "until the install is run again" % etat, etat)
    named = "codex" in choix.agents
    failed = []
    for moteur in order:
        if failed and not _fallback(failed[0][1], moteur):
            break
        for essai in range(1, essais + 1):
            try:
                result = ask(moteur, choix)
            except HeadlessError as e:
                e.moteur = moteur if named else None
                if journal is not None:
                    journal(e.moteur, essai, essais, e)
                if essai < essais and _retry(moteur, e):
                    time.sleep(pause)
                    continue
                failed.append((moteur, e))
                break
            return (moteur if named else None), result, (texte(failed[0][1]) if failed else None)
    first = failed[0][1]
    if len(failed) > 1:
        e = failed[-1][1]
        first = HeadlessError(first.kind, "%s; then %s" % (first.detail, texte(e)), first.reason)
        first.moteur = failed[0][1].moteur
    raise first


def run(prompt, schema, system_prompt, *, claude, codex, essais=1, pause=0, journal=None, agents=None):
    """One isolated question to the chosen AI. Returns an Answer, or raises HeadlessError: the
    preferred engine's error (the fallback's in its detail), its `moteur` set as Answer's.
    essais: tries per engine; pause: seconds before a second one; journal(moteur, essai, essais,
    error) hears each failed try; agents: what lire() gave, else agents.json is read now."""
    def ask(moteur, choix):
        if moteur == "claude":
            a = claude_engine.run(prompt, schema, system_prompt, **claude)
            return a.output, a.version, a.cost_usd, a.num_turns, None
        a = _codex_engine().run(prompt, schema, system_prompt, codex_home=choix.codex_home, model=_model(), **codex)
        return a.output, a.version, a.cost_usd, a.num_turns, a.tokens
    moteur, (output, version, cost, turns, tokens), secours = _in_order(agents, ask, essais, pause, journal)
    return Answer(output, moteur, version, cost, turns, tokens, secours)


def check(*, claude, codex, agents=None):
    """The first engine, in order, that may be asked, without asking anything: (moteur, version),
    moteur as in run(). HeadlessError otherwise, as run() raises it."""
    def probe(moteur, choix):
        if moteur == "claude":
            return claude_engine.check(**claude)
        _model()
        return _codex_engine().check(choix.codex_home, **codex)
    moteur, version, _ = _in_order(agents, probe)
    return moteur, version
