# -*- coding: utf-8 -*-
"""
MCP BaseModel Extensions

Monkey-patches BaseModel.check_access_rights() and check_access_rule() to enforce
MCP model restrictions when MCP restrictions are set on the database cursor.

Note: In Odoo 17, access control uses two separate methods:
- check_access_rights() - checks ir.model.access (called first)
- check_access_rule() - checks ir.rule (called second)
In Odoo 18, these were combined into _check_access().
We patch both for comprehensive coverage.
"""
from odoo import api
from odoo.exceptions import AccessError
from odoo.models import BaseModel


def _check_mcp_restriction(env, model_name, operation, raise_exception=True):
    """
    Check MCP model restrictions before normal access check.

    When '_mcp_restricted_models' is set on the cursor (by MCP tool calls),
    this checks if the current model is in the blocklist and if the
    specific operation is blocked.

    Cursor attribute '_mcp_restricted_models' contains a dict:
        {model_name: {'read': bool, 'create': bool, 'write': bool, 'unlink': bool}}
    Where True = operation is BLOCKED, False = operation is allowed.

    Using cursor attribute instead of context provides guarantees:
    - Cannot be bypassed by with_context(), with_user(), etc.
    - Cursor object is shared across all environments in a transaction

    This protects against DIRECT ORM operations:
    - search() / search_read() calls
    - read() calls
    - write() / create() / unlink() calls

    Limitations (does NOT protect against):
    - sudo() calls (intentionally allowed for Odoo compatibility)
    - Relational field traversal (prefetched/cached data)
    - Computed field internal access
    - Raw SQL queries
    - New cursor creation

    The check is skipped for:
    - Superuser mode (env.su) - allows internal Odoo operations
    - When _mcp_restricted_models is not set (normal Odoo usage)
    - execute_orm tool (which doesn't set restrictions)

    Returns True if access is allowed, False if blocked (when raise_exception=False).
    """
    restricted_models = getattr(env.cr, '_mcp_restricted_models', None)

    # Skip for superuser - allows sudo() and internal Odoo operations
    if restricted_models is not None and not env.su:
        # Check if model is in restricted list
        if model_name in restricted_models:
            restrictions = restricted_models[model_name]
            # True = blocked, False = allowed
            if restrictions.get(operation, True):
                if raise_exception:
                    raise AccessError(
                        f"MCP Access Denied: Model '{model_name}' is restricted. "
                        f"The '{operation}' operation is blocked for this model."
                    )
                return False
    return True


# Store references to original methods
_original_check_access_rights = BaseModel.check_access_rights
_original_check_access_rule = BaseModel.check_access_rule


@api.model
def _mcp_check_access_rights(self, operation, raise_exception=True):
    """
    Wrapper for check_access_rights to add MCP model restrictions.
    This is called FIRST in the access control flow (before check_access_rule).
    """
    # Check MCP restrictions first
    if not _check_mcp_restriction(self.env, self._name, operation, raise_exception):
        return False
    return _original_check_access_rights(self, operation, raise_exception=raise_exception)


def _mcp_check_access_rule(self, operation):
    """
    Wrapper for check_access_rule to add MCP model restrictions.
    This is called SECOND in the access control flow (after check_access_rights).
    Provides additional safety for direct calls to check_access_rule.
    """
    # Check MCP restrictions (always raises on failure)
    _check_mcp_restriction(self.env, self._name, operation, raise_exception=True)
    return _original_check_access_rule(self, operation)


# Apply the monkey-patches
BaseModel.check_access_rights = _mcp_check_access_rights
BaseModel.check_access_rule = _mcp_check_access_rule
