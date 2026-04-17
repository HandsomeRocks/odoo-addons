import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "staging_manager."


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    staging_manager_url = fields.Char(
        string="Manager URL",
        config_parameter=f"{PARAM_PREFIX}url",
        help="Base URL of the Staging Manager (e.g. https://staging.example.com)",
    )
    staging_auth_type = fields.Selection(
        [("api_key", "API Key"), ("basic", "Basic Auth")],
        string="Authentication",
        config_parameter=f"{PARAM_PREFIX}auth_type",
        default="api_key",
    )
    staging_api_key = fields.Char(
        string="API Key",
        config_parameter=f"{PARAM_PREFIX}api_key",
    )
    staging_basic_user = fields.Char(
        string="Username",
        config_parameter=f"{PARAM_PREFIX}basic_user",
    )
    staging_basic_password = fields.Char(
        string="Password",
        config_parameter=f"{PARAM_PREFIX}basic_password",
    )
    staging_sync_interval = fields.Integer(
        string="Auto-sync interval (minutes)",
        config_parameter=f"{PARAM_PREFIX}sync_interval",
        default=5,
    )

    def action_test_connection(self):
        self.ensure_one()
        try:
            resp = self.env["staging.instance"]._api_get("/api/health")
            if resp.get("status") == "ok":
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Connection Successful",
                        "message": "Connected to the Staging Manager API.",
                        "type": "success",
                        "sticky": False,
                    },
                }
        except Exception as e:
            raise UserError(f"Connection failed: {e}")
        raise UserError("Unexpected response from the Staging Manager API.")
