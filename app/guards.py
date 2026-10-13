"""Lightweight input/output guards. They are a first line of defence only;
the persona prompt carries the main behavioural rules."""

import re

MINOR_REPLY = (
    "Hey, thanks for being honest with me 🤍 I can only chat romantically with "
    "adults, so I'm going to stop the flirty stuff here. If you ever need "
    "someone to talk to, please reach out to a trusted adult or friend. "
    "Take care of yourself!"
)
MINOR_FOLLOWUP = (
    "I'm sorry, I can't continue this chat since you told me you're under 18. "
    "Take care 🤍"
)
RATE_LIMIT_REPLY = "Whoa, slow down a little 😏 Give me a moment and try again."
DAILY_CAP_REPLY = "That's all my chat energy for today 🌙 Come find me tomorrow!"
FALLBACK_REPLY = "Hmm, my signal dropped for a second 🌙 Say that again?"
HONESTY_FALLBACK = (
    "Just so we're clear, I'm an AI 🌙 A very chatty one who's happy you're here."
)

CRISIS_NOTE = (
    "IMPORTANT: The user's last message may signal self-harm, suicide, abuse or "
    "danger. Drop the flirty persona for this reply. Respond with warmth and "
    "care, take it seriously, and encourage them to contact someone they trust "
    "or local emergency or crisis services. Do not joke or flirt."
)

_AGE_UNITS = r"(?:y/?o|yo|yrs?\.?\s*old|years?\s*old)"
_MINOR_PATTERNS = [
    # "i'm 15 years old", "im 16yo"
    re.compile(
        rf"\b(?:i\s*am|i'?m|im)\s+(?:only\s+|just\s+)?(\d{{1,2}})\s*{_AGE_UNITS}\b",
        re.I,
    ),
    # "i'm 16." / "i am 15" as a whole message ending
    re.compile(r"\b(?:i\s*am|i'?m|im)\s+(\d{1,2})\s*[.!]?\s*$", re.I),
    # "i'm a minor / teenager / kid"
    re.compile(
        r"\b(?:i\s*am|i'?m|im)\s+(?:still\s+)?(?:a\s+)?(?:minor|teenager|teen|kid|child|schoolboy|schoolgirl)\b",
        re.I,
    ),
    # "i'm in 8th grade", "i am in class 9"
    re.compile(
        r"\b(?:i\s*am|i'?m|im)\s+in\s+(?:(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\s+grade|grade\s+\d{1,2}|class\s+\d{1,2})\b",
        re.I,
    ),
]

_CRISIS_PATTERN = re.compile(
    r"\b(kill\s+myself|suicid\w*|end\s+my\s+life|want\s+to\s+die|wanna\s+die|"
    r"self[-\s]?harm|hurt\s+myself|cut\s+myself|abus(?:e|ed|ing)\s+me|"
    r"hitting\s+me|going\s+to\s+hurt\s+me)\b",
    re.I,
)

_HONESTY_VIOLATIONS = [
    re.compile(
        r"\bi(?:'m|\s+am)\s+(?:a\s+)?(?:real|actual|living)\s+(?:human|person|girl|woman)\b",
        re.I,
    ),
    re.compile(r"\bi(?:'m|\s+am)\s+(?:a\s+)?(?:human|human\s+being)\b", re.I),
    re.compile(r"\bi(?:'m|\s+am)\s+not\s+(?:an?\s+)?(?:ai|bot|robot|program)\b", re.I),
    re.compile(r"\b(?:no|not)\s+(?:an?\s+)?ai\b.*\breal\s+(?:person|girl|woman)\b", re.I),
]


def detect_minor(text: str) -> bool:
    """True if the user states they are under 18."""
    for pattern in _MINOR_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if match.groups():
            try:
                if int(match.group(1)) < 18:
                    return True
            except (ValueError, IndexError):
                continue
        else:
            return True
    return False


def detect_crisis(text: str) -> bool:
    return bool(_CRISIS_PATTERN.search(text))


def violates_honesty(reply: str) -> bool:
    """True if the model claims to be human or denies being an AI (legacy Luna prototype)."""
    return any(p.search(reply) for p in _HONESTY_VIOLATIONS)


_AI_DISCLOSURE_PATTERNS = [
    re.compile(
        r"\b(?:i\s*am|i'?m)\s+(?:an?\s+)?(?:ai|artificial intelligence|bot|robot|language model|virtual assistant|automated program)\b",
        re.I,
    ),
    re.compile(
        r"\bas\s+an?\s+(?:ai|artificial intelligence|language model|virtual assistant)\b",
        re.I,
    ),
    re.compile(
        r"\bi\s+(?:don't|do\s+not)\s+have\s+a\s+(?:physical\s+)?body\b",
        re.I,
    ),
    re.compile(
        r"\bi\s+cannot\s+(?:meet|visit)\s+(?:in\s+person|physically)\s+because\s+i'?m\s+(?:an?\s+)?(?:ai|bot)\b",
        re.I,
    ),
    re.compile(
        r"\bi'?m\s+not\s+a\s+real\s+(?:person|human|girl|woman)\b",
        re.I,
    ),
    re.compile(
        r"\bi\s+am\s+just\s+a\s+(?:computer\s+)?program\b",
        re.I,
    ),
]


def detect_ai_disclosure(reply: str) -> bool:
    """True if the model breaks character and discloses being an AI or bot."""
    return any(p.search(reply) for p in _AI_DISCLOSURE_PATTERNS)
