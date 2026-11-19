"""Refresh the 2028 state election report from public election indexes."""
import csv
import html
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent / "report"
BASE = "https://ballotpedia.org/"
SOURCES = {
    "Governor": ("Gubernatorial_elections,_2028", "gubernatorial offices on the ballot"),
    "Legislature": ("State_legislative_elections,_2028", "regular legislative elections"),
    "Executive": ("State_executive_official_elections,_2028", "State executive offices up for election"),
    "Supreme court": ("State_supreme_court_elections,_2028", "seats on state supreme courts"),
    "Ballot measure": ("2028_ballot_measures", "statewide ballot measures were certified"),
}
FIELDS = ["Category", "State", "Contest or measure", "Date", "Status", "Details", "Source"]


def clean(node):
    return " ".join(node.get_text(" ", strip=True).split())


def record(category, state, contest, url, *, date="", status="", details=""):
    return dict(zip(FIELDS, [category, state, contest, date, status, details, url]))


def parse(category, page, pattern, url):
    body = BeautifulSoup(page, "html.parser").select_one("#mw-content-text")
    if body is None:
        raise ValueError("Article body missing")
    for ref in body.select("sup.reference"):
        ref.decompose()
    match = next((p for p in body.find_all("p") if pattern in clean(p)), None)
    if match is None:
        raise ValueError("Expected cycle summary missing")
    summary = clean(match)
    rows = []
    if category == "Governor":
        for link in match.find_all("a"):
            state = clean(link)
            if state and state != "gubernatorial offices":
                rows.append(record(category, state, "Governor", urljoin(url, link["href"]),
                                   date="November 7, 2028", status="Regular election"))
    if category in {"Supreme court", "Ballot measure", "Executive"}:
        for table in body.find_all("table"):
            table_rows = table.find_all("tr")
            if not table_rows:
                continue
            headers = [clean(cell).lower() for cell in table_rows[0].find_all(["th", "td"], recursive=False)]
            for tr in table_rows[1:]:
                cells = tr.find_all(["th", "td"], recursive=False)
                values = [clean(cell) for cell in cells]
                if len(values) != len(headers):
                    continue
                if category == "Supreme court" and headers == ["state", "seats up for election", "election dates", "election method"]:
                    state, seats, date, method = values
                    if state not in {"Guam", "Northern Mariana Islands", "Puerto Rico", "U.S. Virgin Islands", "American Samoa"}:
                        rows.append(record(category, state, "State supreme court", url,
                                           date=date, details=f"{seats} seat(s) · {method}"))
                if category == "Ballot measure" and headers == ["type", "title", "subject", "description", "result"]:
                    state = clean(table.find_previous("h3"))
                    preceding = table.find_previous(["p", "h3"])
                    date = clean(preceding) + ", 2028" if preceding.name == "p" else ""
                    _, title, subject, description, result = values
                    link = cells[1].find("a", href=True)
                    rows.append(record(category, state, title, urljoin(url, link["href"]) if link else url,
                                       date=date, status=result or "Qualified for ballot", details=f"{subject} · {description}"))
                if category == "Executive" and headers == ["candidate", "party", "office", "status"]:
                    name, party, office, status = values
                    caption = table.find("caption")
                    if caption is None:
                        raise ValueError("Candidate table caption missing")
                    label = clean(caption)
                    pane = table.find_parent(class_="tab-pane")
                    state = next(
                        (clean(link) for link in body.find_all("a", href="#" + pane["id"])
                         if label.startswith(clean(link) + " ")),
                        "",
                    )
                    if not state:
                        raise ValueError(f"Unrecognized candidate table: {label}")
                    if state in {"Guam", "Northern Mariana Islands", "Puerto Rico", "U.S. Virgin Islands", "American Samoa"}:
                        continue
                    rows.append(record(category, state, office, url, status=status, details=f"{name} · {party}"))
    if category in {"Governor", "Supreme court", "Ballot measure"} and not rows:
        raise ValueError("Expected contest rows missing")
    return {"summary": summary, "rows": rows, "source": url}


