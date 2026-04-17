import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CreateInstanceWizard(models.TransientModel):
    _name = "staging.instance.create.wizard"
    _description = "Create Staging Instance"

    branch_name = fields.Selection(
        selection="_get_branches",
        string="Branch",
        required=True,
    )
    label = fields.Char(string="Label")
    ticket_url = fields.Char(string="Ticket URL")
    init_modules = fields.Char(
        string="Init Modules",
        help="Comma-separated list of modules to install (e.g. sale,purchase)",
    )
    upgrade_modules = fields.Char(
        string="Upgrade Modules",
        help="Comma-separated list of modules to upgrade",
    )

    @api.model
    def _get_branches(self):
        try:
            branches = self.env["staging.instance"]._api_get("/api/branches")
            return [
                (b["name"], f"{b['name']}  [taken]" if b.get("taken") else b["name"])
                for b in branches
            ]
        except Exception as e:
            _logger.warning("Could not fetch branches: %s", e)
            return []

    def action_create(self):
        self.ensure_one()
        if not self.branch_name:
            raise UserError("Please select a branch.")

        data = {
            "branch_name": self.branch_name,
            "label": self.label or "",
            "ticket_url": self.ticket_url or "",
            "init_modules": self.init_modules or "",
            "upgrade_modules": self.upgrade_modules or "",
        }

        try:
            result = self.env["staging.instance"]._api_post(
                "/api/instances", data=data
            )
        except Exception as e:
            error_msg = str(e)
            try:
                if hasattr(e, "response") and e.response is not None:
                    error_msg = e.response.json().get("error", error_msg)
            except Exception:
                pass
            raise UserError(f"Failed to create instance: {error_msg}")

        self.env["staging.instance"]._upsert_from_api(result)

        return {
            "type": "ir.actions.act_window",
            "name": "Staging Instances",
            "res_model": "staging.instance",
            "view_mode": "kanban,tree,form",
            "target": "main",
        }
