#!/usr/bin/env python3
"""Build a self-contained HTML summary of a VALIS registration run.

Standard library only, so it runs in the VALIS image with no extra dependency.
Thumbnails are embedded as base64 data URIs so the report is a single file that
survives being downloaded from Seqera Platform or emailed.
"""
import argparse
import base64
import csv
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# VALIS writes one PNG per stage, prefixed with the set name.
STAGES = [
    ("original_overlap", "Before registration"),
    ("rigid_overlap", "After rigid"),
    ("non_rigid_overlap", "After non-rigid"),
    ("micro_reg", "After micro-registration"),
]

METRIC_COLUMNS = [
    ("slide", "Slide"),
    ("original_D", "Original D"),
    ("rigid_D", "Rigid D"),
    ("non_rigid_D", "Non-rigid D"),
    ("original_rTRE", "Original rTRE"),
    ("rigid_rTRE", "Rigid rTRE"),
    ("non_rigid_rTRE", "Non-rigid rTRE"),
]

CSS = """
:root {
  --bg: #ffffff; --fg: #1b1f24; --muted: #5a6472; --line: #e3e7ec;
  --card: #f7f9fb; --accent: #2f6f4f; --warn: #a15c00; --bad: #a12d2d;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14171a; --fg: #e8eaed; --muted: #9aa4b2; --line: #2a2f36;
    --card: #1b1f24; --accent: #7fd1a6; --warn: #e0a458; --bad: #e08585;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0 16px 64px; background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width: 1040px; margin: 0 auto; }
header { padding: 40px 0 24px; border-bottom: 1px solid var(--line); }
h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 14px; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 28px; margin-top: 16px; font-size: 13px; color: var(--muted); }
.meta b { color: var(--fg); font-weight: 600; }
section { margin: 36px 0 0; padding: 20px; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; }
h2 { margin: 0 0 4px; font-size: 19px; }
.ref { color: var(--muted); font-size: 13px; margin-bottom: 14px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 16px; }
.tag { font-size: 12px; padding: 2px 9px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted); background: var(--bg); }
.tag.on { color: var(--accent); border-color: currentColor; }
.tag.flip { color: var(--warn); border-color: currentColor; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 4px; font-size: 13px; }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.04em; }
td.better { color: var(--accent); }
td.worse { color: var(--bad); }
.shots { display: grid; gap: 14px; margin-top: 18px;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
figure { margin: 0; }
figure img { width: 100%; border-radius: 6px; border: 1px solid var(--line); display: block; }
figcaption { font-size: 12px; color: var(--muted); margin-top: 6px; }
.legend { font-size: 12.5px; color: var(--muted); margin-top: 14px; padding-top: 12px;
  border-top: 1px dashed var(--line); }
.empty { color: var(--muted); font-style: italic; }
footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line);
  font-size: 12.5px; color: var(--muted); }
@media (max-width: 560px) { .shots { grid-template-columns: 1fr; } }
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--summaries", required=True, help="Directory of per-set *_summary.json")
    p.add_argument("--overlaps", required=True, help="Directory of overlap PNGs")
    p.add_argument("--run-name", default="")
    p.add_argument("--revision", default="")
    p.add_argument("--out-html", default="summary_report.html")
    p.add_argument("--out-csv", default="registration_qc.csv")
    return p.parse_args(argv)


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value):
    n = num(value)
    return "-" if n is None else f"{n:.3f}" if abs(n) < 1000 else f"{n:.1f}"


def data_uri(path):
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def find_shots(overlap_dir, set_id):
    """Match this set's PNGs. VALIS prefixes each file with the set name."""
    found = []
    for suffix, label in STAGES:
        hit = next((p for p in sorted(overlap_dir.rglob(f"*{suffix}.png"))
                    if p.name.startswith(f"{set_id}_")), None)
        if hit:
            found.append((label, data_uri(hit)))
    return found


def metric_cell(row, key):
    """Colour rigid/non-rigid error against the original, when comparable."""
    value = row.get(key)
    cls = ""
    base = num(row.get("original_D" if key.endswith("_D") else "original_rTRE"))
    cur = num(value)
    if base is not None and cur is not None and key.startswith(("rigid", "non_rigid")):
        if cur < base * 0.95:
            cls = " class='better'"
        elif cur > base * 1.05:
            cls = " class='worse'"
    return f"<td{cls}>{html.escape(fmt(value))}</td>"


