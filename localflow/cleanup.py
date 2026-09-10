"""Deterministic rules pass (~1 ms). Runs before (and dictionary again after) the optional LLM.

Order:
  1. trailing "press enter" -> flag
  2. filler words / phrases
  3. spoken punctuation ("comma", "period", "new line", "at sign", ...)
  4. Backtrack ("scratch that", "no wait", "no no", "oops", "I meant", "correction", "I mean",
     "actually") -> drop back to the previous clause / sentence boundary
  5. duplicate-word collapse ("the the")
  6. dictionary replacements (whole word, case-insensitive)
  7. snippets (whole-word trigger phrase -> expansion)
  8. spacing / capitalization tidy-up
  9. spoken lists ("I need to get milk, eggs and bread" -> lead-in + "- item" lines)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# ---------------------------------------------------------------- spoken punctuation
# (phrase, symbol, mode). Modes control spacing:
#   punct   attach to previous word, space after      ("hello comma world" -> "hello, world")
#   newline replace with line break(s)
#   prev    attach to previous word                    ("fifty percent sign" -> "fifty%")
#   next    attach to next word                        ("hashtag win" -> "#win")
#   join    no spaces either side                      ("bob at sign example" -> "bob@example")
#   spaced  spaces both sides                          ("a dash b" -> "a - b")
SPOKEN_PUNCT: list[tuple[str, str, str]] = [
    ("start a new paragraph", "\n\n", "newline"),
    ("new paragraph", "\n\n", "newline"),
    ("next paragraph", "\n\n", "newline"),
    ("new line", "\n", "newline"),
    ("next line", "\n", "newline"),
    ("line break", "\n", "newline"),
    ("question mark", "?", "punct"),
    ("exclamation point", "!", "punct"),
    ("exclamation mark", "!", "punct"),
    ("full stop", ".", "punct"),
    ("period", ".", "punct"),
    ("comma", ",", "punct"),
    ("semicolon", ";", "punct"),
    ("semi colon", ";", "punct"),
    ("colon", ":", "punct"),
    ("ellipsis", "...", "punct"),
    ("dot dot dot", "...", "punct"),
    ("em dash", "—", "spaced"),
    ("en dash", "–", "spaced"),
    ("hyphen", "-", "join"),
    ("dash", "-", "spaced"),
    ("open quote", "“", "next"),
    ("close quote", "”", "prev"),
    ("open paren", "(", "next"),
    ("open parenthesis", "(", "next"),
    ("close paren", ")", "prev"),
    ("close parenthesis", ")", "prev"),
    ("open bracket", "[", "next"),
    ("close bracket", "]", "prev"),
    ("ampersand", "&", "spaced"),
    ("percent sign", "%", "prev"),
    ("at sign", "@", "join"),
    ("at symbol", "@", "join"),
    ("hashtag", "#", "next"),
    ("hash sign", "#", "next"),
    ("pound sign", "#", "next"),
    ("dollar sign", "$", "next"),
    ("asterisk", "*", "spaced"),
    ("plus sign", "+", "spaced"),
    ("equals sign", "=", "spaced"),
    ("forward slash", "/", "join"),
    ("slash", "/", "join"),
    ("backslash", "\\", "join"),
    ("underscore", "_", "join"),
    ("trademark", "™", "prev"),
]
SPOKEN_PUNCT.sort(key=lambda kv: -len(kv[0]))

_CLAUSE_BOUNDARY = re.compile(r"[.!?;:\n]|,")
_SENTENCE_END = re.compile(r"[.!?\n]")
# strong triggers that are ordinary words when they open the dictation ("I meant it when...")
_KEEP_IF_INITIAL = {"i meant"}

# words that legitimately repeat
_REPEAT_ALLOW = {"had", "that", "very", "no", "ha", "bye", "so", "is", "do", "you", "we", "were", "it", "in", "on"}


@dataclass
class CleanResult:
    text: str
    press_enter: bool = False
    snippet_fired: bool = False
    backtracks: int = 0
    list_fired: bool = False


def _phrase_re(phrase: str, flags=re.IGNORECASE) -> re.Pattern:
    """Whole-word regex for a (multi-word) phrase; tolerant of ASR punctuation between words."""
    words = [re.escape(w) for w in phrase.strip().split()]
    body = r"[\s,]+".join(words)
    return re.compile(rf"(?<![\w'])({body})(?![\w'])", flags)


def _lit(s: str):
    """Literal replacement callable (avoids re.sub escape processing of e.g. a backslash)."""
    return lambda m, _s=s: _s


# ---------------------------------------------------------------- steps
def strip_press_enter(text: str, phrases: Iterable[str]) -> tuple[str, bool]:
    for ph in phrases:
        pat = re.compile(rf"[\s,]*(?<![\w])({_phrase_re(ph).pattern})[\s.!?]*$", re.IGNORECASE)
        m = pat.search(text)
        if m:
            return text[: m.start()].rstrip(), True
    return text, False


def remove_fillers(text: str, fillers: Iterable[str], phrases: Iterable[str] = ()) -> str:
    fl = [f for f in fillers if f]
    if fl:
        alt = "|".join(re.escape(f) for f in fl)
        # ", uh," -> ""  |  "um, hello" -> "hello"  |  "great, um." -> "great."
        pat = re.compile(rf"(,\s*)?(?<![\w'])(?:{alt})(?![\w'])([,.!?]?)\s*", re.IGNORECASE)

        def repl(m: re.Match) -> str:
            lead, trail = m.group(1), m.group(2)
            if m.start() == 0:
                return ""  # "Um, hello" / "Hmm. Let me" -> "hello" / "Let me"
            if trail in (".", "!", "?"):
                return trail + " "  # "great, um." -> "great."
            if lead and trail == ",":
                return " "  # "I think, uh, we" -> "I think we"
            return " " if lead is None else lead
        text = pat.sub(repl, text)
    for ph in phrases:
        if ph:
            text = _phrase_re(ph).sub("", text)
            text = re.sub(r"\s+,", ",", text)
    return text


def spoken_punctuation(text: str) -> str:
    for phrase, sym, mode in SPOKEN_PUNCT:
        pat = _phrase_re(phrase).pattern
        if mode == "newline":
            text = re.sub(rf"[\s,]*{pat}[\s,.]*", _lit(sym), text, flags=re.IGNORECASE)
        elif mode == "punct":
            # "test comma scratch" / "test, comma, scratch" -> "test, scratch"
            text = re.sub(rf"[\s,.;:]*{pat}[,.;:]*\s*", _lit(sym + " "), text, flags=re.IGNORECASE)
        elif mode == "prev":
            text = re.sub(rf"[\s,]*{pat}[\s,]*", _lit(sym + " "), text, flags=re.IGNORECASE)
        elif mode == "next":
            text = re.sub(rf"[\s,]*{pat}[\s,]*", _lit(" " + sym), text, flags=re.IGNORECASE)
        elif mode == "join":
            text = re.sub(rf"[\s,]*{pat}[\s,]*", _lit(sym), text, flags=re.IGNORECASE)
        else:  # spaced
            text = re.sub(rf"[\s,]*{pat}[\s,]*", _lit(" " + sym + " "), text, flags=re.IGNORECASE)
    # The ASR often punctuates for us, so a spoken "question mark" at the end of a sentence
    # it already ended lands as "Friday??". Collapse a run of terminal marks to the last one
    # the speaker actually asked for.
    def _collapse(m: re.Match[str]) -> str:
        run = m.group(0)
        return run if run == "..." else run[-1]  # keep a deliberate ellipsis

    text = re.sub(r"[.!?]{2,}", _collapse, text)
    text = re.sub(r",\s*([.!?])", r"", text)
    return text


def _clause_start(text: str, pos: int) -> int:
    """Index where the clause containing `pos` starts (after the previous boundary)."""
    last = -1
    for m in _CLAUSE_BOUNDARY.finditer(text, 0, pos):
        last = m.end()
    return max(last, 0)


# a previous "clause" made only of these is an aborted false start, not content ("send, no, I mean...")
_ABORT_WORDS = {"no", "nope", "wait", "um", "uh", "sorry", "hmm", "hm", "er", "erm"}


def _prev_clause_start(text: str, start: int) -> int:
    """Start of the clause/sentence before the one beginning at `start`.

    After a full stop the whole previous sentence goes; after a comma only the previous clause.
    A previous clause made of abort words only ("no", "wait") is skipped as well, so
    "send, no, I mean to say ..." drops the real clause too.
    """
    for _ in range(4):
        if start <= 0:
            return 0
        prev_char = text[start - 1]
        if prev_char in ".!?\n":
            prev_start = 0
            for mm in _SENTENCE_END.finditer(text, 0, start - 1):
                prev_start = mm.end()
        else:
            prev_start = _clause_start(text, max(0, start - 2))
        chunk = re.sub(r"[^\w']+", " ", text[prev_start:start]).strip().lower()
        start = prev_start
        if chunk and all(w in _ABORT_WORDS for w in chunk.split()):
            continue  # "no," / "wait." alone is not what the speaker meant to keep either
        return start
    return start


def backtrack(text: str, strong: Iterable[str], weak: Iterable[str]) -> tuple[str, int]:
    """Drop the clause preceding a self-correction trigger.

    Triggers are processed left to right in the order they were spoken (so an earlier correction
    never sees text that a later one already removed). Consecutive triggers ("no, no, wait. I meant
    to say", "I mean, I meant to say") are merged into one correction.

    strong triggers ("scratch that") always drop the preceding clause: the words before the trigger
    in its own clause, or - when the trigger itself starts the clause - the previous clause (the
    previous sentence when the trigger follows a full stop). weak triggers ("actually", "I mean")
    only fire when they begin a comma-clause; otherwise just the word itself is removed ("Actually,
    I think..."). A strong trigger that opens the whole dictation removes only itself; "I meant"
    there is left alone ("I meant it when I said ...").
    """
    strong_l = [p for p in strong if p]
    weak_l = [p for p in weak if p]
    if not strong_l and not weak_l:
        return text, 0
    strong_set = {p.lower() for p in strong_l}
    # longest first so "I meant to say" wins over "I meant", "no no wait" over "no no"
    phrases = sorted(strong_l + weak_l, key=lambda p: -len(p.split()))
    alt = "|".join(f"(?P<p{i}>{_phrase_re(p).pattern})" for i, p in enumerate(phrases))
    pat = re.compile(rf"(?<![\w'])(?:{alt})(?![\w'])", re.IGNORECASE)
    sep = re.compile(r"[\s,.!?;:]*")

    def which(m: re.Match) -> str:
        for i, p in enumerate(phrases):
            if m.group(f"p{i}") is not None:
                return p.lower()
        return ""

    n = 0
    pos = 0
    while True:
        m = pat.search(text, pos)
        if not m:
            break
        first = which(m)
        is_strong = first in strong_set
        # merge directly following triggers ("no no wait. I meant to say", "I mean, I meant to say")
        end = m.end()
        while True:
            m2 = pat.match(text, sep.match(text, end).end())
            if not m2:
                break
            end = m2.end()
            is_strong = is_strong or which(m2) in strong_set
        start = _clause_start(text, m.start())
        between = text[start : m.start()]
        prev_char = text[start - 1] if start > 0 else ""
        tail = re.match(r"[,.]?\s*", text[end:]).end()  # punctuation glued to the trigger
        if not is_strong and (between.strip() or prev_char != ","):
            # weak trigger not opening a comma-clause: drop the word only
            text = text[: m.start()] + text[end + tail :]
            pos = m.start()
            n += 1
            continue
        if start == 0 and not between.strip():
            if first in _KEEP_IF_INITIAL:
                pos = m.end()  # nothing to correct yet: ordinary words
                continue
            # trigger opens the dictation: nothing before it to drop
            text = text[end + tail :].lstrip()
            pos = 0
            n += 1
            continue
        if not between.strip():
            start = _prev_clause_start(text, start)
        head = text[:start].rstrip()
        text = (head + " " + text[end + tail :].lstrip()).strip()
        pos = len(head)
        n += 1
    return text, n


def collapse_repeats(text: str) -> str:
    def repl(m: re.Match) -> str:
        w = m.group(1)
        if w.lower() in _REPEAT_ALLOW or len(w) < 2:
            return m.group(0)
        return w

    return re.sub(r"\b(\w+)(?:[ ,]+\1\b)+", repl, text, flags=re.IGNORECASE)


def apply_dictionary(text: str, mapping: dict[str, str] | None) -> str:
    for wrong, right in (mapping or {}).items():
        if not wrong:
            continue
        text = _phrase_re(str(wrong)).sub(_lit(str(right)), text)
    return text


def apply_snippets(text: str, snippets: dict[str, str] | None) -> tuple[str, bool]:
    fired = False
    for trig, exp in (snippets or {}).items():
        if not trig:
            continue
        pat = _phrase_re(str(trig))
        # whole dictation is just the trigger (optionally with a trailing period)
        if re.fullmatch(rf"{pat.pattern}[.!?]?", text.strip(), re.IGNORECASE):
            return str(exp), True
        new = pat.sub(_lit(str(exp)), text)
        if new != text:
            fired = True
            text = new
    return text, fired


def tidy(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # no space before punctuation; one space after (unless newline/end)
    text = re.sub(r" +([,.;:!?%™)\]”])", r"\1", text)
    text = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", text)
    text = re.sub(r"(?<!\.)([.!?])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r"([(\[“#$]) +", r"\1", text)
    # dedupe punctuation: ",," ",." ".," "?." ". ." -> keep the strongest / one
    text = re.sub(r"([,;:])\s*[,;:]+", r"\1", text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"([.!?])\s*,", r"\1", text)
    text = re.sub(r"\.(?:\s+\.)+", ".", text)
    # capitalisation: start of text / line, after sentence end (not after "..."), standalone i
    text = re.sub(r"(?<![\w])i(?=[' ]|$)", "I", text)
    text = re.sub(r"(^|(?<!\.)[.!?]\s+|\n\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"^\s*([a-z])", lambda m: m.group(1).upper(), text)
    text = re.sub(r"^[\s,;:]+", "", text)
    text = "\n".join(ln.strip() for ln in text.split("\n"))
    return text.strip()


# ---------------------------------------------------------------- spoken lists
DEFAULT_LIST_LEADINS = [
    "I need to get", "I need to buy", "I need to pick up", "I need to do", "we need to get", "we need to buy",
    "the list is", "the items are", "the following", "here is the list", "here's the list", "things to do",
    "things to get", "things to buy", "shopping list", "to do list", "to-do list", "the steps are",
    "the options are",
]
_LIST_SPLIT = re.compile(r"\s*,\s*(?:and\s+|or\s+)?|\s*;\s*|\s+and\s+|\s+or\s+", re.IGNORECASE)
_LIST_ITEM_BAD_START = {"then", "also", "so", "but", "because", "which", "that"}
_HAS_LIST_LINE = re.compile(r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s")


def _list_items(body: str, max_words: int) -> list[str] | None:
    items = [it.strip(" ,;") for it in _LIST_SPLIT.split(body)]
    items = [it for it in items if it]
    if len(items) < 3:
        return None
    for it in items:
        words = it.split()
        if not (1 <= len(words) <= max_words) or not re.search(r"\w", it):
            return None
        if words[0].lower() in _LIST_ITEM_BAD_START:
            return None
    return items


def auto_list(text: str, leadins: Iterable[str] | None = None, max_item_words: int = 5) -> tuple[str, bool]:
    """Format a spoken enumeration as a bulleted list.

    A sentence that contains a lead-in phrase ("I need to get", "the list is", "the following", ...)
    or ends its lead-in with a colon, followed by 3+ short comma/"and"-separated phrases, becomes
    the lead-in sentence ending in ":" plus one "- item" line per phrase. Other sentences are
    left alone. Returns (text, fired).
    """
    phrases = [p for p in (leadins if leadins is not None else DEFAULT_LIST_LEADINS) if p]
    lead_alt = "|".join(_phrase_re(p).pattern for p in phrases)
    if lead_alt:
        lead_re = re.compile(rf"^(?P<lead>(?:.*?(?:{lead_alt}))[,:]?|[^:\n]+:)\s*(?P<body>\S.*)$", re.IGNORECASE | re.DOTALL)
    else:
        lead_re = re.compile(r"^(?P<lead>[^:\n]+:)\s*(?P<body>\S.*)$", re.IGNORECASE | re.DOTALL)
    if _HAS_LIST_LINE.search(text):
        return text, False  # already list-formatted (e.g. by the LLM): never double-format
    # split into sentences, keeping the delimiters as separate pieces
    parts = re.split(r"([.!?]+\s*|\n)", text)
    fired = False
    for i in range(0, len(parts), 2):
        sent = parts[i]
        if not sent.strip() or "\n" in sent or "- " in sent:
            continue
        m = lead_re.match(sent.strip())
        if not m:
            continue
        items = _list_items(m.group("body"), max_item_words)
        if not items:
            continue
        lead = m.group("lead").rstrip(" ,:")
        parts[i] = lead + ":\n" + "\n".join(f"- {it}" for it in items)
        if i + 1 < len(parts) and parts[i + 1] and "\n" not in parts[i + 1]:
            parts[i + 1] = "\n" if (i + 2 < len(parts) and parts[i + 2].strip()) else ""
        fired = True
    return ("".join(parts) if fired else text), fired


# ---------------------------------------------------------------- entry points
def apply_backtrack(text: str, cfg: dict[str, Any] | None) -> tuple[str, int]:
    """The deferred Backtrack pass (LLM fallback): self-corrections, repeat collapse, tidy.

    Used when the LLM was supposed to resolve self-corrections but failed / timed out; the input is
    the rules-cleaned text produced by `clean(..., defer_backtrack=True)`.
    """
    cfg = cfg or {}
    if not text or not cfg.get("backtrack", True):
        return text, 0
    text, n = backtrack(text, cfg.get("backtrack_strong") or ["scratch that"], cfg.get("backtrack_weak") or [])
    if cfg.get("collapse_repeats", True):
        text = collapse_repeats(text)
    return tidy(text), n


def clean(
    text: str, cfg: dict[str, Any] | None = None, *, defer_lists: bool = False, defer_backtrack: bool = False
) -> CleanResult:
    """Apply the rules pass. `cfg` is the `cleanup` section of config.yaml.

    `defer_lists=True` skips the spoken-list formatting (the caller runs `auto_list` itself only
    if the LLM pass, which formats lists on its own, is not used). `defer_backtrack=True` likewise
    leaves self-correction triggers in place for the LLM; the caller runs `apply_backtrack` on the
    result if the LLM is not used.
    """
    cfg = cfg or {}
    text = (text or "").strip()
    if not text:
        return CleanResult("")
    if cfg.get("rules") is False:
        return CleanResult(text)

    text, press_enter = strip_press_enter(text, cfg.get("press_enter_phrases") or ["press enter"])
    if cfg.get("remove_fillers", True):
        text = remove_fillers(text, cfg.get("fillers") or [], cfg.get("filler_phrases") or [])
    if cfg.get("spoken_punctuation", True):
        text = spoken_punctuation(text)
    n_bt = 0
    if cfg.get("backtrack", True) and not defer_backtrack:
        text, n_bt = backtrack(text, cfg.get("backtrack_strong") or ["scratch that"], cfg.get("backtrack_weak") or [])
    if cfg.get("collapse_repeats", True):
        text = collapse_repeats(text)
    text = apply_dictionary(text, cfg.get("dictionary"))
    text, fired = apply_snippets(text, cfg.get("snippets"))
    text = tidy(text)
    list_fired = False
    if cfg.get("auto_lists", True) and not fired and not defer_lists:
        text, list_fired = auto_list(text, cfg.get("list_leadins"))
    return CleanResult(text, press_enter=press_enter, snippet_fired=fired, backtracks=n_bt, list_fired=list_fired)


_CURLY = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u201a": "'", "\u201e": '"'})


def normalize_llm_text(text: str) -> str:
    """Straighten curly quotes/apostrophes (gemma emits them) and collapse 3+ newlines to 2."""
    text = (text or "").translate(_CURLY).replace("\r", "")
    return re.sub(r"\n{3,}", "\n\n", text)


def post_llm(text: str, cfg: dict[str, Any] | None) -> str:
    """Cheap idempotent pass after the LLM: quote/newline normalisation, dictionary again, tidy."""
    cfg = cfg or {}
    return tidy(apply_dictionary(normalize_llm_text(text), cfg.get("dictionary")))
