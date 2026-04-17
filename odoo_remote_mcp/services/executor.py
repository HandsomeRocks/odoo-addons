# -*- coding: utf-8 -*-
"""
MCP Safe Code Executor Service

Provides secure execution of Python/ORM code using Odoo's safe_eval.
"""

import base64
import hashlib
import hmac
import html
import itertools
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime as dt

from odoo import fields
import requests
from pytz import timezone

from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tools.float_utils import float_compare, float_is_zero, float_round

from odoo.tools.func import lazy
from odoo.tools.misc import ReadonlyDict
from odoo.tools.safe_eval import safe_eval, datetime, dateutil, json, time, wrap_module

# Wrap modules for safe_eval (whitelist allowed attributes)
_safe_requests = wrap_module(requests, ['get', 'post', 'put', 'delete', 'patch', 'head', 'Session'])
_safe_re = wrap_module(re, ['match', 'search', 'findall', 'sub', 'split', 'compile', 'escape', 'fullmatch',
                            'IGNORECASE', 'MULTILINE', 'DOTALL'])
_safe_hashlib = wrap_module(hashlib, ['md5', 'sha1', 'sha256', 'sha512', 'sha224', 'sha384', 'new'])
_safe_hmac = wrap_module(hmac, ['new', 'compare_digest', 'digest'])
_safe_base64 = wrap_module(base64, ['b64encode', 'b64decode', 'encodebytes', 'decodebytes',
                                     'urlsafe_b64encode', 'urlsafe_b64decode'])
_safe_math = wrap_module(math, ['ceil', 'floor', 'sqrt', 'log', 'log10', 'pow', 'exp',
                                 'pi', 'e', 'inf', 'fabs', 'gcd', 'isnan', 'isinf'])
_safe_itertools = wrap_module(itertools, ['chain', 'groupby', 'product', 'permutations', 'combinations',
                                           'islice', 'count', 'cycle', 'repeat', 'starmap', 'accumulate'])
_safe_html = wrap_module(html, ['escape', 'unescape'])

_logger = logging.getLogger(__name__)


def json_default(obj):
    """
    JSON serialization helper for common Odoo types.

    Note: This is a v17 compatibility function. In v18, this is provided by
    odoo.tools.json.json_default.
    """
    if isinstance(obj, dt):
        return fields.Datetime.to_string(obj)
    if isinstance(obj, date):
        return fields.Date.to_string(obj)
    if isinstance(obj, lazy):
        return obj._value
    if isinstance(obj, ReadonlyDict):
        return dict(obj)
    if isinstance(obj, bytes):
        return obj.decode()
    return str(obj)


class MCPExecutor:
    """
    Safe code executor for MCP.

    Uses Odoo's safe_eval - imports are not allowed.
    """

    @classmethod
    def execute(cls, env, code, extra_locals=None):
        """
        Execute Python code safely with ORM access.

        :param env: Odoo environment
        :param code: Python code to execute
        :param extra_locals: Optional dict of additional local variables
        :return: Value of `result` variable, serialized for JSON
        """
        if not code or not code.strip():
            raise ValueError("No code provided")

        eval_context = {
            # Odoo environment
            'env': env,
            'user': env.user,
            'uid': env.uid,  # User ID (for compatibility)
            'ref': env.ref,  # Shortcut for XML ID lookups
            # ORM helpers
            'Command': Command,  # For One2many/Many2many operations
            'UserError': UserError,  # User-friendly errors
            'ValidationError': ValidationError,  # Validation errors
            # Date/time utilities
            'datetime': datetime,
            'date': datetime.date,
            'time': time,
            'timedelta': datetime.timedelta,
            'dateutil': dateutil,
            'relativedelta': dateutil.relativedelta.relativedelta,
            'timezone': timezone,  # Timezone operations (e.g., timezone('US/Eastern'))
            # Serialization
            'json': json,
            # Binary encoding
            'base64': _safe_base64,
            'b64encode': base64.b64encode,
            'b64decode': base64.b64decode,
            # Float precision utilities
            'float_round': float_round,
            'float_compare': float_compare,
            'float_is_zero': float_is_zero,
            # Collection utilities
            'defaultdict': defaultdict,
            'Counter': Counter,
            'itertools': _safe_itertools,
            # HTTP requests
            'requests': _safe_requests,
            # Regex
            're': _safe_re,
            # Cryptography / signing
            'hashlib': _safe_hashlib,
            'hmac': _safe_hmac,
            # Math
            'math': _safe_math,
            # HTML utilities
            'html': _safe_html,
            # Debugging (server-side only)
            'logger': _logger,
            # Output variable
            'result': None,
        }

        # Add any extra local variables (e.g., route_params, query_params)
        if extra_locals:
            eval_context.update(extra_locals)

        try:
            # Pass eval_context as both globals and locals so variable assignments
            # are written back to the same dict we can access
            safe_eval(code, eval_context, eval_context, mode='exec', nocopy=True)
            return cls._serialize_result(eval_context.get('result'))
        except Exception as e:
            _logger.warning("MCP code execution failed: %s", e)
            raise ValueError(f"Execution error: {e}")

    @classmethod
    def _serialize_result(cls, value, depth=0, max_depth=10):
        """
        Convert result to JSON-serializable format.

        Handles recordsets, datetime, and other common types.
        """
        if depth > max_depth:
            return str(value)

        # None and basic JSON types
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        # Odoo recordsets
        if hasattr(value, '_name') and hasattr(value, 'ids'):
            return cls._serialize_recordset(value)

        # Collections
        if isinstance(value, dict):
            return {
                str(k): cls._serialize_result(v, depth + 1, max_depth)
                for k, v in list(value.items())[:1000]
            }

        if isinstance(value, (list, tuple)):
            return [cls._serialize_result(v, depth + 1, max_depth) for v in value[:1000]]

        if isinstance(value, (set, frozenset)):
            return [cls._serialize_result(v, depth + 1, max_depth) for v in list(value)[:1000]]

        # Use Odoo's json_default for datetime, bytes, etc.
        try:
            return json_default(value)
        except Exception:
            return str(value)

    @classmethod
    def _serialize_recordset(cls, records):
        """Convert recordset to serializable dict with IDs and display names."""
        if len(records) == 0:
            return {'_model': records._name, 'ids': [], 'count': 0}

        try:
            name_data = {r.id: r.display_name for r in records}
        except Exception:
            name_data = {r.id: str(r.id) for r in records}

        return {
            '_model': records._name,
            'ids': records.ids,
            'count': len(records),
            'records': [
                {'id': r.id, 'display_name': name_data.get(r.id, str(r.id))}
                for r in records[:1000]
            ],
            'truncated': len(records) > 1000,
        }
