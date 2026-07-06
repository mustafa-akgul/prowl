"""Report generator — produces HTML and PDF review reports.

Generates rich, visually formatted review reports using Jinja2 templates.
Reports include code complexity metrics, security risk tables, and
test coverage summaries.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment

logger = logging.getLogger(__name__)

# Output directory for generated reports
REPORTS_DIR = Path(__file__).parent.parent / ".data" / "reports"

# HTML report template
_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code Review Report — PR #{{ pr_number }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
            background: #0d1117; color: #c9d1d9; line-height: 1.6;
            padding: 40px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        .header {
            background: linear-gradient(135deg, #161b22, #1c2333);
            border: 1px solid #30363d; border-radius: 12px;
            padding: 32px; margin-bottom: 24px;
        }
        .header h1 { color: #58a6ff; font-size: 24px; margin-bottom: 8px; }
        .header .meta { color: #8b949e; font-size: 14px; }
        .header .meta span { margin-right: 16px; }
        .badge {
            display: inline-block; padding: 4px 12px; border-radius: 12px;
            font-size: 12px; font-weight: 600; margin-left: 8px;
        }
        .badge-base { background: rgba(88,166,255,.15); color: #58a6ff; }
        .badge-security { background: rgba(251,191,36,.15); color: #fbbf24; }
        .badge-performance { background: rgba(167,139,250,.15); color: #a78bfa; }
        .section {
            background: #161b22; border: 1px solid #30363d;
            border-radius: 12px; padding: 24px; margin-bottom: 20px;
        }
        .section h2 {
            color: #58a6ff; font-size: 18px; margin-bottom: 16px;
            padding-bottom: 8px; border-bottom: 1px solid #21262d;
        }
        .metric-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px; margin-bottom: 16px;
        }
        .metric-card {
            background: #0d1117; border: 1px solid #21262d;
            border-radius: 8px; padding: 16px; text-align: center;
        }
        .metric-value { font-size: 28px; font-weight: 700; color: #58a6ff; }
        .metric-label { font-size: 12px; color: #8b949e; margin-top: 4px; }
        table {
            width: 100%; border-collapse: collapse; margin-top: 12px;
        }
        th, td {
            padding: 10px 12px; text-align: left;
            border-bottom: 1px solid #21262d;
        }
        th { color: #8b949e; font-size: 12px; text-transform: uppercase; }
        .risk-critical { color: #f85149; }
        .risk-high { color: #f0883e; }
        .risk-medium { color: #fbbf24; }
        .risk-low { color: #58a6ff; }
        .review-content {
            background: #0d1117; border: 1px solid #21262d;
            border-radius: 8px; padding: 20px;
            white-space: pre-wrap; font-size: 14px;
        }
        .footer {
            text-align: center; color: #484f58; font-size: 12px;
            margin-top: 32px; padding-top: 16px;
            border-top: 1px solid #21262d;
        }
        @media print {
            body { background: white; color: #1f2937; padding: 20px; }
            .header { background: #f3f4f6; border-color: #e5e7eb; }
            .section { background: #f9fafb; border-color: #e5e7eb; }
            .metric-card { background: white; border-color: #e5e7eb; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Code Review Report — PR #{{ pr_number }}
                <span class="badge badge-{{ mode }}">{{ mode | upper }}</span>
            </h1>
            <div class="meta">
                <span>📅 {{ generated_at }}</span>
                {% if jira_key %}<span>🎫 {{ jira_key }}</span>{% endif %}
                <span>📁 {{ file_count }} files changed</span>
                <span>📝 +{{ additions }} / -{{ deletions }}</span>
            </div>
        </div>

        <div class="section">
            <h2>📊 Metrics Summary</h2>
            <div class="metric-grid">
                <div class="metric-card">
                    <div class="metric-value">{{ file_count }}</div>
                    <div class="metric-label">Files Changed</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">{{ additions }}</div>
                    <div class="metric-label">Lines Added</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">{{ deletions }}</div>
                    <div class="metric-label">Lines Removed</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">{{ finding_count }}</div>
                    <div class="metric-label">Findings</div>
                </div>
            </div>
        </div>

        {% if security_findings %}
        <div class="section">
            <h2>🛡️ Security Findings</h2>
            <table>
                <thead>
                    <tr>
                        <th>Severity</th>
                        <th>Location</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
                {% for finding in security_findings %}
                    <tr>
                        <td class="risk-{{ finding.severity | lower }}">{{ finding.severity }}</td>
                        <td>{{ finding.location }}</td>
                        <td>{{ finding.description }}</td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}

        <div class="section">
            <h2>📝 Full Review</h2>
            <div class="review-content">{{ review_text }}</div>
        </div>

        <div class="footer">
            AI Code Review Agent v3.1 — Generated {{ generated_at }}
        </div>
    </div>
</body>
</html>"""


