# -*- coding: utf-8 -*-
"""
MCP WebApp User Storage Model

Provides persistent storage for web applications.
- Logged-in users: Storage tied to user_id (persists across sessions/devices)
- Anonymous users: Storage tied to session_id (persists within session)
"""

import json
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MCPWebAppUserStorage(models.Model):
    _name = 'mcp.webapp.user.storage'
    _description = 'MCP WebApp User Storage'
    _order = 'write_date desc'

    # -------------------------------------------------------------------------
    # Core Fields
    # -------------------------------------------------------------------------
    webapp_id = fields.Many2one(
        'mcp.webapp',
        string='Web Application',
        required=True,
        ondelete='cascade',
        index=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        ondelete='cascade',
        index=True,
        help='User who owns this storage (for logged-in users)',
    )
    session_id = fields.Char(
        string='Session ID',
        index=True,
        help='Session identifier (for anonymous/public users)',
    )
    data = fields.Json(
        string='Storage Data',
        default=dict,
        help='JSON object containing all user data for this webapp',
    )
    data_text = fields.Text(
        string='Storage Data (Text)',
        compute='_compute_data_text',
        inverse='_inverse_data_text',
        help='JSON text representation for editing',
    )
    last_accessed = fields.Datetime(
        string='Last Accessed',
        default=fields.Datetime.now,
        index=True,
    )

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------
    _sql_constraints = [
        ('unique_user_storage',
         'UNIQUE(webapp_id, user_id)',
         'Each user can only have one storage record per webapp'),
        ('unique_session_storage',
         'UNIQUE(webapp_id, session_id)',
         'Each session can only have one storage record per webapp'),
    ]

    @api.constrains('user_id', 'session_id')
    def _check_identifier(self):
        """Ensure either user_id or session_id is set, but not both."""
        for record in self:
            if not record.user_id and not record.session_id:
                raise ValidationError(
                    "Storage must be associated with either a user or a session."
                )
            if record.user_id and record.session_id:
                raise ValidationError(
                    "Storage cannot be associated with both a user and a session."
                )

    @api.depends('data')
    def _compute_data_text(self):
        for record in self:
            if record.data:
                record.data_text = json.dumps(record.data, indent=2)
            else:
                record.data_text = ''

    def _inverse_data_text(self):
        for record in self:
            if record.data_text:
                try:
                    record.data = json.loads(record.data_text)
                except json.JSONDecodeError as e:
                    raise ValidationError(
                        f"Invalid JSON in Storage Data: {e}"
                    ) from None
            else:
                record.data = False

    @api.constrains('data')
    def _check_data_size(self):
        """Limit storage size to 5MB per user per webapp."""
        for record in self:
            if record.data:
                data_size = len(json.dumps(record.data))
                if data_size > 5_000_000:  # 5MB limit
                    raise ValidationError(
                        f"Storage limit exceeded. Maximum size is 5MB, "
                        f"current size is {data_size / 1_000_000:.2f}MB"
                    )

    # -------------------------------------------------------------------------
    # Storage Methods
    # -------------------------------------------------------------------------
    @api.model
    def get_storage(self, webapp_id, user_id=None, session_id=None):
        """
        Get or create storage record for a user or session.

        For logged-in users, pass user_id (persists across sessions).
        For anonymous users, pass session_id (persists within session).

        :param webapp_id: ID of the webapp
        :param user_id: ID of user (for logged-in users)
        :param session_id: Session ID (for anonymous users)
        :return: Storage record
        """
        if not user_id and not session_id:
            raise ValueError("Either user_id or session_id must be provided")

        # Build domain for search
        if user_id:
            domain = [('webapp_id', '=', webapp_id), ('user_id', '=', user_id)]
            values = {'webapp_id': webapp_id, 'user_id': user_id, 'data': {}}
        else:
            domain = [('webapp_id', '=', webapp_id), ('session_id', '=', session_id)]
            values = {'webapp_id': webapp_id, 'session_id': session_id, 'data': {}}

        # First try to find existing record (most common case)
        storage = self.search(domain, limit=1)
        if storage:
            return storage

        # Record doesn't exist, try to create it
        # Use savepoint to handle race condition gracefully
        try:
            savepoint = self.env.cr.savepoint()
            storage = self.create(values)
            savepoint.close(rollback=False)
            return storage
        except Exception:
            # Race condition - another request created it first
            # Rollback to savepoint and search again
            savepoint.close(rollback=True)
            storage = self.search(domain, limit=1)
            if storage:
                return storage
            # If still not found, something else is wrong - re-raise
            raise

    def get_value(self, key, default=None):
        """Get a value from storage."""
        self.ensure_one()
        self.write({'last_accessed': fields.Datetime.now()})
        data = self.data or {}
        return data.get(key, default)

    def set_value(self, key, value):
        """Set a value in storage."""
        self.ensure_one()
        data = dict(self.data or {})
        data[key] = value
        self.write({
            'data': data,
            'last_accessed': fields.Datetime.now(),
        })
        return True

    def delete_value(self, key):
        """Delete a value from storage."""
        self.ensure_one()
        data = dict(self.data or {})
        if key in data:
            del data[key]
            self.write({
                'data': data,
                'last_accessed': fields.Datetime.now(),
            })
            return True
        return False

    def clear_storage(self):
        """Clear all data in storage."""
        self.ensure_one()
        self.write({
            'data': {},
            'last_accessed': fields.Datetime.now(),
        })
        return True

    def get_all(self):
        """Get all data from storage."""
        self.ensure_one()
        self.write({'last_accessed': fields.Datetime.now()})
        return self.data or {}

    # -------------------------------------------------------------------------
    # Cleanup Methods
    # -------------------------------------------------------------------------
    @api.model
    def _cleanup_expired_sessions(self, days=30):
        """
        Delete anonymous session storage older than specified days.

        This is called by a scheduled action to prevent accumulation
        of orphaned session storage records.

        :param days: Number of days after which to consider storage expired
        :return: Number of records deleted
        """
        cutoff = fields.Datetime.now() - timedelta(days=days)
        expired_storage = self.search([
            ('session_id', '!=', False),
            ('user_id', '=', False),
            ('last_accessed', '<', cutoff),
        ])
        count = len(expired_storage)
        if expired_storage:
            expired_storage.unlink()
        return count