def write(name, value):
    path = ROOT / name
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def csv_value(value):
    # Prevent office or candidate names from becoming spreadsheet formulas.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def render(data, failures, checked):
    esc = html.escape
    rows = [row for entry in data.values() for row in entry["rows"]]
    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(FIELDS)
    writer.writerows([csv_value(row[key]) for key in FIELDS] for row in rows)
    write("contests.csv", "\ufeff" + csv_file.getvalue())
    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(["Category", "Summary", "Last successful refresh UTC", "Refresh status", "Source"])
    writer.writerows([category, entry["summary"], entry["updated"], failures.get(category, "OK"), entry["source"]]
                     for category, entry in data.items())
    write("overview.csv", "\ufeff" + csv_file.getvalue())
    notes = "".join(f'<li>{esc(name)}: {esc(reason)}</li>' for name, reason in failures.items())
    cards = "".join(
        f'<section><h2>{esc(name)}</h2><p>{esc(entry["summary"])}</p>'
        f'<small>Last successful check {esc(entry["updated"])} · '
        f'<a href="{esc(entry["source"])}">Source and detailed coverage</a></small></section>'
        for name, entry in data.items()
    )
    table = "".join(
        "<tr>" + "".join(f"<td>{esc(row[key])}</td>" for key in FIELDS[:-1])
        + f'<td><a href="{esc(row["Source"])}">Source</a></td></tr>' for row in rows
    )
    write("index.html", f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>2028 US State Elections</title>
<style>
body{{font:1rem/1.55 system-ui,sans-serif;background:#f5f2eb;color:#182737;margin:0}}
main{{max-width:1100px;margin:auto;padding:clamp(1rem,4vw,3rem)}}
h1{{font:700 clamp(2.4rem,6vw,4.5rem)/1 Georgia,serif;margin:.4em 0}}
h2{{font:700 1.45rem Georgia,serif;margin:0}}
a{{color:#14554d}} section{{background:#fff;padding:1.2rem;border-top:3px solid #39756d}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:1rem;margin:2rem 0}}
label{{display:block;margin-bottom:.5rem}} input{{font:inherit;padding:.45rem;width:min(90%,24rem)}}
.scroll{{overflow:auto;margin-top:1rem}} table{{background:#fff;border-collapse:collapse;width:100%}}
th,td{{text-align:left;vertical-align:top;padding:.65rem;border-bottom:1px solid #d8dedf}}
th{{background:#213f48;color:#fff}} small{{color:#405564}}
</style>
<main><h1>2028 US State Elections</h1>
<p>General election day is November 7, 2028. This report covers elections for state office and statewide ballot measures across the 50 states.</p>
<p>Last refresh attempt <time>{esc(checked)} UTC</time> ·
<a href="overview.csv" download>Download overview CSV</a> · <a href="contests.csv" download>Download contest CSV</a></p>
{f"<h2>Sources needing attention</h2><ul>{notes}</ul>" if notes else ""}
<p>Use state election offices for official calendars, candidate filings and certified results.
<a href="https://www.ncsl.org/elections-and-campaigns/state-profiles-elections">Find state election officials</a>.</p>
<div class="grid">{cards}</div>
<h2>Contest and candidate records</h2>
<p>Includes governor races, state supreme court contests, qualified measures, and candidate records from the state executive index. For legislative districts and special elections, use the Legislature source above.</p>
<label for="filter">Filter by state, category, contest or keyword</label><input id="filter" type="search">
<div class="scroll"><table><thead><tr>{"".join(f"<th scope='col'>{key}</th>" for key in FIELDS)}</tr></thead>
<tbody>{table}</tbody></table></div>
<script>
const filter = document.querySelector('#filter');
const rows = [...document.querySelectorAll('tbody tr')];
filter.addEventListener('input', () => {{
  const query = filter.value.trim().toLowerCase();
  for (const row of rows) row.hidden = !row.textContent.toLowerCase().includes(query);
}});
</script></main></html>""")


def main():
    ROOT.mkdir(exist_ok=True)
    cache = ROOT / "snapshot.json"
    data = json.loads(cache.read_text()) if cache.exists() else {}
    checked = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    failures = {}
    for category, (slug, pattern) in SOURCES.items():
        url = BASE + slug
        try:
            request = Request(url, headers={"User-Agent": "2028-state-election-report/1.0"})
            with urlopen(request, timeout=25) as response:
                entry = parse(category, response.read(), pattern, url)
            entry["updated"] = checked
            data[category] = entry
        except (OSError, ValueError, AttributeError, KeyError) as error:
            print(f"{category}: {error}")
            failures[category] = "Showing last successful data" if category in data else "Source unavailable"
    if not data:
        raise SystemExit("No source data available")
    write("snapshot.json", json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    render(data, failures, checked)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
