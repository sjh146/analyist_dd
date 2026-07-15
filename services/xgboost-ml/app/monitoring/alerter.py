"""
Alerter
Sends drift detection alerts via Slack webhook.
"""

import json
import logging
import os
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "high": "#FF0000",
    "medium": "#FFA500",
    "low": "#FFFF00",
}


class Alerter:
    """Sends alerts for drift detection results."""

    def __init__(self, slack_webhook_url: Optional[str] = None):
        self.slack_webhook_url = slack_webhook_url or os.getenv("SLACK_WEBHOOK_URL")

    # ------------------------------------------------------------------
    # Slack Alert
    # ------------------------------------------------------------------
    def send_slack_alert(self, message: str, severity: str = "medium") -> bool:
        """Send a message to Slack via webhook with colored attachment.

        Args:
            message: Alert message text.
            severity: Severity level ('high', 'medium', 'low').

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self.slack_webhook_url:
            logger.warning(
                "No SLACK_WEBHOOK_URL configured — alert not sent. "
                f"Message: {message[:100]}..."
            )
            return False

        color = SEVERITY_COLORS.get(severity, "#FFA500")
        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f"ML Drift Alert [{severity.upper()}]",
                    "text": message,
                    "footer": "Drift Detection System",
                }
            ]
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                self.slack_webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info(f"Slack alert sent (severity={severity})")
                    return True
                else:
                    logger.warning(
                        f"Slack returned status {resp.status}: {resp.read().decode()}"
                    )
                    return False
        except URLError as e:
            logger.warning(f"Failed to send Slack alert: {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error sending Slack alert: {e}")
            return False

    # ------------------------------------------------------------------
    # Drift Alert
    # ------------------------------------------------------------------
    def send_drift_alert(self, drift_result: dict) -> bool:
        """Format a drift detection result and send as Slack alert.

        Args:
            drift_result: Dict from DriftDetector.check_performance_drift()
                         or check_data_drift().

        Returns:
            True if sent successfully, False otherwise.
        """
        drift_detected = drift_result.get("drift_detected", False)
        severity = drift_result.get("severity", "medium")

        if not drift_detected:
            logger.info("No drift detected — skipping alert")
            return False

        # Build message based on drift type
        drifted_metrics = drift_result.get("drifted_metrics", {})
        high_drift_features = drift_result.get("high_drift_features", [])
        psi_values = drift_result.get("psi_values", {})

        lines = [f"*Drift Detected:* {severity.upper()} severity"]

        if drifted_metrics:
            lines.append("\n*Performance Drift:*")
            for metric, drop_pct in drifted_metrics.items():
                lines.append(f"  • {metric}: dropped {drop_pct}%")

        if high_drift_features:
            lines.append("\n*Data Drift (PSI > 0.2):*")
            for feat in high_drift_features:
                psi = psi_values.get(feat, "?")
                lines.append(f"  • {feat}: PSI={psi}")

        if not drifted_metrics and not high_drift_features:
            lines.append("\nDrift detected but no specific metrics available.")

        message = "\n".join(lines)
        return self.send_slack_alert(message, severity=severity)
