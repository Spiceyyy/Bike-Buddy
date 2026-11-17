"""Faithfulness check: does the planner's reply match the route it planned?

The prompt tells the model to copy numbers and names from the `plan_route`
result. This checks that it did, without an LLM, so it runs on every reply and
in evals:

- numbers: every distance, riding time, climb and percentage in the reply must
  match the result or the rider's message
- names:   every proper name in the reply must appear in the result or the
  rider's message, written the same way. Names in the result (start, end, via
  places, the stop, scenery highlights) lie on the route by construction, so
  this enforces "every place named lies on the route". A search_places hit
  that was not routed through does not count.
- scenery: a claim that the route goes along the water, through green space
  or on quiet streets needs a highlight with that reason. The result has no
  surface data, so any claim that the route is paved or gravel is unsupported.
- facts:   the reply must give the distance and the riding time
- caveats: when the result says a stop, a climbing limit or a loop distance
  could not be met, the reply must say so

Name and caveat detection are heuristics over English and French text. The
eval reviews every flagged reply by hand to measure false alarms.
"""

import json
import re
import unicodedata
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from pydantic import BaseModel

from app.agent.prompts import load_prompt
from app.models.llm import ToolResultMessage

PLAN_TOOL = "plan_route"
# Rounding is fine: 29.8 km may be "30 km", but not "35 km".
KM_TOLERANCE = 0.5
MINUTES_TOLERANCE = 1
# Climbing may be rounded to the nearest 10 m ("about 120 m" for 118 m).
CLIMB_TOLERANCE_M = 5
PERCENT_TOLERANCE = 1


class ProblemKind(str, Enum):
    WRONG_NUMBER = "wrong_number"
    UNKNOWN_NAME = "unknown_name"
    UNSUPPORTED_SCENERY = "unsupported_scenery"
    WRONG_CAVEAT = "wrong_caveat"
    MISSING_FACT = "missing_fact"
    MISSING_CAVEAT = "missing_caveat"


class Problem(BaseModel):
    kind: ProblemKind
    detail: str