class ReportGenerator:
    """Generates HTML and PDF review reports.

    Uses Jinja2 for HTML templating and optionally WeasyPrint for
    PDF conversion.
    """

    def __init__(self) -> None:
        """Initializes the report generator."""
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self._env = Environment(loader=BaseLoader())
        self._template = self._env.from_string(_REPORT_TEMPLATE)

    def generate_html(
        self,
        pr_number: int,
        mode: str,
        review_text: str,
        jira_key: str = "",
        file_count: int = 0,
        additions: int = 0,
        deletions: int = 0,
    ) -> str:
        """Generates an HTML review report.

        Args:
            pr_number: Pull request number.
            mode: Review mode used.
            review_text: Generated review content.
            jira_key: Optional Jira ticket key.
            file_count: Number of changed files.
            additions: Lines added.
            deletions: Lines removed.

        Returns:
            Path to the generated HTML file.
        """
        security_findings = self._extract_security_findings(review_text)
        finding_count = len(security_findings) or self._count_findings(review_text)

        html = self._template.render(
            pr_number=pr_number,
            mode=mode,
            review_text=review_text,
            jira_key=jira_key,
            file_count=file_count,
            additions=additions,
            deletions=deletions,
            finding_count=finding_count,
            security_findings=security_findings,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        filename = f"review_pr{pr_number}_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        filepath = REPORTS_DIR / filename
        filepath.write_text(html, encoding="utf-8")

        logger.info("HTML report generated: %s", filepath)
        return str(filepath)

    def generate_pdf(
        self,
        pr_number: int,
        mode: str,
        review_text: str,
        **kwargs: Any,
    ) -> str | None:
        """Generates a PDF review report.

        Requires WeasyPrint to be installed. Falls back to HTML-only
        if WeasyPrint is not available.

        Args:
            pr_number: Pull request number.
            mode: Review mode used.
            review_text: Generated review content.
            **kwargs: Additional arguments passed to generate_html.

        Returns:
            Path to the generated PDF file, or None if WeasyPrint is
            not installed.
        """
        html_path = self.generate_html(pr_number, mode, review_text, **kwargs)

        try:
            from weasyprint import HTML
        except ImportError:
            logger.warning(
                "WeasyPrint not installed — PDF generation skipped. "
                "Install with: pip install weasyprint"
            )
            return None

        try:
            pdf_path = html_path.replace(".html", ".pdf")
            HTML(filename=html_path).write_pdf(pdf_path)
            logger.info("PDF report generated: %s", pdf_path)
            return pdf_path
        except Exception as exc:
            logger.error("PDF generation failed: %s", exc)
            return None

    @staticmethod
    def _extract_security_findings(review_text: str) -> list[dict[str, str]]:
        """Extracts structured security findings from review text.

        Looks for patterns like:
        - 🔴 **CRITICAL** — [file:line] description
        - 🟠 **HIGH** — [file:line] description

        Args:
            review_text: Review text to parse.

        Returns:
            List of finding dictionaries with severity, location,
            and description.
        """
        findings: list[dict[str, str]] = []
        pattern = re.compile(
            r"[🔴🟠🟡🔵]\s*\*\*(\w+)\*\*\s*[—-]\s*\[([^\]]+)\]\s*(.*)",
        )

        for match in pattern.finditer(review_text):
            findings.append(
                {
                    "severity": match.group(1),
                    "location": match.group(2),
                    "description": match.group(3).strip(),
                }
            )

        return findings

    @staticmethod
    def _count_findings(review_text: str) -> int:
        """Counts the approximate number of findings in review text.

        Args:
            review_text: Review text to analyze.

        Returns:
            Estimated number of findings.
        """
        # Count bullet points with file/line references
        lines = review_text.splitlines()
        count = 0
        for line in lines:
            stripped = line.strip()
            if (
                stripped.startswith("- **[")
                or stripped.startswith("- 🔴")
                or stripped.startswith("- 🟠")
                or stripped.startswith("- 🟡")
                or stripped.startswith("- 🔵")
            ):
                count += 1
        return count