def render_set(summary, overlap_dir):
    set_id = str(summary.get("set_id", "?"))
    settings = summary.get("settings", {})
    reflections = summary.get("reflections", {}) or {}
    flipped = [k for k, v in reflections.items() if v]

    tags = []
    if settings.get("check_for_reflections"):
        tags.append("<span class='tag on'>reflection search on</span>")
    if settings.get("micro_reg"):
        tags.append("<span class='tag on'>micro-registration</span>")
    tags.append(f"<span class='tag'>crop: {html.escape(str(settings.get('crop', '-')))}</span>")
    tags.append(f"<span class='tag'>{html.escape(str(settings.get('non_rigid_registrar', '-')))}</span>")
    if summary.get("merged"):
        tags.append("<span class='tag'>merged OME-TIFF</span>")
    for name in flipped:
        tags.append(f"<span class='tag flip'>mirrored: {html.escape(name)}</span>")

    rows = summary.get("metrics") or []
    if rows:
        head = "".join(f"<th>{html.escape(lbl)}</th>" for _, lbl in METRIC_COLUMNS)
        body = ""
        for row in rows:
            cells = [f"<td>{html.escape(str(row.get('slide', '-')))}</td>"]
            cells += [metric_cell(row, key) for key, _ in METRIC_COLUMNS[1:]]
            body += "<tr>" + "".join(cells) + "</tr>"
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    else:
        table = "<p class='empty'>No pairwise error metrics were produced.</p>"

    shots = find_shots(overlap_dir, set_id)
    figs = "".join(
        f"<figure><img alt='{html.escape(label)}' src='{uri}'>"
        f"<figcaption>{html.escape(label)}</figcaption></figure>"
        for label, uri in shots
    )
    gallery = f"<div class='shots'>{figs}</div>" if figs else ""

    legend = ("<p class='legend'>In the overlays the reference is magenta and the moving slide is "
              "green; where they coincide the tissue reads grey or white. Lower D and rTRE are "
              "better. When a reflection was applied, VALIS measures the original and rigid error "
              "from the same corrected keypoints, so those two columns can be identical — judge "
              "those sets from the overlays.</p>") if figs else ""

    return f"""
    <section>
      <h2>{html.escape(set_id)}</h2>
      <div class="ref">{summary.get('n_images', '?')} images &middot;
        reference <b>{html.escape(str(summary.get('reference', '?')))}</b></div>
      <div class="tags">{''.join(tags)}</div>
      {table}
      {gallery}
      {legend}
    </section>"""


def main(argv=None):
    args = parse_args(argv)
    summary_dir, overlap_dir = Path(args.summaries), Path(args.overlaps)

    files = sorted(summary_dir.rglob("*_summary.json"))
    if not files:
        print(f"ERROR: no *_summary.json found under {summary_dir}", file=sys.stderr)
        return 1

    summaries = []
    for f in files:
        try:
            summaries.append(json.loads(f.read_text()))
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: skipping unreadable {f.name}: {exc}", file=sys.stderr)
    if not summaries:
        print("ERROR: no readable summaries", file=sys.stderr)
        return 1
    summaries.sort(key=lambda s: str(s.get("set_id", "")))

    # Flat CSV of every pairwise metric, for downstream analysis.
    with open(args.out_csv, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["set", "reference", "slide", "mirrored"]
                        + [k for k, _ in METRIC_COLUMNS[1:]])
        for s in summaries:
            reflections = s.get("reflections", {}) or {}
            for row in s.get("metrics") or []:
                slide = row.get("slide", "")
                writer.writerow([s.get("set_id", ""), s.get("reference", ""), slide,
                                 reflections.get(slide, "")]
                                + [row.get(k, "") for k, _ in METRIC_COLUMNS[1:]])

    n_sets = len(summaries)
    n_images = sum(int(s.get("n_images") or 0) for s in summaries)
    n_flipped = sum(1 for s in summaries for v in (s.get("reflections") or {}).values() if v)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = "".join(render_set(s, overlap_dir) for s in summaries)
    doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VALIS Registration Summary</title>
<style>{CSS}</style>
</head><body><div class="wrap">
<header>
  <h1>VALIS registration summary</h1>
  <div class="sub">Slides warped to the coordinate frame of each set's reference image.</div>
  <div class="meta">
    <span><b>{n_sets}</b> set{'s' if n_sets != 1 else ''}</span>
    <span><b>{n_images}</b> images</span>
    <span><b>{n_flipped}</b> mirrored</span>
    {f'<span>run <b>{html.escape(args.run_name)}</b></span>' if args.run_name else ''}
    {f'<span>pipeline <b>{html.escape(args.revision)}</b></span>' if args.revision else ''}
    <span>{generated}</span>
  </div>
</header>
{body}
<footer>Generated by <b>nf-austin/valis</b>. Full-resolution warped slides are under
each set's <code>registered/</code> directory; per-set metrics are in
<code>registration_qc.csv</code>.</footer>
</div></body></html>"""

    Path(args.out_html).write_text(doc, encoding="utf-8")
    print(f"Wrote {args.out_html} ({n_sets} set(s), {n_images} images) and {args.out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
