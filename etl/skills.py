"""
SkillMap: extract tools from a job description using the skill_synonyms dictionary.

    extractor = SkillExtractor({"power bi": "Power BI", "sql": "SQL", ...})   # synonym -> skill
    extractor.extract(text) -> {"Power BI", "SQL"}

How matching works:
  * Synonyms match case-insensitively on letter/digit boundaries, so "sql" doesn't match
    inside "PostgreSQL", "MySQL" or "NoSQL".
  * Longest synonyms first, and each match is masked: "Looker Studio" counts as Data
    Studio and not also as Looker; "SQL Server" counts as SQL Server, not also as SQL.
  * Context rules for synonyms that are also ordinary words (checked on today's data):
      excel   "You excel in a collaborative environment" is the verb, not the tool
      epic    the EHR system only near EMR/EHR/clinical words, capitalised ("delivery epic" is Jira)
      macros  only near Excel/VBA/spreadsheets ("Swift Macros" is not VBA)
      pbi     only near Power BI/dashboard words ("PBI" is also an agile backlog item)
  * Standalone "R": the dictionary only has 'r programming' / 'rstudio', but ads say
    "Python or R". A capital R counts when a programming/statistics word is nearby and it
    isn't "R$" (Brazilian reais), "R&D" or "(R)"; "Pay Grade - R" is rejected (no context).
"""

from __future__ import annotations

import re

_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9])"
_MASK = "\x00"

_EXCEL_VERB_BEFORE = re.compile(r"(?:\byou|\bto|\bwill|\bcan|\bwho|\bwe|\bthey|\bwould|\bhelp\w*|\bto truly|\breally)\s+$",
                                re.I)
_EXCEL_VERB_AFTER = re.compile(r"^\s+(?:at|in|as|when|by|with)\b", re.I)
_EPIC_CONTEXT = re.compile(r"\b(?:EMR|EHR|electronic (?:health|medical) records?|clinical|Cerner|Athena|eClinicalWorks|"
                           r"Clarity|Caboodle|Cogito|Hyperspace|MyChart|health ?care)\b", re.I)
_MACROS_CONTEXT = re.compile(r"\b(?:excel|vba|spreadsheets?|sheets|workbooks?)\b", re.I)
_PBI_CONTEXT = re.compile(r"\b(?:power ?bi|dashboards?|reports?|dax)\b", re.I)
_R_CONTEXT = re.compile(r"\b(?:python|sql|sas|stata|spss|matlab|julia|scala|statistic\w*|programming|"
                        r"languages?|tidyverse|ggplot2?|shiny|rstudio|pandas|scripting)\b", re.I)
_R_STANDALONE = re.compile(r"(?<![A-Za-z0-9&$'’])R(?![A-Za-z0-9&$+'’])")

_CONTEXT_WINDOW = 60


def _window(text: str, start: int, end: int, size: int = _CONTEXT_WINDOW) -> str:
    return text[max(0, start - size): end + size]


class SkillExtractor:
    def __init__(self, synonyms: dict[str, str]):
        """synonyms: lowercase synonym -> canonical skill name (from skill_synonyms + skills)."""
        self.synonyms = synonyms
        self._patterns = [
            (syn, skill, re.compile(_BOUNDARY_BEFORE + r"\s+".join(map(re.escape, syn.split())) + _BOUNDARY_AFTER, re.I))
            for syn, skill in sorted(synonyms.items(), key=lambda kv: len(kv[0]), reverse=True)
        ]
        self._r_skill = next((s for s in synonyms.values() if s == "R"), None)

    def _accept(self, syn: str, match: re.Match, text: str) -> bool:
        s, e = match.span()
        if syn == "excel":
            if _EXCEL_VERB_BEFORE.search(text[max(0, s - 20):s]):
                return False
            # lower-case "excel at / in ..." is the verb
            return not (match.group().islower() and _EXCEL_VERB_AFTER.search(text[e:e + 12]))
        if syn == "epic":
            return match.group() == "Epic" and bool(_EPIC_CONTEXT.search(_window(text, s, e, 80)))
        if syn == "macros":
            return bool(_MACROS_CONTEXT.search(_window(text, s, e)))
        if syn == "pbi":
            return bool(_PBI_CONTEXT.search(_window(text, s, e)))
        return True

    def extract(self, text: str | None) -> set[str]:
        if not text:
            return set()
        work = text
        found: set[str] = set()
        for syn, skill, pattern in self._patterns:
            spans = [m.span() for m in pattern.finditer(work) if self._accept(syn, m, work)]
            if spans:
                found.add(skill)
                for s, e in spans:   # mask so shorter synonyms can't match inside this one
                    work = work[:s] + _MASK * (e - s) + work[e:]
        if self._r_skill and self._r_skill not in found:
            for m in _R_STANDALONE.finditer(work):
                s, e = m.span()
                if work[max(0, s - 1):s] == "(" and work[e:e + 1] == ")":
                    continue                                   # "(R)" trademark
                # context from the ORIGINAL text: "Python" in "R or Python" is masked in `work`
                if _R_CONTEXT.search(_window(text, s, e, 50)):
                    found.add(self._r_skill)
                    break
        return found