def normalize(text: str) -> str:
    """Case, accents, hyphens and apostrophes do not change a name or a word."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    plain = re.sub(r"[^\w\s]|_", " ", plain)
    return " ".join(plain.split())


# --- numbers ----------------------------------------------------------------

_NUMBER = r"(?<![\w.,])(\d+(?:[.,]\d+)?)"
_HOURS = r"(?:h|hr|hrs|hours?|heures?)"
_MINUTES = r"(?:min|mins|minutes?)"
_DURATION = re.compile(
    rf"(?<![\w.,])(\d+)\s*{_HOURS}(?![^\W\d_])(?:\s*(?:and|et)?\s*(\d+)(?:\s*{_MINUTES}\b)?)?"
    rf"|(?<![\w.,])(\d+)\s*{_MINUTES}\b",
    re.IGNORECASE,
)
_KM = re.compile(rf"{_NUMBER}\s*(?:km|kilom[eè]t(?:er|re)s?)\b", re.IGNORECASE)
_METRES = re.compile(rf"{_NUMBER}\s*(?:m|meters?|metres?|mètres?)\b", re.IGNORECASE)
_PERCENT = re.compile(rf"{_NUMBER}\s*(?:%|percent|pour\s*cent)", re.IGNORECASE)
_ANY_NUMBER = re.compile(rf"{_NUMBER}(?!\w)")


def _numbers(pattern: re.Pattern[str], text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in pattern.findall(text)]


def durations_in(text: str) -> list[int]:
    """Durations in minutes: "1 h 27 min", "1 hour and 27 minutes", "1h27", "45 min"."""
    found = []
    for hours, minutes, only_minutes in _DURATION.findall(text):
        if only_minutes:
            found.append(int(only_minutes))
        else:
            found.append(int(hours) * 60 + int(minutes or 0))
    return found


def _near_any(value: float, allowed: Iterable[float], tolerance: float) -> bool:
    return any(abs(value - a) <= tolerance for a in allowed)


def _values(content: Any, wanted: Callable[[str], bool], key: str = "") -> list[float]:
    """Every number in the result stored under a key that `wanted` accepts."""
    if isinstance(content, dict):
        return [v for k, item in content.items() for v in _values(item, wanted, k)]
    if isinstance(content, list):
        return [v for item in content for v in _values(item, wanted, key)]
    if isinstance(content, (int, float)) and not isinstance(content, bool) and wanted(key):
        return [float(content)]
    return []


# --- names ------------------------------------------------------------------

# Capitalised words that start sentences or label things rather than name places.
_COMMON_WORDS = {
    normalize(word)
    for word in (
        "a an the this that these those your you it its i we there here my our "
        "if on in at from to along after before then and or but with for about "
        "around near past via through unfortunately sorry note also enjoy ride "
        "start head pick choose click tap try no yes please loop route routes "
        "best match most scenic hilliest flattest other another option options "
        "map stop café cafe coffee climbing climb distance time riding total "
        "approximately roughly about expect plan planned surface vibe vibes "
        "preferences preference is are was will would can could may might when "
        "where what which how km min h ai gps it's you'll you're i'm i've here's "
        "there's this's along "
        # The second half of a contraction, after normalize: "you'll" -> "you ll".
        "ll re ve s m t "
        "much many some all quiet green water nice lovely great beautiful scenic "
        "flat hilly easy overall mostly notez paved unpaved gravel surfaces "
        # French
        "le la les un une des du de d l ce cette ces votre vos vous il elle nous "
        "je j en au aux à par pour avec sans sur dans vers depuis puis ensuite et "
        "ou mais si voici voilà malheureusement désolé boucle itinéraire "
        "itinéraires trajet parcours départ arrivée arrêt carte autre autres "
        "choisir choisissez cliquez comptez environ durée dénivelé temps aucun "
        "aucune pas meilleur meilleure plus pittoresque c ça cela y est sont sera "
        "passe passez profitez bonne bon vous pouvez"
    ).split()
}
# Lower-case words allowed inside a name: "Parc du Mont-Royal", "Bank of Montreal".
# Not "and" or "the": "Rue Willibrord and the Piste cyclable" is two names.
_CONNECTORS = {normalize(w) for w in "de du des la le les d l of à au aux sur en".split()}
_SENTENCES = re.compile(r"(?<=[.!?;:])\s+|[\n\"“”«»()\[\],]")
_WORD = re.compile(r"[^\W_][\w'’\-]*")
# French elision before a name: "l'Oratoire", "d'Youville".
_ELIDED_NAME = re.compile(r"^[dlDL]['’][A-ZÀ-ÖØ-Þ]")


def _is_common(word: str) -> bool:
    words = normalize(word).split()
    return bool(words) and all(w in _COMMON_WORDS or w in _CONNECTORS for w in words)


def candidate_names(text: str) -> list[str]:
    """Runs of capitalised words, joined by connectors: likely proper names."""
    names: list[str] = []
    for sentence in _SENTENCES.split(text):
        run: list[str] = []
        pending: list[str] = []
        for index, word in enumerate(_WORD.findall(sentence)):
            capitalised = word[0].isupper() or bool(_ELIDED_NAME.match(word))
            if capitalised and not (index == 0 and _is_common(word)):
                run += [*pending, word]
                pending = []
            elif run and normalize(word) in _CONNECTORS:
                pending.append(word)
            else:
                if run:
                    names.append(" ".join(run))
                run, pending = [], []
        if run:
            names.append(" ".join(run))
    return [name for name in names if not _is_common(name)]


def _core(name: str) -> str:
    """The name without leading common words: "The Lachine Canal" -> "lachine canal"."""
    words = normalize(name).split()
    while words and (words[0] in _COMMON_WORDS or words[0] in _CONNECTORS):
        words.pop(0)
    return " ".join(words)


# --- scenery claims ---------------------------------------------------------

# Phrases claiming a kind of scenery, keyed by the highlight reason that backs them.
_SCENERY_CLAIMS = {
    "along the water": (
        r"\b(?:along|by|beside|follows?|following|next to) the water\b|waterfront|riverside|"
        r"le long de l'eau|au bord de l'eau|bord de l'eau|longe(?:ant)? l'eau"
    ),
    "through green space": (
        r"green ?space|greenery|leafy|espaces? verts?|verdure"
    ),
    "on quiet streets": (
        r"quiet (?:street|road|residential)s?|low[- ]traffic|rues? (?:calmes|tranquilles)|"
        r"peu de circulation"
    ),
}
# The result has no surface data, so the route's surface cannot be described.
_SURFACE_CLAIM = (
    r"\b(?:route|ride|loop|it|this|trajet|parcours|boucle|itinéraire)\b[^.!?]{0,30}?"
    r"\b(?:is|est|will be|sera|stays|reste)\s+(?:fully |entirely |mostly |all |entièrement |surtout )?"
    r"(?:paved|unpaved|gravel|asphalt(?:ed)?|asphalté|pavé|en gravier)\b"
)
# Sentences about unsupported preferences ("water preferences are not supported") claim nothing.
_NOT_SUPPORTED = r"not (?:yet )?supported|unsupported|prise? en (?:charge|compte)"


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


# --- caveats ----------------------------------------------------------------

_NEGATION = (
    r"\b(?:no|not|couldn'?t|could not|can'?t|cannot|unable|wasn'?t|isn'?t|didn'?t|"
    r"over|above|exceeds?|more than|closest|nearest|"
    r"aucun|aucune|pas|impossible|dépasse|plus de|au-dessus|plus proche)\b"
)
# Each caveat counts as said when a negation shares a sentence with its topic.
_CAVEATS = {
    "stop_found": (
        r"caf[eé]|coffee|bakery|stop|arr[eê]t|boulangerie",
        "no stop was found near the route",
    ),
    "climb_on_target": (
        r"climb|\d\s*m\b|elevation|limit|d[eé]nivel|mont[ée]e|limite",
        "the climbing limit was not met",
    ),
    "distance_on_target": (
        r"km|distance|long|closest|plus proche",
        "the loop is not the requested distance",
    ),
}


def _says(reply: str, topic: str) -> bool:
    return any(
        re.search(_NEGATION, s, re.IGNORECASE) and re.search(topic, s, re.IGNORECASE)
        for s in _sentences(reply)
    )


# --- the check --------------------------------------------------------------


def last_plan_result(tool_results: Sequence[ToolResultMessage]) -> dict[str, Any] | None:
    plans = [result.content for result in tool_results if result.name == PLAN_TOOL]
    return plans[-1] if plans else None


def check_reply(
    reply: str, user_message: str, tool_results: Sequence[ToolResultMessage]
) -> list[Problem]:
    """Problems with a reply that describes a planned route. Other replies pass."""
    plan = last_plan_result(tool_results)
    if plan is None or plan.get("status") != "ok":
        return []
    return [
        *_check_numbers(reply, user_message, plan),
        *_check_names(reply, user_message, plan),
        *_check_scenery(reply, plan),
        *_check_caveats(reply, plan),
    ]


def _check_numbers(reply: str, user_message: str, plan: dict[str, Any]) -> list[Problem]:
    problems: list[Problem] = []

    def wrong(detail: str) -> None:
        problems.append(Problem(kind=ProblemKind.WRONG_NUMBER, detail=detail))

    def missing(detail: str) -> None:
        problems.append(Problem(kind=ProblemKind.MISSING_FACT, detail=detail))

    from_message = _numbers(_ANY_NUMBER, user_message)

    km_allowed = [*_values(plan, lambda key: key.endswith("_km")), *from_message]
    km_in_reply = _numbers(_KM, reply)
    for value in km_in_reply:
        if not _near_any(value, km_allowed, KM_TOLERANCE):
            wrong(f"{value:g} km is not in the result")
    if not km_in_reply:
        missing("the distance is not given")

    riding_time = plan.get("riding_time", "")
    expected_minutes = durations_in(riding_time)
    reply_minutes = durations_in(reply)
    for minutes in reply_minutes:
        if expected_minutes and abs(minutes - expected_minutes[0]) > MINUTES_TOLERANCE:
            wrong(f"a riding time of {minutes} min, but the result says {riding_time}")
    if not reply_minutes:
        missing("the riding time is not given")

    climb_allowed = [*_values(plan, lambda key: key == "climb_m"), *from_message]
    for value in _numbers(_METRES, reply):
        if not _near_any(value, climb_allowed, CLIMB_TOLERANCE_M):
            wrong(f"{value:g} m is not in the result")

    share = (plan.get("scenery") or {}).get("scenic_share")
    percent_allowed = [*from_message, *([share * 100] if share is not None else [])]
    for value in _numbers(_PERCENT, reply):
        if not _near_any(value, percent_allowed, PERCENT_TOLERANCE):
            wrong(f"{value:g}% is not in the result")
    return problems


def _check_names(reply: str, user_message: str, plan: dict[str, Any]) -> list[Problem]:
    known = f" {normalize(user_message)} {normalize(json.dumps(plan, ensure_ascii=False))} "
    return [
        Problem(kind=ProblemKind.UNKNOWN_NAME, detail=f'"{name}" is not in the request or the route')
        for name in candidate_names(reply)
        if (core := _core(name)) and f" {core} " not in known
    ]


def _check_scenery(reply: str, plan: dict[str, Any]) -> list[Problem]:
    reasons = {h.get("why") for h in (plan.get("scenery") or {}).get("highlights", [])}
    claims = [s for s in _sentences(reply) if not re.search(_NOT_SUPPORTED, s, re.IGNORECASE)]
    problems = [
        Problem(
            kind=ProblemKind.UNSUPPORTED_SCENERY,
            detail=f'the reply says the route goes {reason}, but no highlight is "{reason}"',
        )
        for reason, pattern in _SCENERY_CLAIMS.items()
        if reason not in reasons and any(re.search(pattern, s, re.IGNORECASE) for s in claims)
    ]
    if re.search(_SURFACE_CLAIM, reply, re.IGNORECASE):
        problems.append(
            Problem(
                kind=ProblemKind.UNSUPPORTED_SCENERY, detail="the reply describes the route's surface"
            )
        )
    return problems


# Saying a loop missed its distance when it did not.
_CLOSEST_LOOP = r"closest loop|closest .{0,20}distance|plus proche"


def _check_caveats(reply: str, plan: dict[str, Any]) -> list[Problem]:
    problems = [
        Problem(kind=ProblemKind.MISSING_CAVEAT, detail=f"the reply does not say {text}")
        for key, (topic, text) in _CAVEATS.items()
        if plan.get(key) is False and not _says(reply, topic)
    ]
    if plan.get("distance_on_target") is True and re.search(_CLOSEST_LOOP, reply, re.IGNORECASE):
        problems.append(
            Problem(
                kind=ProblemKind.WRONG_CAVEAT,
                detail="the reply says the loop missed its distance, but it is on target",
            )
        )
    return problems


def feedback(problems: Sequence[Problem]) -> str:
    """The message that asks the model to fix its reply."""
    lines = "\n".join(f"- {problem.detail}" for problem in problems)
    return load_prompt("reply_feedback").replace("{problems}", lines)


def reply_check(user_message: str) -> Callable[[str, Sequence[ToolResultMessage]], str | None]:
    """The agent loop's check for one request: feedback when the reply is unfaithful."""

    def check(reply: str, tool_results: Sequence[ToolResultMessage]) -> str | None:
        problems = check_reply(reply, user_message, tool_results)
        return feedback(problems) if problems else None

    return check
