from odoo import fields, models


class TestNote(models.Model):
    _name = "staging.test.note"
    _description = "Staging Test Note"
    _order = "create_date desc"

    name = fields.Char(string="Title", required=True)
    content = fields.Text(string="Content")
    tag = fields.Selection(
        [
            ("info", "Info"),
            ("success", "Success"),
            ("warning", "Warning"),
            ("bug", "Bug"),
        ],
        string="Tag",
        default="info",
    )
    is_resolved = fields.Boolean(string="Resolved", default=False)

    priority = fields.Selection(
        [
            ("0", "Normal"),
            ("1", "Important"),
            ("2", "Urgent"),
        ],
        string="Priority",
        default="0",
    )
