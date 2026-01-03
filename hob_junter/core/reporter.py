import html
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from hob_junter.core.scraper import JobRecord


def send_telegram_message(text: str, bot_token: Optional[str], chat_id: Optional[str]):
    if not bot_token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                chunk = text[i : i + 4000]
                requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=10)
        else:
            requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"[Telegram] Error: {exc}")


def summarize_jobs(jobs_with_scores: List[Tuple[JobRecord, int, str, Dict]]) -> str:
    if not jobs_with_scores:
        return "No new matches."

    msg = f"Found {len(jobs_with_scores)} matches:\n"
    sorted_jobs = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)

    for job, score, reason, _ in sorted_jobs[:10]:
        msg += f"\n {score}/100 - {job.title} @ {job.company}\nLink: {job.apply_url}\nReason: {reason[:100]}...\n"

    if len(sorted_jobs) > 10:
        msg += f"\n...and {len(sorted_jobs) - 10} more in the HTML report."

    return msg


def export_jobs_html(
    jobs_with_scores: List[Tuple[JobRecord, int, str, Dict]],
    strategy_data: Dict,
    path: str,
):
    if not jobs_with_scores:
        return

    strategy_data = strategy_data or {}
    advisor = strategy_data.get("advisor_response", {})
    final_roles = strategy_data.get("final_roles", [])
    exclusions = strategy_data.get("exclusions", [])

    archetype = advisor.get("archetype", "N/A")
    ai_suggestions = advisor.get("suggestions", [])

    suggestions_html = ""
    for sugg in ai_suggestions:
        suggestions_html += f"<li><strong>{sugg.get('role')}</strong>: {sugg.get('reason')}</li>"

    header_html = f"""
    <div class="strategy-box">
      <div class="strategy-header">
        <div>
          <h2>MISSION DOSSIER: <span style="color:#1a73e8">{archetype}</span></h2>
          <p><strong>Target Industry:</strong> {advisor.get('industry', 'Unknown')}</p>
        </div>
        <div class="stats-box">
           <div><strong>Matches Found:</strong> {len(jobs_with_scores)}</div>
           <div><strong>Active Filters:</strong> {len(final_roles)} Roles</div>
        </div>
      </div>
      
      <div class="grid-container">
        <div class="panel">
           <h3> AI Strategic Assessment</h3>
           <p style="font-size:0.9em; color:#555;">Based on your profile, the following opportunities offer the highest probability of success:</p>
           <ul class="suggestion-list">{suggestions_html}</ul>
        </div>
        
        <div class="panel">
           <h3>⚡ Active Search Parameters</h3>
           <p style="font-size:0.9em; color:#555;">These are the actual keywords and filters currently being hunted:</p>
           <div class="tag-container">
             {''.join([f'<span class="tag tag-role">{r}</span>' for r in final_roles])}
           </div>
           
           {f'<h4>Exclusions (NOT):</h4><div class="tag-container">' + ''.join([f'<span class="tag tag-exclude">{e}</span>' for e in exclusions]) + '</div>' if exclusions else ''}
        </div>
      </div>
    </div>
    """

    rows = []
    sorted_jobs = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)

    for job, score, reason, red_team_data in sorted_jobs:
        color = "#137333" if score >= 80 else "#f9ab00" if score >= 60 else "#d93025"

        red_team_html = ""
        if red_team_data and score >= 85:
            questions = "<li>" + "</li><li>".join(red_team_data.get("interview_questions", [])) + "</li>"
            hook = red_team_data.get("outreach_hook", "N/A")
            red_team_html = f"""
            <div style="background: #fff0f0; padding: 12px; margin-top: 10px; border-left: 4px solid #d93025; font-size: 0.9em; border-radius: 4px;">
                <strong style="color: #b71c1c;"> Red Team Analysis (Kill Questions):</strong>
                <ul style="margin: 5px 0 10px 20px; color: #333;">{questions}</ul>
                <div style="background: #e3f2fd; padding: 8px; border-left: 4px solid #1976d2; color: #0d47a1; margin-top: 5px;">
                    <strong>📧 Sniper Outreach:</strong> "{html.escape(hook)}"
                </div>
            </div>
            """

        rows.append(
            f"<tr><td><div class='job-title'>{html.escape(job.title)}</div><div class='job-comp'>{html.escape(job.company)}</div></td>"
            f"<td><span style='font-size:1.2em; font-weight:bold; color:{color}'>{score}</span></td>"
            f"<td><a href='{html.escape(job.apply_url)}' target='_blank' class='btn'>Apply</a></td>"
            f"<td class='reason-cell'>{html.escape(reason)}{red_team_html}</td></tr>"
        )

    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Hob-Junter Intelligence Report</title>
  <style>
    body {{ font-family: 'Segoe UI', Roboto, Helvetica, sans-serif; margin: 0; background: #f4f6f8; color: #172b4d; }}
    .container {{ max-width: 1200px; margin: 40px auto; padding: 0 20px; }}
    
    .strategy-box {{ background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); padding: 25px; margin-bottom: 30px; border-top: 5px solid #1a73e8; }}
    .strategy-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 15px; }}
    .strategy-header h2 {{ margin: 0; font-size: 1.4em; }}
    .stats-box {{ text-align: right; font-size: 0.9em; color: #5e6c84; }}
    
    .grid-container {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
    .panel h3 {{ margin-top: 0; color: #091e42; font-size: 1.1em; border-bottom: 2px solid #dfe1e6; padding-bottom: 8px; display: inline-block; }}
    .suggestion-list {{ padding-left: 20px; font-size: 0.9em; color: #333; }}
    .suggestion-list li {{ margin-bottom: 8px; }}
    
    .tag-container {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .tag {{ padding: 4px 10px; border-radius: 4px; font-size: 0.85em; font-weight: 500; }}
    .tag-role {{ background: #e3f2fd; color: #0d47a1; border: 1px solid #bbdefb; }}
    .tag-exclude {{ background: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }}

    table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 15px; border-bottom: 1px solid #ebecf0; vertical-align: middle; text-align: left; }}
    th {{ background: #fafbfc; font-weight: 600; color: #5e6c84; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em; }}
    tr:hover {{ background: #f4f5f7; }}
    
    .job-title {{ font-weight: 600; font-size: 1.05em; color: #172b4d; }}
    .job-comp {{ font-size: 0.9em; color: #6b778c; margin-top: 2px; }}
    .reason-cell {{ font-size: 0.9em; color: #42526e; line-height: 1.5; }}
    
    .btn {{ display: inline-block; padding: 6px 12px; background: #0052cc; color: white; text-decoration: none; border-radius: 3px; font-size: 0.9em; font-weight: 500; }}
    .btn:hover {{ background: #0065ff; }}
  </style>
</head>
<body>
  <div class="container">
    {header_html}
    
    <table>
      <thead>
        <tr><th style="width: 30%">Role</th><th style="width: 10%">Score</th><th style="width: 10%">Action</th><th>Analysis</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <p style="text-align: center; color: #888; font-size: 0.8em; margin-top: 30px;">Generated by Hob-Junter at {datetime.now().strftime('%H:%M:%S')}</p>
  </div>
</body>
</html>"""

    with open(path, "w") as f:
        f.write(html_doc)
