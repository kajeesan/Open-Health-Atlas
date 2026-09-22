"""Strict parsing for cited laboratory catalog configuration."""

import re


LAB_CITES = {
    # Explicitly non-clinical source used only by bundled fictional fixtures.
    "TEST": "synthetic-test-fixture",
}


LAB_CATALOG_NOTE = ("reference intervals come from the configured catalog; "
                    "plausibility bounds are WIDE 'physically "
                    "possible' safety limits (catch a dropped-comma OCR error), "
                    "NOT clinical-normal — a real abnormal value still flows, "
                    "flagged out-of-range in the view")



def _parse_ref_spec(spec, line):
    """'ref 8.3-10.7' | 'ref <3.0' | 'ref >50' | 'ref none' → (low, high).
    Open-ended intervals leave one side null."""
    body = spec[len("ref"):].strip()
    if body == "none":
        return (None, None)
    if body.startswith("<"):
        return (None, _lab_num(body[1:], line))
    if body.startswith(">"):
        return (_lab_num(body[1:], line), None)
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", body)
    if not m:
        raise SystemExit(f"malformed reference interval {body!r} in: {line!r}")
    return (float(m.group(1)), float(m.group(2)))


def _lab_num(s, line):
    try:
        return float(s.strip())
    except ValueError:
        raise SystemExit(f"expected a number, got {s!r} in: {line!r}")


def parse_catalog(text):
    """Parse the configured laboratory catalog into row dictionaries. Strict grammar (a malformed row aborts
    — silent skipping is data loss). Rows live under a '### <Panel>' header:
      '- <canonical> · <display> · <unit> · ref <spec> · plaus <lo>-<hi> ·
       delta <spec> · [CITE][CITE…][ · aliases: a|b]'
    delta <spec> = '<val>' (abs) | '<val>f' (frac) | 'none'."""
    out_rows, panel, seen = [], None, set()
    for line in text.splitlines():
        if line.startswith("### "):
            panel = line[4:].strip()
            continue
        if line.startswith("#") or line.startswith("---"):
            panel = None
            continue
        if not line.startswith("- ") or line.startswith("- *"):
            continue
        if panel is None:
            raise SystemExit(f"catalog row outside a '### <Panel>' section: {line!r}")
        parts = [p.strip() for p in line[2:].split("·")]
        if len(parts) < 7:
            raise SystemExit(f"malformed catalog row (need canonical · display · unit "
                     f"· ref · plaus · delta · [CITE]): {line!r}")
        canonical, display, unit, ref_s, plaus_s, delta_s, cite_s = parts[:7]
        if not canonical or not unit:
            raise SystemExit(f"catalog row needs a canonical name and unit: {line!r}")
        if canonical in seen:
            raise SystemExit(f"duplicate canonical test {canonical!r}")
        seen.add(canonical)
        if not ref_s.startswith("ref "):
            raise SystemExit(f"expected 'ref …' field in: {line!r}")
        ref_low, ref_high = _parse_ref_spec(ref_s, line)
        if not plaus_s.startswith("plaus "):
            raise SystemExit(f"expected 'plaus <lo>-<hi>' field in: {line!r}")
        pm = re.fullmatch(r"plaus\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", plaus_s)
        if not pm:
            raise SystemExit(f"malformed plaus bounds in: {line!r}")
        plaus_low, plaus_high = float(pm.group(1)), float(pm.group(2))
        if not plaus_low < plaus_high:
            raise SystemExit(f"plaus low must be < high in: {line!r}")
        if not delta_s.startswith("delta "):
            raise SystemExit(f"expected 'delta …' field in: {line!r}")
        dbody = delta_s[len("delta"):].strip()
        if dbody == "none":
            max_delta, delta_kind = None, "abs"
        elif dbody.endswith("f"):
            max_delta, delta_kind = _lab_num(dbody[:-1], line), "frac"
        else:
            max_delta, delta_kind = _lab_num(dbody, line), "abs"
        keys = re.findall(r"\[([A-Z]+)\]", cite_s)
        if not keys:
            raise SystemExit(f"catalog row needs at least one [CITE] key: {line!r}")
        bad = [k for k in keys if k not in LAB_CITES]
        if bad:
            raise SystemExit(f"unknown citation key(s) {bad} in {line!r} — "
                     f"known: {', '.join(LAB_CITES)}")
        source = "+".join(LAB_CITES[k] for k in keys)
        aliases = []
        for extra in parts[7:]:
            if extra.startswith("aliases:"):
                # Pipes separate aliases so commas and punctuation remain data.
                aliases = [a.strip() for a in extra[len("aliases:"):].split("|")
                           if a.strip()]
            elif extra:
                raise SystemExit(f"unknown trailing field {extra!r} in: {line!r}")
        out_rows.append({"canonical": canonical, "display": display,
                         "panel": panel, "unit": unit,
                         "ref_low": ref_low, "ref_high": ref_high,
                         "plaus_low": plaus_low, "plaus_high": plaus_high,
                         "max_delta": max_delta, "delta_kind": delta_kind,
                         "aliases": aliases, "source": source})
    if not out_rows:
        raise SystemExit("catalog has no rows")
    return out_rows
