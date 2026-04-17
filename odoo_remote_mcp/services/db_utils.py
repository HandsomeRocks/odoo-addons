# -*- coding: utf-8 -*-
"""
Database Context Utilities for Multi-Database Support

Provides utilities for handling database context in auth='none' endpoints
where Odoo doesn't automatically determine the database.
"""

import logging
from contextlib import contextmanager, nullcontext

from odoo import api, http, SUPERUSER_ID
from odoo.http import request
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)


def get_db_from_request(required=False, path_db=None):
    """
    Extract database name from request or path parameter.

    Priority:
    1. path_db parameter (from URL path like /<db>/mcp)
    2. request.db (already set by Odoo from session)
    3. Monodb auto-detection (if only one database exists)

    :param required: If True, return error when db cannot be determined
    :param path_db: Database name from URL path (e.g., from /<db>/mcp route)
    :return: Tuple of (db_name, error_dict) - error_dict is None on success
    """
    # Path parameter takes priority (multi-db path-based routing)
    if path_db and path_db != 'mcp':
        try:
            available_dbs = http.db_list(force=True)
        except Exception:
            available_dbs = []
        if path_db not in available_dbs:
            return None, {
                'error': 'invalid_request',
                'error_description': f'Database "{path_db}" not available',
            }
        return path_db, None

    # Check if Odoo already resolved the database (session)
    if hasattr(request, 'db') and request.db:
        return request.db, None

    # Auto-detect in monodb mode
    try:
        all_dbs = http.db_list(force=True)
    except Exception as e:
        _logger.warning("Failed to list databases: %s", e)
        all_dbs = []

    if len(all_dbs) == 1:
        return all_dbs[0], None

    if required:
        if len(all_dbs) == 0:
            return None, {
                'error': 'server_error',
                'error_description': 'No database available',
            }
        return None, {
            'error': 'invalid_request',
            'error_description': 'Database required. Use /<database>/mcp endpoint.',
        }

    return None, None


def has_request_env():
    """
    Check if request.env is available and usable.

    When True, you can use request.env directly and request.update_env()
    for user switching. This preserves proper Odoo request lifecycle
    including post-request hooks (message tracking, etc).

    :return: True if request.env is available
    """
    return hasattr(request, 'env') and request.env is not None


def is_multi_db():
    """
    Check if the Odoo instance has multiple databases.

    :return: True if more than one database exists
    """
    try:
        all_dbs = http.db_list(force=True)
        return len(all_dbs) > 1
    except Exception:
        return False


def get_current_db():
    """
    Get the current database name.

    :return: Database name or None
    """
    if hasattr(request, 'db') and request.db:
        return request.db
    if hasattr(request, 'env') and request.env:
        return request.env.cr.dbname
    return None


@contextmanager
def with_db_env(db):
    """
    Context manager to get an environment for a specific database.

    WARNING: Prefer using get_env() instead, which handles both cases automatically.
    Only use this directly when you need explicit control over the database connection.

    For compatibility with third-party modules that access request.env
    (like auditlog), this context manager temporarily sets up request.env
    when the request object exists but doesn't have an environment.
    This happens in auth='none' endpoints in multi-db environments.

    :param db: Database name
    :yields: Odoo environment for the specified database
    """
    registry = Registry(db)
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})

        # Set up request.env for third-party module compatibility
        need_request_env_setup = request and not has_request_env()
        orig_env = None

        if need_request_env_setup:
            orig_env = getattr(request, 'env', None)
            request.env = env

        try:
            yield env
            cr.commit()
        except Exception:
            cr.rollback()
            raise
        finally:
            if need_request_env_setup:
                request.env = orig_env


def get_env(db=None):
    """
    Get an environment for the current request, handling both cases:
    - When request.env is available (normal Odoo request lifecycle)
    - When request.env is unavailable (auth='none' with multi-db)

    This eliminates the need for repeated if/else blocks throughout the code.

    Usage:
        with get_env(db) as env:
            config = env['mcp.config'].sudo().get_config()
            base_url = get_base_url(env)

    :param db: Database name (required when request.env unavailable)
    :yields: Odoo environment
    :raises ValueError: If db is required but not provided
    """
    if has_request_env():
        # nullcontext wraps request.env as a no-op context manager
        return nullcontext(request.env)
    else:
        if not db:
            raise ValueError("Database name required when request.env is unavailable")
        return with_db_env(db)


def get_base_url(env):
    """
    Get the web.base.url system parameter.

    :param env: Odoo environment
    :return: Base URL string (empty string if not configured)
    """
    return env['ir.config_parameter'].sudo().get_param('web.base.url', '')


def get_base_url_or_host(db=None):
    """
    Get the base URL, with fallback to request host URL.

    This is useful for well-known endpoints that may be called before
    a database is determined.

    Priority:
    1. From request.env if available
    2. From database if db is provided
    3. From request host URL as fallback

    :param db: Optional database name
    :return: Base URL string
    """
    if has_request_env():
        return get_base_url(request.env)
    elif db:
        with with_db_env(db) as env:
            return get_base_url(env)
    else:
        # Fallback: use request host URL
        return request.httprequest.host_url.rstrip('/')
