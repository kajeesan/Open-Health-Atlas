"""Strict parsing for the cited authored submuscle map."""

import re

from ..catalogs import MUSCLE_GROUP_AXES


SUBMAP_CITES = {
    "DIST09": "lit:distefano-2009",   # Distefano 2009 JOSPT — glute EMG
    "BOR11": "lit:boren-2011",        # Boren 2011 IJSPT — glute med/max EMG
    "YOU10": "lit:youdas-2010",       # Youdas 2010 JSCR — pull-up/chin-up EMG
    "CON15": "lit:contreras-2015",    # Contreras 2015 JAB — hip thrust EMG
    "BIOMECH": "biomech",             # consensus biomechanics (generic tier)
}


SUBMAP_NOTE = ("sub-region emphasis is approximate (EMG evidence is fuzzy at "
               "sub-region level) — each row cites its literature key; rows "
               "flagged approx are mechanism-based emphasis; laterality "
               "defaults to bilateral (per-side curation comes later)")



def parse_map(text):
    """docs/authored-submuscle-map.md → [{title, source, uni, rows}]. Strict:
    a malformed data row aborts the import (silent skipping would be data
    loss); italic commentary bullets ('- *…') and prose bullets outside a
    '### ' section are ignored by design. Row format (v2.8 tiered):
    '- <Group> · <sub-region>[ (detail)] · <weight> [E|B][ iso][ (note)]'
    — the [E]/[B] confidence flag is REQUIRED (E = EMG-anchored, B =
    biomechanical estimate); `approx` is derived (B rows are mechanism-based
    emphasis), the legacy '· approx' suffix is refused."""
    sections, cur = [], None
    for line in text.splitlines():
        if line.startswith("### "):
            header = line[4:].strip()
            # Citation separators also use an em dash, but some real Hevy
            # exercise titles contain one themselves ("Quarterly Test —
            # Tibialis Raise"). Preserve the full pre-citation text here;
            # import resolution below first tries it exactly, then removes an
            # optional authored descriptor such as "— conventional".
            title = header.split("[", 1)[0].strip()
            title = re.sub(r"\s+—\s*$", "", title).strip()
            title = re.sub(r"\s*·\s*uni\s*$", "", title).strip()
            keys = re.findall(r"\[([A-Z0-9]+)\]", header)
            if not keys:
                raise SystemExit(f"map section {title!r} has no [CITATION] key")
            bad = [k for k in keys if k not in SUBMAP_CITES]
            if bad:
                raise SystemExit(f"map section {title!r}: unknown citation key(s) "
                         f"{bad} — known: {', '.join(SUBMAP_CITES)}")
            cur = {"title": title,
                   "source": "+".join(SUBMAP_CITES[k] for k in keys),
                   "uni": bool(re.search(r"·\s*uni\b", header)), "rows": []}
            sections.append(cur)
            continue
        if line.startswith("#") or line.startswith("---"):
            cur = None                       # any heading ends the section
            continue
        if cur is None or not line.startswith("- ") or line.startswith("- *"):
            continue
        parts = [p.strip() for p in line[2:].split("·")]
        if len(parts) < 3:
            raise SystemExit(f"malformed map row (need Group · sub-region · weight): {line!r}")
        group, sub_raw = parts[0], parts[1]
        if group not in MUSCLE_GROUP_AXES:
            raise SystemExit(f"map row for {cur['title']!r}: {group!r} is not one of "
                     f"the 7 groups ({', '.join(MUSCLE_GROUP_AXES)})")
        sub = re.sub(r"\s*\([^)]*\)", "", sub_raw).strip().lower()
        # weight cell: '<decimal> [E|B][ iso]' with an optional trailing
        # '(note)' — strip the note, then every token must be recognized
        toks = re.sub(r"\s*\([^)]*\)\s*$", "", parts[2]).split()
        if not toks or not re.fullmatch(r"\d+(\.\d+)?", toks[0]):
            raise SystemExit(f"malformed weight in map row: {line!r}")
        weight = float(toks[0])
        if not 0 < weight <= 1.0:
            raise SystemExit(f"weight out of range (0–1.0] in map row: {line!r}")
        if len(toks) < 2 or toks[1] not in ("[E]", "[B]"):
            raise SystemExit(f"missing/unknown confidence flag in map row: {line!r} "
                     "(every row needs [E] EMG-anchored or [B] biomech estimate)")
        confidence = toks[1][1]
        iso = toks[2:] == ["iso"]
        if toks[2:] and not iso:    # a typo'd flag must not be silently dropped
            raise SystemExit(f"unknown token(s) {toks[2:]} in map row: {line!r} "
                     "(only 'iso' is valid after the confidence flag)")
        if any(r["sub_region"] == sub for r in cur["rows"]):
            raise SystemExit(f"duplicate sub-region {sub!r} for {cur['title']!r}")
        extras = [p for p in parts[3:] if p]
        if extras:                  # incl. the retired '· approx' suffix
            raise SystemExit(f"unknown row flag(s) {extras} in map row: {line!r} "
                     "(approx is derived from [B] since v2.8 — nothing goes "
                     "after the weight cell)")
        cur["rows"].append({"muscle_group": group, "sub_region": sub,
                            "weight": weight, "laterality": "bilateral",
                            "confidence": confidence, "iso": iso,
                            "approx": confidence == "B"})
    empty = [s["title"] for s in sections if not s["rows"]]
    if empty:
        raise SystemExit(f"map section(s) with no rows: {empty}")
    return sections


def resolve_title(map_title, candidates):
    """Prefer an exact configured title before the supported authored aliases."""
    # Preserve semantic em-dash titles such as ``Quarterly Test —
    # Tibialis Raise``.  Only the three authored grip/stance descriptors
    # are aliases rather than part of the canonical exercise name.
    variants = [map_title]
    no_parenthetical = re.sub(r"\s*\([^)]*\)\s*$", "", map_title).strip()
    if no_parenthetical not in variants:
        variants.append(no_parenthetical)
    no_descriptor = re.sub(
        r"\s+—\s*(pronated grip|supinated grip|conventional)\s*$",
        "", map_title, flags=re.IGNORECASE).strip()
    if no_descriptor not in variants:
        variants.append(no_descriptor)
    matched = next((v for v in variants if v in candidates), None)
    title = matched or no_descriptor
    return matched, title
