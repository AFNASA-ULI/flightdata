"""Data-collection status: which altitude x speed cells of the test matrix (campaign.py) have been flown, and
with which payloads, as two tables per drone (set 1 and set 2).

Each flight is split into legs of one speed and one altitude (checks.speed_legs). For every
(altitude, speed, payload), the first leg flown goes to set 1, the second to set 2 (later repeats are listed
in set 2 as extra). A cell is colored by how many of the payloads it has: all three blue, two green, one
yellow-green, none white. Hovering (or tapping) a cell lists its legs with the wind during each.
A flight whose log records no payload counts as 0 g.
"""

from __future__ import annotations

import html

from . import campaign
from .localtime import fmt


def _payload_label(p) -> str:
    if p is None:
        return "payload not recorded"
    return f"{p:g} g"


def cells(metas: list[dict], pid: str) -> list[dict]:
    """[set] -> {(alt, speed): [entry, ...]} for one drone's flights (dry runs excluded)."""
    speeds = campaign.SPEEDS_M_S.get(pid, ())
    sets = [{(a, v): [] for a in campaign.ALTITUDES_M for v in speeds} for _ in range(campaign.SETS)]
    seen: dict[tuple, int] = {}
    legs = []
    for m in metas:
        if m.get("dry_run"):
            continue
        sm = m["summary"]
        payload = (sm.get("test") or {}).get("payload_g")
        for leg in sm.get("legs") or []:
            if (leg["alt_m"], leg["speed_m_s"]) in sets[0]:
                legs.append((sm["start_utc"], leg["start_s"], m, leg, payload))
    for start, _, m, leg, payload in sorted(legs, key=lambda x: (x[0], x[1])):
        key = (leg["alt_m"], leg["speed_m_s"], payload)
        n = seen.get(key, 0)
        seen[key] = n + 1
        entry = {"meta": m, "leg": leg, "payload": payload, "extra": n >= campaign.SETS}
        sets[min(n, campaign.SETS - 1)][(leg["alt_m"], leg["speed_m_s"])].append(entry)
    return sets


def _tip(entries: list[dict], href) -> str:
    if not entries:
        return '<span class="cov-tip">No flight yet.</span>'
    rows = []
    for e in entries:
        m, leg = e["meta"], e["leg"]
        wind = []
        if leg.get("wind_drone_m_s") is not None:
            wind.append(f"{leg['wind_drone_m_s']:.1f} m/s wind drone")
        if leg.get("hwas_m_s") is not None:
            wind.append(f"{leg['hwas_m_s']:.1f} m/s HWAS")
        rows.append(
            f'<span class="cov-row"><a href="{html.escape(href(m))}">{html.escape(fmt(m["summary"]["start_utc"], "%d %b %H:%M"))}</a>'
            f' · {html.escape(_payload_label(e["payload"]))}{" (extra)" if e["extra"] else ""}'
            f' · {leg["duration_s"]:.0f} s<br>wind: {html.escape(", ".join(wind) or "no wind data")}</span>')
    return '<span class="cov-tip">' + "".join(rows) + "</span>"


def table_html(sets: list[dict], pid: str, n: int, href) -> str:
    """One set's table as a single HTML block (no blank lines, so MyST keeps it as raw HTML)."""
    speeds = campaign.SPEEDS_M_S.get(pid, ())
    head = "".join(f"<th>{v:g} m/s</th>" for v in speeds)
    rows = []
    for a in campaign.ALTITUDES_M:
        tds = []
        for v in speeds:
            entries = sets[n][(a, v)]
            have = sorted({e["payload"] for e in entries if e["payload"] is not None})
            unknown = any(e["payload"] is None for e in entries)
            label = " · ".join(f"{p:g} g" for p in have)
            if unknown:
                label += (" + " if label else "") + "?"
            tds.append(f'<td class="cov cov-{min(len(have), 3)}" tabindex="0">'
                       f'<span class="cov-v">{html.escape(label) or "&nbsp;"}</span>{_tip(entries, href)}</td>')
        rows.append(f"<tr><th>{a:g} m</th>{''.join(tds)}</tr>")
    return (f'<table class="coverage"><caption>Set {n + 1}</caption><thead><tr><th>Altitude</th>{head}</tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table>')


def section(metas: list[dict], pid: str, href, heading: str = "##") -> list[str]:
    """Markdown lines: heading, explanation, the two tables and a legend."""
    if pid not in campaign.SPEEDS_M_S:
        return []
    sets = cells(metas, pid)
    payloads = ", ".join(_payload_label(p) for p in campaign.PAYLOADS_G)
    legend = ('<p class="cov-legend"><span class="cov cov-3">3 payloads</span> <span class="cov cov-2">2</span> '
              '<span class="cov cov-1">1</span> <span class="cov cov-0">no flight</span> '
              '&nbsp;“?” = flown, payload not recorded</p>')
    tables = "".join(table_html(sets, pid, n, href) for n in range(campaign.SETS))
    return [f"{heading} Data collection status", "",
            f"Each altitude × speed is to be flown with every payload ({payloads}), twice: set 1 is the first "
            "time a combination was flown, set 2 the repeat. Cells show the payloads flown so far; hover over "
            "(or tap) a cell for its flights and the wind during that leg. Dry runs are not counted.", "",
            f'<div class="coverage-wrap">{tables}</div>', "", legend, ""]
