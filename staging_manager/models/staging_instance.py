import logging
from base64 import b64encode

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

STATUS_COLORS = {
    "running": 10,    # green
    "building": 2,    # orange
    "pending": 2,     # orange
    "updating": 2,    # orange
    "stopped": 1,     # red
    "destroying": 1,  # red
    "error": 1,       # red
}

TEST_STATUS_COLORS = {
    "passed": 10,
    "failed": 1,
    "running": 2,
    "skipped": 0,
}


class StagingInstance(models.Model):
    _name = "staging.instance"
    _description = "Staging Instance"
    _order = "create_date desc"
    _rec_name = "display_name"

    # ── Core fields (synced from API) ─────────────────────────────────────

    remote_id = fields.Integer(string="Remote ID", readonly=True, index=True)
    branch_name = fields.Char(string="Branch", required=True, readonly=True)
    slug = fields.Char(string="Slug", readonly=True, index=True)
    label = fields.Char(string="Label")
    ticket_url = fields.Char(string="Ticket URL")

    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("building", "Building"),
            ("running", "Running"),
            ("stopped", "Stopped"),
            ("updating", "Updating"),
            ("destroying", "Destroying"),
            ("error", "Error"),
        ],
        string="Status",
        default="pending",
        readonly=True,
    )
    status_color = fields.Integer(
        compute="_compute_status_color", store=False
    )

    url = fields.Char(string="Instance URL", readonly=True)
    db_name = fields.Char(string="Database", readonly=True)
    db_source = fields.Selection(
        [("local", "Local Clone"), ("remote", "Remote Clone"), ("empty", "Empty")],
        string="DB Source",
        readonly=True,
    )
    git_commit = fields.Char(string="Commit SHA", readonly=True)

    # PR info
    pr_number = fields.Integer(string="PR #", readonly=True)
    pr_url = fields.Char(string="PR URL", readonly=True)
    pr_state = fields.Char(string="PR State", readonly=True)

    # Testing
    test_status = fields.Selection(
        [
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("running", "Running"),
            ("skipped", "Skipped"),
        ],
        string="Test Status",
        readonly=True,
    )
    test_status_color = fields.Integer(
        compute="_compute_test_status_color", store=False
    )
    test_log = fields.Text(string="Test Log", readonly=True)

    # Modules
    init_modules = fields.Char(string="Init Modules")
    upgrade_modules = fields.Char(string="Upgrade Modules")

    # Logs
    build_log = fields.Text(string="Build Log", readonly=True)
    error_message = fields.Text(string="Error Message", readonly=True)

    # Timestamps from the manager
    remote_created_at = fields.Datetime(string="Created (Remote)", readonly=True)
    remote_updated_at = fields.Datetime(string="Updated (Remote)", readonly=True)

    display_name = fields.Char(
        compute="_compute_display_name", store=True
    )

    # ── Computed ──────────────────────────────────────────────────────────

    @api.depends("status")
    def _compute_status_color(self):
        for rec in self:
            rec.status_color = STATUS_COLORS.get(rec.status, 0)

    @api.depends("test_status")
    def _compute_test_status_color(self):
        for rec in self:
            rec.test_status_color = TEST_STATUS_COLORS.get(rec.test_status, 0)

    @api.depends("label", "branch_name")
    def _compute_display_name(self):
        for rec in self:
            if rec.label:
                rec.display_name = f"{rec.label} ({rec.branch_name})"
            else:
                rec.display_name = rec.branch_name or "New Instance"

    # ── API transport layer ───────────────────────────────────────────────

    def _get_api_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = (ICP.get_param("staging_manager.url") or "").rstrip("/")
        auth_type = ICP.get_param("staging_manager.auth_type") or "api_key"
        if not base_url:
            raise UserError(
                "Staging Manager URL is not configured. "
                "Go to Staging Manager → Configuration → Settings."
            )
        return base_url, auth_type

    def _get_session(self):
        base_url, auth_type = self._get_api_config()
        ICP = self.env["ir.config_parameter"].sudo()
        sess = requests.Session()
        sess.timeout = 30

        if auth_type == "api_key":
            api_key = ICP.get_param("staging_manager.api_key") or ""
            if not api_key:
                raise UserError("API Key is not configured in settings.")
            sess.headers["X-Api-Key"] = api_key
        else:
            user = ICP.get_param("staging_manager.basic_user") or ""
            pwd = ICP.get_param("staging_manager.basic_password") or ""
            if not user or not pwd:
                raise UserError(
                    "Basic auth credentials are not configured in settings."
                )
            sess.auth = (user, pwd)

        return sess, base_url

    @api.model
    def _api_get(self, path):
        sess, base_url = self._get_session()
        resp = sess.get(f"{base_url}{path}")
        resp.raise_for_status()
        return resp.json()

    @api.model
    def _api_post(self, path, data=None):
        sess, base_url = self._get_session()
        resp = sess.post(f"{base_url}{path}", json=data)
        resp.raise_for_status()
        return resp.json()

    # ── Sync ──────────────────────────────────────────────────────────────

    @api.model
    def action_sync_all(self):
        """Fetch all instances from the manager and sync local records."""
        try:
            remote_list = self._api_get("/api/instances")
        except Exception as e:
            _logger.error("Failed to sync staging instances: %s", e)
            raise UserError(f"Sync failed: {e}")

        remote_ids = set()
        for data in remote_list:
            remote_ids.add(data["id"])
            self._upsert_from_api(data)

        orphans = self.search([
            ("remote_id", "!=", False),
            ("remote_id", "not in", list(remote_ids)),
        ])
        if orphans:
            orphans.unlink()

        return True

    @api.model
    def _cron_sync(self):
        """Called by ir.cron to sync instances periodically."""
        try:
            self.action_sync_all()
        except Exception:
            _logger.exception("Cron sync of staging instances failed")

    def action_refresh(self):
        """Refresh a single instance from the API."""
        self.ensure_one()
        if not self.slug:
            return
        try:
            data = self._api_get(f"/api/instances/{self.slug}")
            self._upsert_from_api(data)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                self.unlink()
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Instance Removed",
                        "message": "This instance no longer exists on the manager.",
                        "type": "warning",
                    },
                }
            raise UserError(f"Refresh failed: {e}")
        except Exception as e:
            raise UserError(f"Refresh failed: {e}")

    @api.model
    def _upsert_from_api(self, data):
        existing = self.search([("remote_id", "=", data["id"])], limit=1)
        vals = self._map_api_to_vals(data)
        if existing:
            existing.write(vals)
            return existing
        else:
            vals["remote_id"] = data["id"]
            return self.create(vals)

    @api.model
    def _map_api_to_vals(self, data):
        return {
            "branch_name": data.get("branch_name", ""),
            "slug": data.get("slug", ""),
            "label": data.get("label") or "",
            "ticket_url": data.get("ticket_url") or "",
            "status": data.get("status", "pending"),
            "url": data.get("url") or "",
            "db_name": data.get("db_name") or "",
            "db_source": data.get("db_source") or False,
            "git_commit": data.get("git_commit") or "",
            "pr_number": data.get("pr_number") or 0,
            "pr_url": data.get("pr_url") or "",
            "pr_state": data.get("pr_state") or "",
            "test_status": data.get("test_status") or False,
            "test_log": data.get("test_log") or "",
            "init_modules": data.get("init_modules") or "",
            "upgrade_modules": data.get("upgrade_modules") or "",
            "build_log": data.get("build_log") or "",
            "error_message": data.get("error_message") or "",
            "remote_created_at": data.get("created_at") or False,
            "remote_updated_at": data.get("updated_at") or False,
        }

    # ── Instance actions ──────────────────────────────────────────────────

    def _do_action(self, endpoint_suffix, success_msg, error_prefix):
        self.ensure_one()
        if not self.slug:
            raise UserError("Instance has no slug — sync first.")
        try:
            result = self._api_post(f"/api/instances/{self.slug}/{endpoint_suffix}")
            if isinstance(result, dict) and "error" not in result:
                self._upsert_from_api(result)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Success",
                    "message": success_msg,
                    "type": "success",
                    "sticky": False,
                },
            }
        except requests.exceptions.HTTPError as e:
            body = ""
            if e.response is not None:
                try:
                    body = e.response.json().get("error", e.response.text)
                except Exception:
                    body = e.response.text
            raise UserError(f"{error_prefix}: {body or e}")
        except Exception as e:
            raise UserError(f"{error_prefix}: {e}")

    def action_start(self):
        return self._do_action("start", "Instance started.", "Failed to start")

    def action_stop(self):
        return self._do_action("stop", "Instance stopped.", "Failed to stop")

    def action_rebuild(self):
        return self._do_action(
            "rebuild",
            "Rebuild started — the instance will be re-created from production.",
            "Failed to rebuild",
        )

    def action_update(self):
        return self._do_action(
            "update",
            "Pulling latest code and restarting...",
            "Failed to update",
        )

    def action_destroy(self):
        self.ensure_one()
        if not self.slug:
            raise UserError("Instance has no slug — sync first.")
        try:
            self._api_post(f"/api/instances/{self.slug}/destroy")
            self.write({"status": "destroying"})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Destroying",
                    "message": f"Instance {self.branch_name} is being destroyed.",
                    "type": "warning",
                    "sticky": False,
                },
            }
        except Exception as e:
            raise UserError(f"Failed to destroy: {e}")

    def action_run_tests(self):
        return self._do_action(
            "run-tests",
            "Tests started — check back in a moment.",
            "Failed to run tests",
        )

    def action_open_url(self):
        self.ensure_one()
        if not self.url:
            raise UserError("No URL available for this instance.")
        return {
            "type": "ir.actions.act_url",
            "url": self.url,
            "target": "new",
        }

    def action_open_pr(self):
        self.ensure_one()
        if not self.pr_url:
            raise UserError("No PR URL available for this instance.")
        return {
            "type": "ir.actions.act_url",
            "url": self.pr_url,
            "target": "new",
        }

    def action_view_logs(self):
        """Fetch logs from API and show in a popup."""
        self.ensure_one()
        if not self.slug:
            raise UserError("Instance has no slug — sync first.")
        try:
            logs = self._api_get(f"/api/instances/{self.slug}/logs")
            self.write({
                "build_log": logs.get("build_log") or "",
                "test_log": logs.get("test_log") or "",
            })
        except Exception as e:
            _logger.warning("Could not fetch logs: %s", e)

        return {
            "type": "ir.actions.act_window",
            "name": f"Logs: {self.display_name}",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {"form_view_ref": "staging_manager.view_staging_instance_logs_form"},
        }
