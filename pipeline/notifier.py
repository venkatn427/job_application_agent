"""
Notification System
--------------------
Sends alerts via:
  • Email (SMTP / Gmail)
  • Slack webhook
  • Discord webhook (optional)

Configure via environment variables — see .env.template.
All methods fail silently (log warning) when not configured so the
pipeline never crashes due to a missing notification credential.
"""

import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Notification payload
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineRunSummary:
    """Summary of a single pipeline run, used in notifications."""
    run_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    jobs_scraped: int = 0
    jobs_scored: int = 0
    jobs_matched: int = 0
    jobs_skipped: int = 0
    resumes_generated: int = 0
    errors: list[str] = field(default_factory=list)
    top_matches: list[dict] = field(default_factory=list)   # [{title, company, score, url}]
    spreadsheet_url: str = ""
    drive_url: str = ""

    def to_text(self) -> str:
        lines = [
            f"📊 Job Application Pipeline — {self.run_date}",
            "=" * 50,
            f"Jobs scraped   : {self.jobs_scraped}",
            f"Jobs scored    : {self.jobs_scored}",
            f"Jobs matched   : {self.jobs_matched}",
            f"Jobs skipped   : {self.jobs_skipped}",
            f"Resumes built  : {self.resumes_generated}",
        ]
        if self.top_matches:
            lines.append("\n🏆 Top Matches:")
            for m in self.top_matches[:5]:
                lines.append(f"  [{m['score']}/100] {m['title']} @ {m['company']}")
                if m.get("url"):
                    lines.append(f"         {m['url']}")
        if self.errors:
            lines.append(f"\n⚠️  {len(self.errors)} error(s):")
            for e in self.errors[:3]:
                lines.append(f"  • {e}")
        if self.spreadsheet_url:
            lines.append(f"\n📋 Tracker: {self.spreadsheet_url}")
        if self.drive_url:
            lines.append(f"📁 Drive:   {self.drive_url}")
        return "\n".join(lines)

    def to_slack_blocks(self) -> list[dict]:
        """Build Slack Block Kit message."""
        status_emoji = "✅" if not self.errors else "⚠️"
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} Job Pipeline — {self.run_date}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Scraped:* {self.jobs_scraped}"},
                    {"type": "mrkdwn", "text": f"*Matched:* {self.jobs_matched}"},
                    {"type": "mrkdwn", "text": f"*Skipped:* {self.jobs_skipped}"},
                    {"type": "mrkdwn", "text": f"*Resumes:* {self.resumes_generated}"},
                ],
            },
        ]
        if self.top_matches:
            match_lines = "\n".join(
                f"• [{m['score']}/100] *{m['title']}* @ {m['company']}"
                for m in self.top_matches[:5]
            )
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🏆 Top Matches:*\n{match_lines}"},
            })
        if self.errors:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*⚠️ Errors ({len(self.errors)}):*\n" + "\n".join(
                        f"• {e[:100]}" for e in self.errors[:3]
                    ),
                },
            })
        links = []
        if self.spreadsheet_url:
            links.append(f"<{self.spreadsheet_url}|📋 View Tracker>")
        if self.drive_url:
            links.append(f"<{self.drive_url}|📁 View Drive Folder>")
        if links:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "  ".join(links)},
            })
        return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Notifier
# ─────────────────────────────────────────────────────────────────────────────

class Notifier:
    """
    Sends pipeline run summaries via multiple channels.
    Each channel is optional — missing config is a warning, not an error.
    """

    def __init__(
        self,
        email_to: Optional[str] = None,
        email_from: Optional[str] = None,
        email_password: Optional[str] = None,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        slack_webhook_url: Optional[str] = None,
        discord_webhook_url: Optional[str] = None,
    ):
        from submission.identity import application_email
        identity_email = application_email()
        self.email_to = identity_email or email_to or os.environ.get("NOTIFY_EMAIL_TO", "")
        self.email_from = identity_email or email_from or os.environ.get("NOTIFY_EMAIL_FROM", "")
        self.email_password = email_password or os.environ.get("NOTIFY_EMAIL_PASSWORD", "")
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.slack_url = slack_webhook_url or os.environ.get("NOTIFY_SLACK_WEBHOOK", "")
        self.discord_url = discord_webhook_url or os.environ.get("NOTIFY_DISCORD_WEBHOOK", "")

    # ── Public methods ─────────────────────────────────────────────────────────

    def send_run_summary(self, summary: PipelineRunSummary) -> None:
        """Send summary to all configured channels."""
        sent = []
        if self.slack_url:
            if self._send_slack(summary):
                sent.append("Slack")
        if self.discord_url:
            if self._send_discord(summary):
                sent.append("Discord")
        if self.email_to and self.email_from:
            if self._send_email(summary):
                sent.append("Email")
        if sent:
            logger.info("Notifications sent via: %s", ", ".join(sent))
        else:
            logger.debug("No notification channels configured")

    def send_error_alert(self, error_message: str, context: str = "") -> None:
        """Send an immediate error alert (not a summary)."""
        msg = f"🚨 Pipeline Error\n{context}\n\n{error_message}"
        if self.slack_url:
            self._post_slack_text(msg)
        if self.discord_url:
            self._post_discord_text(msg)

    def send_match_alert(self, job_title: str, company: str, score: int, url: str) -> None:
        """Immediate alert for a high-scoring match (optional, call per-match)."""
        if score < 85:
            return  # Only alert for exceptional matches
        msg = f"🎯 Great Match Found!\n[{score}/100] {job_title} @ {company}\n{url}"
        if self.slack_url:
            self._post_slack_text(msg)

    # ── Email ──────────────────────────────────────────────────────────────────

    def _send_email(self, summary: PipelineRunSummary) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Job Pipeline Report — {summary.run_date} ({summary.jobs_matched} matches)"
            msg["From"] = self.email_from
            msg["To"] = self.email_to

            text_part = MIMEText(summary.to_text(), "plain")
            html_part = MIMEText(self._summary_to_html(summary), "html")
            msg.attach(text_part)
            msg.attach(html_part)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.email_from, self.email_password)
                server.sendmail(self.email_from, self.email_to, msg.as_string())
            logger.info("Email notification sent to %s", self.email_to)
            return True
        except Exception as e:
            logger.warning("Email notification failed: %s", e)
            return False

    def _summary_to_html(self, summary: PipelineRunSummary) -> str:
        match_rows = ""
        for m in summary.top_matches[:10]:
            url = m.get("url", "#")
            match_rows += (
                f"<tr><td>{m['score']}/100</td>"
                f"<td><a href='{url}'>{m['title']}</a></td>"
                f"<td>{m['company']}</td></tr>"
            )
        errors_html = ""
        if summary.errors:
            items = "".join(f"<li>{e}</li>" for e in summary.errors[:5])
            errors_html = f"<h3>⚠️ Errors</h3><ul>{items}</ul>"
        links_html = ""
        if summary.spreadsheet_url:
            links_html += f"<a href='{summary.spreadsheet_url}'>📋 View Tracker</a>&nbsp;&nbsp;"
        if summary.drive_url:
            links_html += f"<a href='{summary.drive_url}'>📁 View Drive</a>"
        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto">
        <h2>📊 Job Application Pipeline — {summary.run_date}</h2>
        <table border="0" cellpadding="8" style="background:#f5f5f5;border-radius:8px">
          <tr><td><b>Jobs Scraped</b></td><td>{summary.jobs_scraped}</td></tr>
          <tr><td><b>Jobs Matched</b></td><td>{summary.jobs_matched}</td></tr>
          <tr><td><b>Jobs Skipped</b></td><td>{summary.jobs_skipped}</td></tr>
          <tr><td><b>Resumes Built</b></td><td>{summary.resumes_generated}</td></tr>
        </table>
        <h3>🏆 Top Matches</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
          <tr style="background:#1a1a2e;color:white"><th>Score</th><th>Title</th><th>Company</th></tr>
          {match_rows}
        </table>
        {errors_html}
        <p>{links_html}</p>
        </body></html>"""

    # ── Slack ──────────────────────────────────────────────────────────────────

    def _send_slack(self, summary: PipelineRunSummary) -> bool:
        try:
            payload = {"blocks": summary.to_slack_blocks()}
            r = requests.post(self.slack_url, json=payload, timeout=10)
            r.raise_for_status()
            logger.info("Slack notification sent")
            return True
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)
            return False

    def _post_slack_text(self, text: str) -> None:
        try:
            requests.post(self.slack_url, json={"text": text}, timeout=10)
        except Exception as e:
            logger.debug("Slack post failed: %s", e)

    # ── Discord ────────────────────────────────────────────────────────────────

    def _send_discord(self, summary: PipelineRunSummary) -> bool:
        return self._post_discord_text(summary.to_text()[:2000])

    def _post_discord_text(self, text: str) -> bool:
        try:
            r = requests.post(
                self.discord_url,
                json={"content": text[:2000]},
                timeout=10,
            )
            r.raise_for_status()
            logger.info("Discord notification sent")
            return True
        except Exception as e:
            logger.warning("Discord notification failed: %s", e)
            return False
