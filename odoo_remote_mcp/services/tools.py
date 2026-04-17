# -*- coding: utf-8 -*-
"""
MCP Tools Service

Implements the tool registry and tool implementations for MCP.
Tools provide the actual functionality that AI agents can call.
"""

import base64
import fnmatch
import glob as glob_module
import json
import logging
import os
import re
import requests
import shutil
import time
import traceback
from collections import defaultdict

from lxml import etree

from odoo.addons import __path__ as __addons_path__
from odoo.exceptions import AccessError, ValidationError, UserError

from .binary_utils import fetch_field_resource_content
from .executor import json_default


class MCPToolRegistry:
    """
    Registry for MCP tools.

    Tools are registered with their metadata and implementations.
    """

    # Tool definitions
    TOOLS = {
        'list_models': {
            'name': 'list_models',
            'description': 'List accessible Odoo models. Use pattern/filter for efficient discovery instead of listing all. IMPORTANT: Results are paginated. Check "total" vs "returned" in response - if returned < total, use "offset" parameter to fetch more.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'pattern': {
                        'type': 'string',
                        'description': 'Regex pattern to match model names (e.g., "^sale\\.", "partner", "stock.*move")',
                    },
                    'filter': {
                        'type': 'string',
                        'description': 'Simple substring filter for model name or description (case-insensitive)',
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Maximum models to return (default: 50, max: 200)',
                        'default': 50,
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Number of models to skip for pagination',
                        'default': 0,
                    },
                },
                'required': [],
            },
            'required_scope': 'odoo.read',
        },
        'get_model_schema': {
            'name': 'get_model_schema',
            'description': 'Get field definitions for an Odoo model. Use filters to reduce context size.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The technical name of the model (e.g., res.partner)',
                    },
                    'field_names': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Specific field names to return (omit for all fields)',
                    },
                    'field_types': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Filter by field types: char, text, integer, float, boolean, date, datetime, many2one, one2many, many2many, selection, binary, html, monetary',
                    },
                    'stored_only': {
                        'type': 'boolean',
                        'description': 'Only return stored (database) fields, exclude computed fields. Default true.',
                        'default': True,
                    },
                    'required_only': {
                        'type': 'boolean',
                        'description': 'Only return required fields',
                        'default': False,
                    },
                    'include_relational': {
                        'type': 'boolean',
                        'description': 'Include relational fields (many2one, one2many, many2many). Default true.',
                        'default': True,
                    },
                    'no_default': {
                        'type': 'boolean',
                        'description': 'Only return fields without default values (useful for dummy data generation). Set to false to see all fields.',
                        'default': True,
                    },
                },
                'required': ['model', 'stored_only', 'required_only', 'include_relational', 'no_default'],
            },
            'required_scope': 'odoo.read',
        },
        'search_read': {
            'name': 'search_read',
            'description': 'Search for records and return specified fields. Binary/image fields (e.g., datas, image_1920) are automatically base64-encoded and returned as separate image/resource content - no manual decoding needed. IMPORTANT: Results are paginated. Check "total" vs "count" in response - if count < total, use "offset" parameter to fetch more.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model to search (e.g., res.partner)',
                    },
                    'domain': {
                        'type': 'array',
                        'items': {},
                        'description': 'Odoo domain filter (e.g., [[\"is_company\", \"=\", true]])',
                        'default': [],
                    },
                    'fields': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Fields to return (empty = all fields)',
                        'default': [],
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Maximum records to return',
                        'default': 100,
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Number of records to skip',
                        'default': 0,
                    },
                    'order': {
                        'type': 'string',
                        'description': 'Sort order (e.g., \"name asc, id desc\")',
                    },
                },
                'required': ['model', 'domain', 'fields', 'limit'],
            },
            'required_scope': 'odoo.read',
        },
        'read_record': {
            'name': 'read_record',
            'description': 'Read a single record by ID. Binary/image fields are automatically base64-encoded and returned as separate image/resource content - no manual decoding needed.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model name',
                    },
                    'id': {
                        'type': 'integer',
                        'description': 'The record ID',
                    },
                    'fields': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Fields to return (empty array = all fields)',
                        'default': [],
                    },
                },
                'required': ['model', 'id', 'fields'],
            },
            'required_scope': 'odoo.read',
        },
        'read_group': {
            'name': 'read_group',
            'description': 'Aggregate and group records using GROUP BY with SUM, AVG, COUNT, MIN, MAX, etc. Can use for data aggregations when execute_orm tool is not available. Returns: {model, count, offset, groups} where each group contains groupby values, aggregate values, and __extra_domain (a domain filter you can pass to search_read to fetch individual records in that group).',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model to query (e.g., sale.order)',
                    },
                    'domain': {
                        'type': 'array',
                        'items': {},
                        'description': 'Odoo domain filter (e.g., [["state", "=", "sale"]])',
                        'default': [],
                    },
                    'groupby': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Fields to group by. For date/datetime fields use "field:granularity" (e.g., "date_order:month", "create_date:quarter"). Granularities: day, week, month, quarter, year. Empty array returns a single group with totals across all matching records.',
                        'default': [],
                    },
                    'fields': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Aggregations as "field:function" or "__count" for total record count. Functions: sum, avg, count, count_distinct, min, max, array_agg. Also supports "name:function(field)" for custom result names. Examples: ["amount_total:sum", "__count", "partner_id:count_distinct"]',
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Maximum number of groups to return',
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Number of groups to skip',
                        'default': 0,
                    },
                    'orderby': {
                        'type': 'string',
                        'description': 'Sort order (e.g., "amount_total desc", "date_order:month asc")',
                    },
                },
                'required': ['model', 'domain', 'fields'],
            },
            'required_scope': 'odoo.read',
        },
        'create_record': {
            'name': 'create_record',
            'description': 'Create one or more records in an Odoo model. Supports bulk creation.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model name (e.g., res.partner)',
                    },
                    'values': {
                        'type': 'array',
                        'items': {'type': 'object'},
                        'description': 'Array of field value objects. Each object creates one record.',
                    },
                },
                'required': ['model', 'values'],
            },
            'required_scope': 'odoo.write',
        },
        'update_record': {
            'name': 'update_record',
            'description': 'Update one or more records. Supports bulk updates with same or different values per record.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model name (e.g., res.partner)',
                    },
                    'ids': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                        'description': 'Record IDs to update. Use with "values" to apply same values to all.',
                    },
                    'values': {
                        'type': 'object',
                        'description': 'Field values to apply to all records in "ids". Use with "ids".',
                    },
                    'updates': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'integer'},
                                'values': {'type': 'object'},
                            },
                            'required': ['id', 'values'],
                        },
                        'description': 'Array of {id, values} objects for different values per record. Mutually exclusive with ids+values.',
                    },
                },
                'required': ['model'],
            },
            'required_scope': 'odoo.write',
        },
        'delete_record': {
            'name': 'delete_record',
            'description': 'Delete one or more records. Supports bulk deletion.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model name (e.g., res.partner)',
                    },
                    'ids': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                        'description': 'Record IDs to delete',
                    },
                },
                'required': ['model', 'ids'],
            },
            'required_scope': 'odoo.write',
        },
        'execute_method': {
            'name': 'execute_method',
            'description': 'Call a method on Odoo records (e.g., action_confirm, action_cancel, message_post, button_*, copy). For CRUD use dedicated create/update/delete tools.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The model name (e.g., sale.order)',
                    },
                    'method': {
                        'type': 'string',
                        'description': 'Method name to call (e.g., action_confirm)',
                    },
                    'ids': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                        'description': 'Record IDs to call the method on',
                        'default': [],
                    },
                    'args': {
                        'type': 'array',
                        'items': {},
                        'description': 'Positional arguments for the method',
                        'default': [],
                    },
                    'kwargs': {
                        'type': 'object',
                        'description': 'Keyword arguments for the method',
                        'default': {},
                    },
                },
                'required': ['model', 'method'],
            },
            'required_scope': 'odoo.write',
        },
        'execute_orm': {
            'name': 'execute_orm',
            'description': """Execute Python/ORM code in a sandboxed environment using Odoo's safe_eval.

IMPORTS FORBIDDEN - Do not use 'import' or 'from' statements. All utilities are pre-imported.

OUTPUT: Assign to `result` variable. Auto-serialized to JSON:
- Recordsets → {_model, ids, count, records: [{id, display_name}, ...]}
- datetime → ISO string, dicts/lists → recursively serialized

PRE-IMPORTED (use directly):
- env: Odoo Environment, access models via env['model.name']
- user: Current user (env.user), uid: User ID
- ref(xmlid): XML ID lookup
- Command: x2many operations (.create, .update, .delete, .link, .unlink, .set, .clear)
- UserError, ValidationError: Exception classes
- date.today(), datetime.datetime.now(), timedelta(days=N), relativedelta(months=N): Date/time (NOTE: datetime is the MODULE, use datetime.datetime.now() for current datetime)
- base64: Full module (base64.b64encode, base64.b64decode, base64.encodebytes, etc.)
- b64encode, b64decode: Shortcut binary encoding/decoding
- defaultdict, Counter: Collection utilities for grouping and counting
- itertools: Iteration utilities (itertools.groupby, chain, product, etc.)
- json, logger: Serialization and logging
- float_round, float_compare, float_is_zero: Numeric precision
- requests: HTTP library (requests.get, requests.post, requests.put, requests.delete, requests.patch, requests.head)
- re: Regular expressions (re.match, re.search, re.findall, re.sub, re.compile, re.split)
- hashlib: Hashing (hashlib.md5, hashlib.sha1, hashlib.sha256, hashlib.sha512)
- hmac: HMAC signing for webhook signature verification
- math: Math functions (math.ceil, math.floor, math.sqrt, math.log, etc.)
- html: HTML utilities (html.escape, html.unescape)
- Builtins: len, sum, min, max, sorted, filter, map, range, enumerate, zip, any, all, bool, int, float, str, list, dict, set, tuple, round, abs

WHEN TO USE: Complex aggregations, multi-step logic, atomic operations, or when multiple tool calls would be inefficient.

EXAMPLES:
# Group sales by month
grouped = defaultdict(list)
for o in env['sale.order'].search_read([('state', '=', 'sale')], ['name', 'date_order', 'amount_total']):
    grouped[o['date_order'].strftime('%Y-%m')].append(o)
result = {k: {'count': len(v), 'total': sum(x['amount_total'] for x in v)} for k, v in grouped.items()}

# Count by category
result = dict(Counter(p.categ_id.name for p in env['product.product'].search([])))

# Simple search
result = env['res.partner'].search([('is_company', '=', True)], limit=5)""",
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'code': {
                        'type': 'string',
                        'description': 'Python code to execute. Assign output to `result` variable.',
                    },
                },
                'required': ['code'],
            },
            'required_scope': 'odoo.execute',
        },
        'code_search': {
            'name': 'code_search',
            'description': 'Search Odoo addon source code. Use "pattern" to search within file contents, "file_pattern" to filter which files to search. Returns file paths or matching lines. Use output_mode="files_with_matches" for discovery, then code_read to examine specific files. IMPORTANT: Results are subject to server-configured limits. Always compare "returned" against the total in response: for files_with_matches check "total_files", for content check "total_matches". If returned < total, use "offset" parameter to paginate.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'pattern': {
                        'type': 'string',
                        'description': 'Regex pattern to search for in file contents (e.g., "def create\\(", "class.*Model", ".*" for any content)',
                    },
                    'module': {
                        'type': 'string',
                        'description': 'Limit search to specific addon module name (e.g., "sale", "stock"). Omit to search all addons.',
                    },
                    'file_pattern': {
                        'type': 'string',
                        'description': 'Glob pattern to filter which files to search (e.g., "**/*" for all files, "**/*.py" for Python only, "**/*.xml" for XML only)',
                        'default': '**/*',
                    },
                    'case_sensitive': {
                        'type': 'boolean',
                        'description': 'Enable case-sensitive matching (default: false)',
                        'default': False,
                    },
                    'output_mode': {
                        'type': 'string',
                        'enum': ['files_with_matches', 'content', 'count'],
                        'description': 'files_with_matches=file paths only (fast), content=matching lines with line numbers, count=match counts grouped by module',
                        'default': 'files_with_matches',
                    },
                    'context_before': {
                        'type': 'integer',
                        'description': 'Lines before each match (requires output_mode=content)',
                        'default': 0,
                    },
                    'context_after': {
                        'type': 'integer',
                        'description': 'Lines after each match (requires output_mode=content)',
                        'default': 0,
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Maximum items to return per page (files for files_with_matches, lines for content). Ignored for count mode. Capped by server config.',
                        'default': 50,
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Skip first N results for pagination. If previous call returned offset=0 and returned=100 but total_matches=250, use offset=100 to get next batch.',
                        'default': 0,
                    },
                },
                'required': ['pattern', 'output_mode', 'limit'],
            },
            'required_scope': 'odoo.read',
        },
        'code_read': {
            'name': 'code_read',
            'description': 'Read source code from an addon file. Use after code_search to examine files. For context around a match at line N, use offset=N-10 and limit=30 to get 10 lines before and 20 after. IMPORTANT: Lines returned are subject to server-configured limits (default 500). Always check "total_lines" vs "end_line" in the response - if end_line < total_lines, the file was only partially read. Use "offset" parameter to continue reading from where you left off.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'file_path': {
                        'type': 'string',
                        'description': 'Relative path from addons root, as returned by code_search (e.g., "sale/models/sale_order.py")',
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Line number to start reading from (1-indexed). To continue reading a partially-read file, use offset=end_line+1 from previous response. To read around line N, use offset=max(1, N-10).',
                        'default': 1,
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Number of lines to read. Default reads up to max configured limit.',
                    },
                },
                'required': ['file_path'],
            },
            'required_scope': 'odoo.read',
        },
        'search_webapp_code': {
            'name': 'search_webapp_code',
            'description': """Search code within a webapp's components, pages, component files, endpoints, and styles.
Use to find exact code locations before applying patches via manage_webapp.
Searches all code fields by default; use "scope" to limit which fields are searched.

WHEN TO USE THIS TOOL vs search_read/read_record:
- Use search_webapp_code for: targeted pattern searches before applying small patches, locating specific functions/variables
- Use search_read/read_record to READ FULL CODE when you need to understand overall structure, plan large changes, or review an entire file.
  This avoids multiple fragmented searches. Workflow: search_read to discover what exists → read_record for full code.
  MODELS AND CODE FIELDS:
  - mcp.webapp: shared_components, shared_styles, data_code, global_state_code, custom_imports
  - mcp.webapp.page: component_code, data_code (filter by webapp_id to list pages; page also has: name, route_path, page_title)
  - mcp.webapp.page.file: code (filter by page_id to list files; file also has: name, sequence)
  - mcp.webapp.endpoint: handler_code (filter by webapp_id to list endpoints; endpoint also has: name, endpoint_path, method)
  Example: search_read on 'mcp.webapp.page.file' with domain [('page_id','=',187)], fields ['name','code','sequence'] returns all component files for that page in one call.

SCOPE MAPPING (what each scope value searches):
- shared_components: webapp.shared_components (JSX)
- shared_styles: webapp.shared_styles (CSS)
- data_code: webapp.data_code (Python, webapp-level only)
- global_state_code: webapp.global_state_code (JavaScript)
- custom_imports: webapp.custom_imports (JavaScript)
- pages: page.component_code (JSX) + page.data_code (Python)
- page_files: page_file.code (JavaScript/JSX component files)
- endpoints: endpoint.handler_code (Python)

RESPONSE FORMAT:
Each match includes: location (webapp/page/page_file/endpoint), field name, line number, match text.
Page matches include page_id + page_name. File matches add file_id + file_name. Endpoint matches add endpoint_id + endpoint_name.
Use context_before/context_after for surrounding lines (returned as "before"/"after" arrays).

IMPORTANT: Results are paginated. Check "total_matches" vs "returned" in response - if returned < total_matches, use "offset" parameter to fetch more. Maximum matches per request is subject to server-configured limits (code_search_max_matches).""",
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'webapp_id': {
                        'type': 'integer',
                        'description': 'ID of the webapp to search within.',
                    },
                    'pattern': {
                        'type': 'string',
                        'description': 'Regex pattern to search for (e.g., "ENEMY_SPEED", "function\\s+check", "useState\\(").',
                    },
                    'scope': {
                        'type': 'array',
                        'items': {
                            'type': 'string',
                            'enum': [
                                'shared_components', 'shared_styles', 'data_code',
                                'global_state_code', 'custom_imports',
                                'pages', 'page_files', 'endpoints',
                            ],
                        },
                        'description': 'Limit search to specific code locations. Omit to search all. See tool description for scope mapping details.',
                    },
                    'case_sensitive': {
                        'type': 'boolean',
                        'description': 'Enable case-sensitive matching (default: false).',
                        'default': False,
                    },
                    'context_before': {
                        'type': 'integer',
                        'description': 'Number of lines to include before each match.',
                        'default': 0,
                    },
                    'context_after': {
                        'type': 'integer',
                        'description': 'Number of lines to include after each match.',
                        'default': 0,
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Maximum matches to return (default: 50, max: 200).',
                        'default': 50,
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Skip first N matches for pagination.',
                        'default': 0,
                    },
                },
                'required': ['webapp_id', 'pattern'],
            },
            'required_scope': 'odoo.read',
        },
        'search_module_code': {
            'name': 'search_module_code',
            'description': """Search code within a module's files (XML, Python, CSV, JS, CSS, etc.).
Use to find exact code locations before applying content_patches via manage_module.
Searches all files in the module by default; use "file_pattern" to filter which files are searched.

RESPONSE FORMAT:
Each match includes: file_id, file_path, line number, match text.
Use context_before/context_after for surrounding lines (returned as "before"/"after" arrays).

IMPORTANT: Results are paginated. Check "total_matches" vs "returned" in response - if returned < total_matches, use "offset" parameter to fetch more. Maximum matches per request is subject to server-configured limits (code_search_max_matches, default 500).""",
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'module_id': {
                        'type': 'integer',
                        'description': 'ID of the module to search within.',
                    },
                    'pattern': {
                        'type': 'string',
                        'description': 'Regex pattern to search for (e.g., "x_priority", "field name=", "<record.*model=").',
                    },
                    'file_pattern': {
                        'type': 'string',
                        'description': 'Glob pattern to filter files (e.g., "*.xml", "*.py", "views/*.xml"). Omit to search all files.',
                    },
                    'case_sensitive': {
                        'type': 'boolean',
                        'description': 'Enable case-sensitive matching (default: false).',
                        'default': False,
                    },
                    'context_before': {
                        'type': 'integer',
                        'description': 'Number of lines to include before each match.',
                        'default': 0,
                    },
                    'context_after': {
                        'type': 'integer',
                        'description': 'Number of lines to include after each match.',
                        'default': 0,
                    },
                    'limit': {
                        'type': 'integer',
                        'description': 'Maximum matches to return (default: 50, max: 200).',
                        'default': 50,
                    },
                    'offset': {
                        'type': 'integer',
                        'description': 'Skip first N matches for pagination.',
                        'default': 0,
                    },
                },
                'required': ['module_id', 'pattern'],
            },
            'required_scope': 'odoo.read',
        },
        'create_echart': {
            'name': 'create_echart',
            'description': '''Create a persistent ECharts dashboard in Odoo (uses ECharts 6.x).

Arguments:
- name: Dashboard title
- data_code: Python code that fetches data and assigns to `result` variable
- chart_options OR chart_panels (ONE IS REQUIRED): ECharts 6.x options JSON with $data placeholders
  - chart_options: Single chart (object)
  - chart_panels: Multi-panel dashboard (array of objects)

IMPORTANT: data_code follows the SAME RULES as execute_orm:
- IMPORTS FORBIDDEN - Do not use 'import' or 'from' statements
- Assign output to `result` variable
- CONTEXT: mcp_chart_id (int — this chart's database ID). To access this chart's record use env['mcp.echart'].browse(mcp_chart_id) — NEVER hardcode database IDs.
- PRE-IMPORTED: env, user, uid, ref, Command, UserError, ValidationError,
  date.today(), datetime.datetime.now(), timedelta, relativedelta, timezone (NOTE: datetime is the MODULE),
  base64, b64encode, b64decode, defaultdict, Counter, itertools, json, logger,
  float_round, float_compare, float_is_zero,
  requests, re, hashlib, hmac, math, html

The code is validated by executing it BEFORE the chart is created.
If the code fails, the chart will not be created and an error is returned.

chart_options use ECharts 6.x format:
- Options API: https://echarts.apache.org/en/option.html
- Examples Gallery: https://echarts.apache.org/examples/en/index.html

DATA PLACEHOLDERS (for chart_options):
Use "$data.xxx" placeholders in chart_options to inject values from your result dict.

Supported placeholder forms:
- "$data" — the entire result object
- "$data.field" — a top-level key from the result dict
- "$data.field.subfield" — nested dict access
- "$data.items.0.name" — array index access (0-based) followed by further traversal

If a path does not resolve (missing key, out-of-range index, wrong type), it becomes null.

RAW DATA IN JAVASCRIPT:
The `data` variable (your full result dict) is available in pre_init_js, post_init_js,
and inside __fn__: formatter functions. No need to embed it in chart_options.
  Example post_init_js: "data.records.forEach(r => console.log(r.name));"

JAVASCRIPT FUNCTIONS (__fn__: prefix):
Use __fn__: for ECharts callback properties like formatter, labelFormatter, etc.
The `data` variable is available inside these functions (e.g., data.total, data.labels).
Does NOT work for: series.data, graphic.text, xAxis.data - these expect static values.
Prepare all data arrays and display strings in Python data_code, not JavaScript.

Example with formatter function (correct usage):
{
  "tooltip": {
    "formatter": "__fn__:function(params) { return params.name + ': ' + params.value.toLocaleString(); }"
  },
  "series": [{
    "type": "pie",
    "label": {
      "formatter": "__fn__:function(params) { return params.name + '\\n' + params.percent.toFixed(1) + '%'; }"
    }
  }]
}

For simple cases, ECharts string templates still work without __fn__:
  formatter: "{b}: {c}" (tooltip), formatter: "{value}%" (axis labels)

DATA FORMATS BY CHART TYPE:
Different chart types require different data structures:

- BAR/LINE charts: Use separate arrays for xAxis categories and series values
  xAxis.data: ["Jan", "Feb", "Mar"]
  series.data: [100, 200, 150]

- PIE/FUNNEL charts: Use array of {name, value} objects
  series.data: [{"name": "Category A", "value": 100}, {"name": "Category B", "value": 200}]
  Python: [{'name': k, 'value': v} for k, v in data.items()]

- SCATTER charts: Use array of [x, y] coordinate pairs
  series.data: [[10, 20], [15, 30], [20, 25]]

- GAUGE charts: Use array with single {value} object
  series.data: [{"value": 75, "name": "Score"}]

TIPS:
- Use percentage-based positioning (\"top\": \"10%\") for responsive layouts
- Escape backslashes in function strings (use \\n for newline)
- PIE CHARTS: Always set radius to control size (e.g., \"radius\": \"55%\" or \"radius\": [\"40%\", \"70%\"] for donut)
  Example: \"series\": [{\"type\": \"pie\", \"radius\": \"55%\", \"center\": [\"50%\", \"55%\"], \"data\": \"$data.items\"}]

ADVANCED FEATURES (optional arguments):

extension_urls: CDN URLs for ECharts extensions (one per line). Default includes popular extensions:
  - echarts-gl (3D charts, globe visualization, WebGL)
  - echarts-wordcloud (word cloud charts)
  - echarts-liquidfill (liquid fill gauges)
  - echarts-stat (statistical transforms, regression)
Only override if you need to add/remove specific extensions.

pre_init_js: JavaScript code that runs BEFORE chart.setOption(). Use for:
  - echarts.registerMap('mapName', geoJsonData) - for geographic maps
  - echarts.registerTheme('themeName', themeConfig) - for custom themes
  - echarts.registerTransform(ecStatTransform) - for data transforms
  Available variables: echarts, chartDom, data, options

post_init_js: JavaScript code that runs AFTER chart.setOption(). Use for:
  - Event handlers: chart.on('click', (params) => { ... })
  - Drill-down logic
  - Export buttons
  - External integrations
  Available variables: echarts, chart (initialized instance), chartDom, data, options

Example - Bar Chart (Sales by Month):

data_code:
```
sales = env['sale.order'].read_group(
    [('state', '=', 'sale')],
    ['amount_total:sum'],
    ['date_order:month']
)
result = {
    'months': [s['date_order:month'] for s in sales],
    'totals': [s['amount_total'] for s in sales]
}
```

chart_options:
{
  "title": {"text": "Sales by Month", "top": "3%", "left": "center"},
  "tooltip": {"trigger": "axis"},
  "grid": {"top": "15%", "bottom": "10%", "containLabel": true},
  "xAxis": {"type": "category", "data": "$data.months"},
  "yAxis": {"type": "value"},
  "series": [{"type": "bar", "data": "$data.totals"}]
}

Returns the created record ID, validation result, and a URL to view the chart.''',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'name': {
                        'type': 'string',
                        'description': 'Dashboard title',
                    },
                    'description': {
                        'type': 'string',
                        'description': 'Optional description of what this chart visualizes',
                    },
                    'data_code': {
                        'type': 'string',
                        'description': 'Python code that fetches data. Assign output to `result` variable. Context variable: mcp_chart_id (int — this chart\'s database ID). Use env[\'mcp.echart\'].browse(mcp_chart_id) to access the chart record — NEVER hardcode IDs.',
                    },
                    'chart_options': {
                        'type': 'object',
                        'description': 'REQUIRED if chart_panels not provided. ECharts options object for a single chart. Use $data placeholders to inject data.',
                    },
                    'chart_panels': {
                        'type': 'array',
                        'description': 'REQUIRED if chart_options not provided. Array of ECharts options for multi-panel dashboard (stacked vertically). Use for dashboards with multiple charts.',
                        'items': {'type': 'object'},
                    },
                    'share_with_all_users': {
                        'type': 'boolean',
                        'description': 'Make this chart visible to all MCP users',
                        'default': False,
                    },
                    'renderer': {
                        'type': 'string',
                        'enum': ['canvas', 'svg'],
                        'description': 'Renderer: canvas (default, better for large data/effects) or svg (better for mobile/memory)',
                        'default': 'canvas',
                    },
                    'media_queries': {
                        'type': 'array',
                        'description': 'Responsive breakpoints using ECharts native media query system',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'query': {
                                    'type': 'object',
                                    'description': 'Conditions: maxWidth, minWidth, maxHeight, minHeight (pixels)',
                                },
                                'option': {
                                    'type': 'object',
                                    'description': 'Chart option overrides when query matches',
                                },
                            },
                        },
                    },
                    'extension_urls': {
                        'type': 'string',
                        'description': 'CDN URLs for ECharts extensions (one per line). Default includes echarts-gl, wordcloud, liquidfill, stat. Only set to override defaults.',
                    },
                    'pre_init_js': {
                        'type': 'string',
                        'description': 'JavaScript code to run BEFORE chart.setOption(). Use for registerMap, registerTheme, registerTransform. Available vars: echarts, chartDom, data, baseOptions, mediaQueries',
                    },
                    'post_init_js': {
                        'type': 'string',
                        'description': 'JavaScript code to run AFTER chart.setOption(). Use for event handlers (chart.on), drill-down, exports. Available vars: echarts, chart, chartDom, data, baseOptions, mediaQueries',
                    },
                },
                'required': ['name', 'data_code'],  # Either chart_options or chart_panels must be provided
            },
            'required_scope': 'odoo.write',
        },
        'manage_webapp': {
            'name': 'manage_webapp',
            'description': '''Create and manage React 19 web applications with multi-page routing, data binding, API endpoints, and persistent user storage.

OPERATIONS (combine as needed):
- create_webapp / update_webapp: App-level settings (mutually exclusive, required for create_pages/create_endpoints)
- create_pages: Add new pages (requires create_webapp or update_webapp)
- update_pages / delete_page_ids: Modify/remove pages (can use alone - webapp derived from page)
- create_endpoints: Add new endpoints (requires create_webapp or update_webapp)
- update_endpoints / delete_endpoint_ids: Modify/remove endpoints (can use alone - webapp derived from endpoint)
- create_page_files: Add component files to existing pages (can use alone - webapp derived from page)
- update_page_files / delete_page_file_ids: Modify/remove component files (can use alone - webapp derived from file)
- upload_assets: Upload binary assets (images, audio, sprites) via URL or base64 data (requires create_webapp or update_webapp)
- delete_asset_ids: Remove assets by attachment ID (can use alone - webapp derived from asset)
- CODE PATCHES: Use *_patches fields (e.g., component_code_patches, code_patches) for surgical find/replace edits instead of rewriting entire code fields. Each patch: {find: "unique string", replace: "new string"}. Applied in order. Cannot combine with full field replacement.
  Use search_webapp_code tool first to find exact code locations and surrounding context before applying patches.

READING EXISTING CODE (use search_read / read_record tools):
Before modifying a webapp, you often need to read its existing code. Use search_read to discover what exists, then read_record for full code.
- Discover pages: search_read on 'mcp.webapp.page', domain [('webapp_id','=',ID)], fields ['id','name','route_path']
- Read full page code: read_record on 'mcp.webapp.page', id=PAGE_ID, fields ['component_code','data_code']
- Discover component files: search_read on 'mcp.webapp.page.file', domain [('page_id','=',PAGE_ID)], fields ['id','name','sequence']
- Read full file code: read_record on 'mcp.webapp.page.file', id=FILE_ID, fields ['code']
- Discover endpoints: search_read on 'mcp.webapp.endpoint', domain [('webapp_id','=',ID)], fields ['id','name','endpoint_path','method']
- Read full endpoint code: read_record on 'mcp.webapp.endpoint', id=ENDPOINT_ID, fields ['handler_code']
- Read app-level code: read_record on 'mcp.webapp', id=ID, fields ['shared_components','shared_styles','data_code','global_state_code','custom_imports']
TIP: For small/targeted edits, search_webapp_code + patches is most efficient. For understanding overall structure or planning large changes, reading the full code via read_record is faster than multiple fragmented searches.

ARCHITECTURE (loaded automatically):
- React 19 + ReactDOM 19 via esm.sh (https://esm.sh/react@19)
- React Router 6 via esm.sh (https://esm.sh/react-router-dom@6)
- Babel standalone for JSX (pinned version, loaded automatically)
- Tailwind CSS via CDN (https://cdn.tailwindcss.com) - enabled by default
- ESM import maps - add libraries via cdn_dependencies, then import via custom_imports
- IMPORTANT: For React-based libraries on esm.sh, use ?external=react,react-dom to avoid duplicate React instances (causes "Cannot read properties of null" errors)

URLS:
- App: /{slug} (bare slug, preferred) or /mcp/webapp/{id}
- Page data: /mcp/webapp/{id}/page/{page_id}/data
- API: /mcp/webapp/{id}/api/{endpoint_path}
- Storage: /mcp/webapp/{id}/storage/{key}
- Asset: /mcp/webapp/{id}/asset/{filename}

ACCESS CONTROL (via shared_group_ids/shared_user_ids):
- Public website (no login): Add 'Public' group to shared_group_ids
- Portal users: Add 'Portal' group to shared_group_ids
- Internal users: Add 'User' or specific groups to shared_group_ids
- Specific users: Add users to shared_user_ids

DATA CODE (Python - same rules as execute_orm):
*** NO IMPORTS ALLOWED - NEVER use 'import' or 'from' statements! ***
WRONG: from datetime import datetime / import json
RIGHT: datetime.datetime.now() / json.dumps() (already available)

- OUTPUT: Assign to `result` variable (auto-serialized to JSON)
- PRE-IMPORTED (use directly, no import needed):
  env, user, uid, ref(), Command, date, datetime (MODULE), timedelta, relativedelta,
  json, UserError, ValidationError, defaultdict, Counter, itertools, float_round,
  requests, re, base64, hashlib, hmac, math, itertools, html, logger
- DATE/TIME: Use datetime.datetime.now(), datetime.date.today(), timedelta(days=N)
- CONTEXT BY LOCATION (auto-injected variables):
  - Webapp data_code: mcp_webapp_id (int)
  - Page data_code: route_params dict, mcp_webapp_id (int), mcp_page_id (int)
  - Endpoint handler_code: query_params, body, route_params dicts, mcp_webapp_id (int), mcp_endpoint_id (int)
- API KEYS: Store third-party API keys (e.g. Anthropic, OpenAI) in the mcp.api.key model (MCP Server → API Keys).
  Look them up by name in endpoint handlers: env['mcp.api.key'].sudo().search([('name', '=', 'my-key')], limit=1).api_key
- *** NEVER HARDCODE DATABASE IDs OR SLUGS *** — use the mcp_ variables above to reference the current record.
  Hardcoded IDs/slugs break when migrating between databases (test → production) or renaming slugs.
  WRONG: env['mcp.webapp'].browse(6).global_state_code
  WRONG: env['mcp.webapp'].search([('slug','=','my-app')]).global_state_code
  RIGHT: env['mcp.webapp'].browse(mcp_webapp_id).global_state_code

COMPONENT CODE (JSX - React 19 functional components):
- PROPS: data (page data), routeParams, globalState, setGlobalState, initialData (app data), api, storage, user (current user info), asset (asset URL helper)
- PRE-IMPORTED REACT: useState, useEffect, useCallback, useMemo, useRef, useReducer, useId
- PRE-IMPORTED ROUTER: Link, NavLink, Navigate, Outlet, useParams, useNavigate, useLocation, useSearchParams
- API HELPER: api.get(path, params), api.post(path, body), api.put(path, body), api.delete(path)
- STORAGE HELPER (persistent data with localStorage cache):
  - storage.get(key, defaultValue) - get stored value (returns defaultValue if not found)
  - storage.set(key, value) - save value to server (cached locally for offline fallback)
  - storage.delete(key) - remove a key
  - storage.getAll() - get all stored data as object
  - storage.clear() - delete all stored data
  Logged-in users: server storage keyed by user_id (persists across sessions/devices, 5MB limit).
  Anonymous users: server storage keyed by session_id (persists within browser session, ~7 days).
  Both use localStorage as cache for offline fallback. Ideal for: game progress, user preferences, form drafts, high scores.
  SERVER-SIDE ACCESS: Storage is backed by the mcp.webapp.user.storage model (fields: webapp_id, user_id, data JSON).
  In endpoint handler_code, query across ALL users' storage for cross-user features (leaderboards, shared feeds, aggregate stats):
    records = env['mcp.webapp.user.storage'].sudo().search_read([('webapp_id', '=', mcp_webapp_id)], ['user_id', 'data'])
  Do NOT use ir.config_parameter or other models for user data — always use mcp.webapp.user.storage.
- USER CONTEXT: user object contains {id, name, email, login, is_public, is_portal, is_internal, is_system, company_id, company_name} - use for role-based UI, personalization, user-specific features
- ASSET HELPER: asset(filename) - returns URL for uploaded binary asset. Example: <img src={asset('player.png')} />. Upload assets via upload_assets operation.
- CUSTOM HOOK: useApp() returns {globalState, setGlobalState, initialData, api, storage, user, asset} - use inside function components as alternative to destructuring props
- SHARED: Components from shared_components are globally available. Unlike page components, shared component names are NOT auto-renamed — use the exact function name when referencing them in pages (e.g., define `function Nav()` → use `<Nav/>`).
- FORMAT: Return JSX directly (props like data, globalState, etc. are available) or define as function. Page component functions are automatically renamed to match the page name (e.g., page "Game" → component "GamePage"), so the function name you use in component_code does not matter.
- UNICODE: Use actual Unicode characters (→, •, ─, ✓, ✗) directly in code, NEVER \\uXXXX escape sequences. JSX text does not process JS Unicode escapes — they render as literal text (e.g., \\u2500 shows as "\\u2500" instead of "─"). Only exception: escapes inside JS expressions like {"\\u2588"} are OK since the JS engine processes them.

COMPONENT FILES (optional per page):
Split large component_code into multiple named code files for better organization.
Files are injected in the same script scope BEFORE the main component_code, ordered by sequence.
Use for: game engines, sprite/level data, utility functions, constants.
Files can reference earlier files; the main component can reference all files.
Create inline with page: include "component_files" array in create_pages items.
Manage standalone: use create_page_files, update_page_files (supports code_patches), delete_page_file_ids.
Only use component_files when component_code would exceed ~500 lines.

EXAMPLE - Game with persistent high score:
{
  "create_webapp": {
    "name": "Number Guessing Game",
    "global_state_code": "{ secretNumber: Math.floor(Math.random() * 100) + 1, attempts: 0, gameOver: false }"
  },
  "create_pages": [
    {
      "name": "Game",
      "route_path": "/",
      "component_code": "function GamePage({globalState, setGlobalState, storage}) {\\n  const [guess, setGuess] = useState('');\\n  const [message, setMessage] = useState('Guess a number 1-100');\\n  const [highScore, setHighScore] = useState(null);\\n\\n  useEffect(() => { storage.get('highScore', 999).then(setHighScore); }, []);\\n\\n  const handleGuess = async () => {\\n    const num = parseInt(guess);\\n    const newAttempts = globalState.attempts + 1;\\n    setGlobalState(s => ({...s, attempts: newAttempts}));\\n\\n    if (num === globalState.secretNumber) {\\n      setMessage(`Correct! You won in ${newAttempts} attempts!`);\\n      setGlobalState(s => ({...s, gameOver: true}));\\n      if (newAttempts < highScore) {\\n        await storage.set('highScore', newAttempts);\\n        setHighScore(newAttempts);\\n      }\\n    } else {\\n      setMessage(num < globalState.secretNumber ? 'Too low!' : 'Too high!');\\n    }\\n  };\\n\\n  const resetGame = () => {\\n    setGlobalState({ secretNumber: Math.floor(Math.random() * 100) + 1, attempts: 0, gameOver: false });\\n    setMessage('Guess a number 1-100');\\n    setGuess('');\\n  };\\n\\n  return (\\n    <div className='p-8 max-w-md mx-auto'>\\n      <h1 className='text-2xl font-bold mb-4'>Number Guessing Game</h1>\\n      <p className='mb-2'>High Score: {highScore === 999 ? 'None yet' : highScore + ' attempts'}</p>\\n      <p className='mb-4 text-lg'>{message}</p>\\n      <input type='number' value={guess} onChange={e => setGuess(e.target.value)} disabled={globalState.gameOver} className='border p-2 mr-2 rounded' placeholder='Your guess'/>\\n      <button onClick={handleGuess} disabled={globalState.gameOver} className='bg-blue-500 text-white px-4 py-2 rounded mr-2'>Guess</button>\\n      <button onClick={resetGame} className='bg-gray-500 text-white px-4 py-2 rounded'>New Game</button>\\n      <p className='mt-4 text-gray-600'>Attempts: {globalState.attempts}</p>\\n    </div>\\n  );\\n}"
    }
  ]
}

EXAMPLE - Dashboard with navigation and API:
{
  "create_webapp": {
    "name": "Sales Dashboard",
    "data_code": "result = {'company': env.company.name, 'user': env.user.name}",
    "global_state_code": "{ sidebarOpen: true }",
    "shared_components": "function Card({title, children}) { return <div className='bg-white rounded-lg shadow p-4'><h3 className='font-bold mb-2'>{title}</h3>{children}</div>; }\\nfunction Nav() { return <nav className='flex gap-4 p-4 bg-gray-100'><Link to='/'>Home</Link><Link to='/orders'>Orders</Link></nav>; }"
  },
  "create_pages": [
    {
      "name": "Home",
      "route_path": "/",
      "component_code": "function HomePage({data, user}) { return <div><Nav/><div className='p-4'><h1>Welcome, {user.name}!</h1><p>Company: {user.company_name}</p><p>Orders today: {data.order_count}</p>{user.is_system && <p className='text-red-500'>Admin access enabled</p>}</div></div>; }",
      "data_code": "today = datetime.date.today()\\nresult = {'order_count': env['sale.order'].search_count([('date_order', '>=', str(today))]), 'today': str(today)}"
    },
    {
      "name": "Orders",
      "route_path": "/orders",
      "component_code": "function OrdersPage({data, api}) {\\n  const [orders, setOrders] = useState(data.orders);\\n  const navigate = useNavigate();\\n  const refresh = async () => { const r = await api.get('orders'); setOrders(r); };\\n  return <div><Nav/><div className='p-4'><button onClick={refresh} className='bg-blue-500 text-white px-4 py-2 rounded'>Refresh</button><ul className='mt-4'>{orders.map(o => <li key={o.id} onClick={() => navigate(`/order/${o.id}`)} className='cursor-pointer hover:bg-gray-100 p-2'>{o.name} - {o.state}</li>)}</ul></div></div>;\\n}",
      "data_code": "result = {'orders': env['sale.order'].search_read([], ['name', 'state', 'amount_total'], limit=50)}"
    },
    {
      "name": "Order Detail",
      "route_path": "/order/:id",
      "component_code": "function OrderDetailPage({data, routeParams}) { return <div><Nav/><div className='p-4'><Card title={data.name}><p>Status: {data.state}</p><p>Total: {data.amount_total}</p><Link to='/orders' className='text-blue-500'>Back</Link></Card></div></div>; }",
      "data_code": "order = env['sale.order'].browse(int(route_params.get('id', 0)))\\nresult = order.read(['name', 'state', 'amount_total', 'partner_id'])[0] if order.exists() else {}"
    }
  ],
  "create_endpoints": [
    {"name": "List Orders", "endpoint_path": "orders", "method": "GET", "handler_code": "limit = int(query_params.get('limit', 50))\\nthis_month = datetime.date.today().replace(day=1)\\nresult = env['sale.order'].search_read([('date_order', '>=', this_month)], ['name', 'state', 'amount_total'], limit=limit)"},
    {"name": "Update Order", "endpoint_path": "orders/:id", "method": "PUT", "handler_code": "order = env['sale.order'].browse(int(route_params['id']))\\norder.write(body)\\nresult = {'success': True, 'id': order.id}"}
  ]
}
''',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    # === WEBAPP OPERATIONS ===
                    'create_webapp': {
                        'type': 'object',
                        'description': 'Create new webapp. Returns webapp_id for subsequent operations.',
                        'properties': {
                            'name': {'type': 'string', 'description': 'Application title (shown in browser tab)'},
                            'slug': {'type': 'string', 'description': 'Custom URL slug (e.g., "sales-dashboard" creates /sales-dashboard). Also accessible at /mcp/webapp/sales-dashboard. Lowercase, alphanumeric, hyphens only. Leave empty to use numeric ID.'},
                            'description': {'type': 'string', 'description': 'Internal description (not displayed in app)'},
                            'data_code': {'type': 'string', 'description': 'Safe_eval Python code run on app load (same rules/pre-imports as execute_orm). Assign to `result` variable (auto-serialized to JSON). Available to pages via `initialData` prop. Context: mcp_webapp_id (int — this webapp\'s database ID). Use env[\'mcp.webapp\'].browse(mcp_webapp_id) — NEVER hardcode IDs or slugs.'},
                            'global_state_code': {'type': 'string', 'description': 'JS object literal for React state. Access via globalState/setGlobalState props. Example: { user: null, theme: "dark" }'},
                            'shared_components': {'type': 'string', 'description': 'JSX functions available to all pages. Example: function Card({children}) { return <div>{children}</div>; }'},
                            'shared_styles': {'type': 'string', 'description': 'Global CSS rules applied to entire app'},
                            'router_mode': {'type': 'string', 'enum': ['hash', 'memory'], 'default': 'hash', 'description': 'hash=URL fragments (#/path, works everywhere), memory=no URL changes'},
                            'tailwind_enabled': {'type': 'boolean', 'default': True, 'description': 'Include Tailwind CSS CDN'},
                            'cdn_dependencies': {'type': 'string', 'description': 'JSON object mapping package names to ESM URLs — entries are added to the import map. MUST be valid JSON. Example: {"chart.js": "https://esm.sh/chart.js@4.4.1", "react-chartjs-2": "https://esm.sh/react-chartjs-2@5.2.0?external=react,react-dom&deps=chart.js@4.4.1"}. Use together with custom_imports to import the packages.'},
                            'custom_imports': {'type': 'string', 'description': 'ES module imports for libraries in cdn_dependencies. Example: import { Chart as ChartJS, CategoryScale, LinearScale, BarElement } from "chart.js"; import { Bar } from "react-chartjs-2"; ChartJS.register(CategoryScale, LinearScale, BarElement);'},
                            'track_viewcount': {'type': 'boolean', 'default': False, 'description': 'Enable view counting - increments view_count each time app is viewed'},
                            'pwa_enabled': {'type': 'boolean', 'default': False, 'description': 'Enable Progressive Web App (installable, fullscreen, home screen icon)'},
                            'pwa_display': {'type': 'string', 'enum': ['standalone', 'fullscreen'], 'default': 'standalone', 'description': 'PWA display mode: standalone (no browser bar) or fullscreen'},
                            'pwa_theme_color': {'type': 'string', 'default': '#714B67', 'description': 'PWA theme color (hex, e.g. #714B67)'},
                            'pwa_background_color': {'type': 'string', 'default': '#ffffff', 'description': 'PWA splash screen background color (hex)'},
                        },
                        'required': ['name'],
                    },
                    'update_webapp': {
                        'type': 'object',
                        'description': 'Update existing webapp. Only provided fields are changed.',
                        'properties': {
                            'webapp_id': {'type': 'integer', 'description': 'ID of webapp to update (required)'},
                            'name': {'type': 'string'},
                            'slug': {'type': 'string'},
                            'description': {'type': 'string'},
                            'data_code': {'type': 'string'},
                            'global_state_code': {'type': 'string'},
                            'shared_components': {'type': 'string'},
                            'shared_styles': {'type': 'string'},
                            'router_mode': {'type': 'string', 'enum': ['hash', 'memory']},
                            'tailwind_enabled': {'type': 'boolean'},
                            'cdn_dependencies': {'type': 'string'},
                            'custom_imports': {'type': 'string'},
                            'track_viewcount': {'type': 'boolean'},
                            'pwa_enabled': {'type': 'boolean'},
                            'pwa_display': {'type': 'string', 'enum': ['standalone', 'fullscreen']},
                            'pwa_theme_color': {'type': 'string'},
                            'pwa_background_color': {'type': 'string'},
                            'shared_components_patches': {
                                'type': 'array',
                                'description': 'Find/replace patches for shared_components. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full shared_components replacement.',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                        'replace': {'type': 'string', 'description': 'Replacement string'},
                                    },
                                    'required': ['find', 'replace'],
                                },
                            },
                            'shared_styles_patches': {
                                'type': 'array',
                                'description': 'Find/replace patches for shared_styles. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full shared_styles replacement.',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                        'replace': {'type': 'string', 'description': 'Replacement string'},
                                    },
                                    'required': ['find', 'replace'],
                                },
                            },
                            'data_code_patches': {
                                'type': 'array',
                                'description': 'Find/replace patches for data_code. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full data_code replacement.',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                        'replace': {'type': 'string', 'description': 'Replacement string'},
                                    },
                                    'required': ['find', 'replace'],
                                },
                            },
                        },
                        'required': ['webapp_id'],
                    },
                    # === PAGE OPERATIONS ===
                    'create_pages': {
                        'type': 'array',
                        'description': 'Add pages to webapp. Use with create_webapp or update_webapp.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string', 'description': 'Page name (used to generate component name)'},
                                'route_path': {'type': 'string', 'description': 'React Router path. Use :param for dynamic segments. Examples: "/", "/orders", "/order/:id"'},
                                'component_code': {'type': 'string', 'description': 'React component. Props: {data, routeParams, globalState, setGlobalState, initialData, api, storage}. Can be JSX or function.'},
                                'data_code': {'type': 'string', 'description': 'Safe_eval Python code for page data (same rules/pre-imports as execute_orm). Assign to `result` variable. Auto-injected variables: route_params (dict — dynamic path segments from route_path, e.g. "/order/:id" → route_params["id"]). Context: mcp_webapp_id (int — parent webapp ID), mcp_page_id (int — this page ID). Use env[\'mcp.webapp\'].browse(mcp_webapp_id) — NEVER hardcode IDs or slugs.'},
                                'page_title': {'type': 'string', 'description': 'Browser tab title when page is active'},
                                'sequence': {'type': 'integer', 'default': 10, 'description': 'Display order in page list'},
                                'component_files': {
                                    'type': 'array',
                                    'description': 'Optional code files injected before the main component_code. Use for large apps to split engine, data, utilities into separate files.',
                                    'items': {
                                        'type': 'object',
                                        'properties': {
                                            'name': {'type': 'string', 'description': 'File name (e.g. "engine.js", "sprites.js")'},
                                            'code': {'type': 'string', 'description': 'JavaScript/JSX code'},
                                            'sequence': {'type': 'integer', 'default': 10, 'description': 'Injection order (lower = first)'},
                                        },
                                        'required': ['name', 'code'],
                                    },
                                },
                            },
                            'required': ['name', 'route_path', 'component_code'],
                        },
                    },
                    'update_pages': {
                        'type': 'array',
                        'description': 'Modify existing pages. Only provided fields are updated.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'page_id': {'type': 'integer', 'description': 'ID of page to update (required)'},
                                'name': {'type': 'string'},
                                'route_path': {'type': 'string'},
                                'component_code': {'type': 'string'},
                                'data_code': {'type': 'string'},
                                'page_title': {'type': 'string'},
                                'sequence': {'type': 'integer'},
                                'component_code_patches': {
                                    'type': 'array',
                                    'description': 'Find/replace patches for component_code. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full component_code replacement.',
                                    'items': {
                                        'type': 'object',
                                        'properties': {
                                            'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                            'replace': {'type': 'string', 'description': 'Replacement string'},
                                        },
                                        'required': ['find', 'replace'],
                                    },
                                },
                                'data_code_patches': {
                                    'type': 'array',
                                    'description': 'Find/replace patches for data_code. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full data_code replacement.',
                                    'items': {
                                        'type': 'object',
                                        'properties': {
                                            'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                            'replace': {'type': 'string', 'description': 'Replacement string'},
                                        },
                                        'required': ['find', 'replace'],
                                    },
                                },
                            },
                            'required': ['page_id'],
                        },
                    },
                    'delete_page_ids': {
                        'type': 'array',
                        'description': 'Remove pages by ID',
                        'items': {'type': 'integer'},
                    },
                    # === PAGE FILE OPERATIONS ===
                    'create_page_files': {
                        'type': 'array',
                        'description': 'Add component files to existing pages. Can use alone (webapp derived from page).',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'page_id': {'type': 'integer', 'description': 'ID of page to add file to (required)'},
                                'name': {'type': 'string', 'description': 'File name (e.g. "engine.js", "sprites.js")'},
                                'code': {'type': 'string', 'description': 'JavaScript/JSX code'},
                                'sequence': {'type': 'integer', 'default': 10, 'description': 'Injection order (lower = first)'},
                            },
                            'required': ['page_id', 'name', 'code'],
                        },
                    },
                    'update_page_files': {
                        'type': 'array',
                        'description': 'Modify existing component files. Only provided fields are updated.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'file_id': {'type': 'integer', 'description': 'ID of file to update (required)'},
                                'name': {'type': 'string'},
                                'code': {'type': 'string'},
                                'sequence': {'type': 'integer'},
                                'code_patches': {
                                    'type': 'array',
                                    'description': 'Find/replace patches for code. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full code replacement.',
                                    'items': {
                                        'type': 'object',
                                        'properties': {
                                            'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                            'replace': {'type': 'string', 'description': 'Replacement string'},
                                        },
                                        'required': ['find', 'replace'],
                                    },
                                },
                            },
                            'required': ['file_id'],
                        },
                    },
                    'delete_page_file_ids': {
                        'type': 'array',
                        'description': 'Remove component files by ID',
                        'items': {'type': 'integer'},
                    },
                    # === ENDPOINT OPERATIONS ===
                    'create_endpoints': {
                        'type': 'array',
                        'description': 'Add API endpoints. Called via api.get/post/put/delete() in components.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string', 'description': 'Endpoint name for identification'},
                                'endpoint_path': {'type': 'string', 'description': 'Relative path (no leading /). Use :param for dynamic. Examples: "orders", "orders/:id"'},
                                'method': {'type': 'string', 'enum': ['GET', 'POST', 'PUT', 'DELETE'], 'default': 'GET'},
                                'handler_code': {'type': 'string', 'description': 'Safe_eval Python handler (same rules/pre-imports as execute_orm). Auto-injected variables: query_params (dict — URL query string params, e.g. ?limit=10 → query_params["limit"]="10"), body (dict — parsed JSON request body for POST/PUT), route_params (dict — dynamic path segments, e.g. endpoint_path "orders/:id" → route_params["id"]), request (Odoo HTTP request object — request.session.sid for session ID, request.httprequest.headers for HTTP headers, request.httprequest.remote_addr for client IP). Context: mcp_webapp_id (int — parent webapp ID), mcp_endpoint_id (int — this endpoint ID). NEVER hardcode IDs or slugs.'},
                            },
                            'required': ['name', 'endpoint_path', 'handler_code'],
                        },
                    },
                    'update_endpoints': {
                        'type': 'array',
                        'description': 'Modify existing endpoints. Only provided fields are updated.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'endpoint_id': {'type': 'integer', 'description': 'ID of endpoint to update (required)'},
                                'name': {'type': 'string'},
                                'endpoint_path': {'type': 'string'},
                                'method': {'type': 'string', 'enum': ['GET', 'POST', 'PUT', 'DELETE']},
                                'handler_code': {'type': 'string'},
                                'handler_code_patches': {
                                    'type': 'array',
                                    'description': 'Find/replace patches for handler_code. Each patch has find (unique string to locate) and replace (replacement string). Applied in order. Cannot combine with full handler_code replacement.',
                                    'items': {
                                        'type': 'object',
                                        'properties': {
                                            'find': {'type': 'string', 'description': 'Exact string to find (must appear exactly once)'},
                                            'replace': {'type': 'string', 'description': 'Replacement string'},
                                        },
                                        'required': ['find', 'replace'],
                                    },
                                },
                            },
                            'required': ['endpoint_id'],
                        },
                    },
                    'delete_endpoint_ids': {
                        'type': 'array',
                        'description': 'Remove endpoints by ID',
                        'items': {'type': 'integer'},
                    },
                    # === ASSET OPERATIONS ===
                    'upload_assets': {
                        'type': 'array',
                        'description': 'Upload binary assets (images, audio, sprites) to the webapp. Requires create_webapp or update_webapp. Assets are served via asset(filename) helper in components.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'filename': {'type': 'string', 'description': 'Asset filename (e.g., "player.png", "music.mp3"). Must be unique within the webapp.'},
                                'url': {'type': 'string', 'description': 'URL to download the asset from (preferred). Fetched server-side.'},
                                'data': {'type': 'string', 'description': 'Base64-encoded binary data (for AI-generated content). Use url instead when possible.'},
                                'mime_type': {'type': 'string', 'description': 'MIME type (e.g., "image/png"). Required with data, auto-detected from URL response.'},
                            },
                            'required': ['filename'],
                        },
                    },
                    'delete_asset_ids': {
                        'type': 'array',
                        'description': 'Remove assets by attachment ID. Deletes the attachment record.',
                        'items': {'type': 'integer'},
                    },
                },
            },
            'required_scope': 'odoo.write',
        },
        'manage_module': {
            'name': 'manage_module',
            'description': '''Create and manage importable Odoo data modules. Modules are packaged as ZIPs and installed via base_import_module.

ODOO VERSION: This instance runs Odoo 17. All generated XML, fields, views, and attributes
MUST be compatible with Odoo 17. Do NOT use syntax from newer versions (e.g. Odoo 18+). If unsure about any
feature or attribute, verify it first using code_search/code_read tools to check the Odoo 17
source code (or local code search tools like native IDE/editor search if available).
  Example: Odoo 17 uses <tree> for list views. Do NOT use <list> (introduced in Odoo 18).

AUTO INSTALL: By default, any file mutation (create_files, update_files, delete_file_ids)
automatically packages the module and installs it into Odoo. Set skip_install=true to
defer installation (useful for multi-step builds across several calls). On the next call
with skip_install=false (the default), pending changes will be detected and installed.
Installation requires MCP Admin access.

INSTALL FAILURE: If installation fails, file changes are still saved — only the install
is rolled back. Fix the broken file(s) using update_files and call again to retry install.
You do NOT need to resubmit all files.

READING FILES: To read existing module file contents or discover modules, use the
search_records/read_record tools on models 'mcp.module' and 'mcp.module.file'.
Use search_module_code to search within file contents by regex pattern.

MANIFEST VALIDATION: Before install, the tool validates that all files in the manifest
'data' list exist. Missing files block install with a clear error. Unreferenced .xml/.csv
files (not in manifest) trigger a warning in the response.

OPERATIONS (combine as needed):
- create_module / update_module: Module-level settings (mutually exclusive)
- create_files: Add files to module (requires module context)
- update_files: Edit file content with full replacement or content_patches
- delete_file_ids: Remove files by ID
- skip_install: Defer auto-install (default false) — use for multi-step builds
- force: Overwrite noupdate=1 records on install (default false)
- uninstall_first: Drop all module tables/columns/data before reinstall (default false). DESTRUCTIVE — always ask the user for confirmation before using this flag

MANIFEST FILE:
The __manifest__.py is a REGULAR FILE you write via create_files (file_path="__manifest__.py").
You have full control over its contents — no auto-generation. It MUST exist before packaging.
You should also include an empty __init__.py file (file_path="__init__.py", content="").
While not strictly required for import, it is needed if the module is ever deployed via addons path.

__manifest__.py TEMPLATE:
{
    'name': 'My Custom Module',
    'version': '17.0.1.0.0',
    'category': 'Customizations',
    'summary': 'Short description',
    'author': 'MCP Studio',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [
        'data/models.xml',
        'security/ir.model.access.csv',
        'views/views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'x_my_module/static/src/js/custom.js',
        ],
    },
    'installable': True,
    'application': False,
}

LIMITATIONS:
- No Python model files — models defined via ir.model XML records only
- No _inherit on Python models — but XML view inheritance IS supported
- No custom Python methods or ORM overrides
- Computed fields use a restricted sandbox (no imports, no dot assignment for writes)
- Asset paths in manifest CANNOT use glob wildcards — list each file explicitly
For full importable module documentation, see:
  https://www.odoo.com/documentation/17.0/developer/tutorials/importable_modules.html

CRITICAL — FILE STRUCTURE & LOAD ORDER (most common install failure):
  The __manifest__.py 'data' list MUST follow this exact order — Odoo loads files
  sequentially and later files depend on records created by earlier ones:
    __manifest__.py               — Module manifest (REQUIRED, not listed in data)
    __init__.py                   — Empty file (recommended, not listed in data)
    1. data/models.xml            — ir.model + ir.model.fields (models/fields must exist first)
    2. security/security.xml      — Groups, record rules (references models)
    3. security/ir.model.access.csv — ACLs (references models + groups)
    4. views/views.xml            — Views, menus, actions (references models + groups)
    5. data/actions.xml           — Server actions, automations (references models + views)
    6. data/records.xml           — Default data records (references models)
    static/src/js/               — JavaScript files (optional, listed in manifest 'assets')
    static/src/css/              — CSS files (optional, listed in manifest 'assets')
  Wrong order = install failure. This is the #1 cause of module install errors.

MODULE DESCRIPTION:
  static/description/index.html does NOT work for imported modules. Use the 'description'
  key in __manifest__.py with RST-formatted text instead.

XML TEMPLATE FOR MODEL + FIELDS:
  <record id="model_x_my_model" model="ir.model">
    <field name="name">My Model</field>
    <field name="model">x_my_model</field>
    <field name="info">Description of the model</field>
    <field name="is_mail_thread" eval="True"/>    <!-- chatter: requires 'mail' in depends -->
    <field name="is_mail_activity" eval="True"/>  <!-- activity scheduling (optional) -->
  </record>
  <record id="field_x_my_model_x_name" model="ir.model.fields">
    <field name="model_id" ref="model_x_my_model"/>
    <field name="name">x_name</field>
    <field name="field_description">Name</field>
    <field name="ttype">char</field>
    <field name="required" eval="True"/>
    <field name="copied" eval="True"/>
  </record>
  Chatter: use is_mail_thread/is_mail_activity flags on ir.model (NOT Python inheritance).
  In form views, render chatter with:
    <div class="oe_chatter">
        <field name="message_follower_ids"/>
        <field name="activity_ids"/>
        <field name="message_ids"/>
    </div>
  Do NOT use <chatter/> (introduced in Odoo 18).

DEFAULT VALUE (via ir.default — NOT on the field itself):
  <record id="default_x_my_model_x_state" model="ir.default">
    <field name="field_id" ref="field_x_my_model_x_state"/>
    <field name="json_value">"draft"</field>
  </record>
  Note: json_value is a JSON-encoded string. Use "draft" for strings, 100 for numbers,
  true for booleans. Dynamic defaults (like "today") are NOT possible.

COMPUTED FIELD EXAMPLE:
  <record id="field_x_my_model_x_total" model="ir.model.fields">
    <field name="model_id" ref="model_x_my_model"/>
    <field name="name">x_total</field>
    <field name="field_description">Total</field>
    <field name="ttype">float</field>
    <field name="depends">x_quantity, x_unit_price</field>
    <field name="compute"><![CDATA[
for record in self:
    record['x_total'] = record.x_quantity * record.x_unit_price
    ]]></field>
    <field name="store" eval="True"/>
    <field name="readonly" eval="True"/>
  </record>
  CRITICAL: Use dict assignment for writes: record['x_total'] = value
  Dot assignment (record.x_total = value) is DISABLED in the sandbox.
  Dot notation for reads works normally: record.x_quantity
  CRITICAL: Computed fields MUST be defined AFTER all fields listed in their 'depends'
  attribute. If a computed field references x_quantity and x_unit_price, those field
  records must appear earlier in the XML file. Otherwise installation fails with
  "Unknown field in dependency" error.

RELATIONAL FIELDS (all use ir.model.fields with extra attributes):
  many2one:  ttype=many2one,  relation=res.partner, on_delete=set null
  one2many:  ttype=one2many,  relation=x_child_model, relation_field=x_parent_id
             (requires a many2one on the related model pointing back)
  many2many: ttype=many2many, relation=x_my_tag

SELECTION FIELD (requires separate ir.model.fields.selection records for options):
  <record id="field_x_my_model_x_state" model="ir.model.fields">
    <field name="model_id" ref="model_x_my_model"/>
    <field name="name">x_state</field>
    <field name="field_description">Status</field>
    <field name="ttype">selection</field>
  </record>
  <record id="selection_x_state_draft" model="ir.model.fields.selection">
    <field name="field_id" ref="field_x_my_model_x_state"/>
    <field name="value">draft</field>
    <field name="name">Draft</field>
    <field name="sequence">1</field>
  </record>

VIEW INHERITANCE: Use inherit_id to extend existing views. Use code_search to find
  the correct view XML ID and field xpath for the module you want to extend.

SERVER ACTION TEMPLATE:
  Available in safe_eval sandbox: env, model, record, records, uid, user, time, datetime, dateutil,
  timezone, float_compare, b64encode, b64decode, Command, log, UserError (for user-facing errors)
  NOT available: ValidationError — use UserError instead (ValidationError is only available in execute_orm, not in server actions)
  NOTE: datetime is the MODULE — use datetime.datetime.now() for current datetime, datetime.date.today() for current date

  <record id="action_x_do_something" model="ir.actions.server">
    <field name="name">Do Something</field>
    <field name="model_id" ref="model_x_my_model"/>
    <field name="state">code</field>
    <field name="code"><![CDATA[
for rec in records:
    rec.write({'x_state': 'done'})
    ]]></field>
  </record>

AUTOMATED ACTION (requires base_automation dependency in __manifest__.py):
  Automated actions consist of TWO records: a server action with the code, and a
  base.automation record that defines the trigger and links to the server action.

  <!-- 1. Server action with the code to execute -->
  <record id="action_x_log_change" model="ir.actions.server">
    <field name="name">Log State Change</field>
    <field name="model_id" ref="model_x_my_model"/>
    <field name="state">code</field>
    <field name="code"><![CDATA[
for rec in records:
    rec.message_post(body="State changed to: %s" % rec.x_state)
    ]]></field>
  </record>

  <!-- 2. Automation rule that triggers the server action -->
  <record id="automation_x_log_change" model="base.automation">
    <field name="name">Log State Change on Update</field>
    <field name="model_id" ref="model_x_my_model"/>
    <field name="trigger">on_create_or_write</field>
    <field name="trigger_field_ids" eval="[(6, 0, [ref('field_x_my_model_x_state')])]"/>
    <field name="action_server_ids" eval="[(4, ref('action_x_log_change'))]"/>
  </record>

  Common triggers: on_create, on_write, on_create_or_write, on_time (date-based)
  trigger_field_ids: limits the trigger to specific field changes (uses XML ID refs to ir.model.fields)

SECURITY GROUP:
  <record id="module_category_x_my_module" model="ir.module.category">
    <field name="name">My Module</field>
    <field name="sequence">100</field>
  </record>
  <record id="group_x_my_user" model="res.groups">
    <field name="name">User</field>
    <field name="category_id" ref="module_category_x_my_module"/>
    <field name="comment">Basic access to my module</field>
  </record>
  Use implied_ids to create group hierarchy (e.g. Manager implies User).
  For standalone groups without a category, omit category_id.

SECURITY CSV FORMAT:
  id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
  access_x_my_model_user,x_my_model user,model_x_my_model,base.group_user,1,1,1,0

content_patches (on update_files):
  Surgical find/replace edits on a file's existing content. Each patch must have a
  unique 'find' string (found exactly once in the file) which is replaced with 'replace'.
  Patches are applied sequentially. Use instead of full 'content' for small edits to
  large files — saves tokens and reduces errors. Cannot combine with 'content' on same file.
  Use search_module_code tool first to find exact code locations before applying content_patches.
  Example: "update_files": [{"file_id": 1, "content_patches": [
    {"find": "<field name=\"x_price\"/>", "replace": "<field name=\"x_price\" optional=\"show\"/>"}
  ]}]

RESPONSE FORMAT: The result is a JSON object with these keys:
  module_id, name, technical_name — module identifiers
  state — current module state (e.g. "installed", "draft")
  files_changed — true if files were mutated but not yet installed
  installed_module_id — Odoo ir.module.module ID (false if never installed)
  form_url — direct URL to the module form in the Odoo backend
  operations — list of human-readable strings summarizing what happened
  message — overall status message
  created_files / updated_files — list of {id, file_path} for files created/updated (if any)
  manifest_warnings — list of unreferenced files (if any)
  On install failure: operations includes the error; file changes are still saved.

INSTALL TIMEOUT WARNING: Module installation (packaging + base_import_module) can take
  several minutes, especially for modules with many models/fields. This may exceed the
  MCP client's tool-call timeout. A timeout typically returns an empty response with no
  error details. If the tool call times out (empty/no response), the installation is
  still running server-side and will likely complete successfully.

  TIMEOUT RECOVERY PROCEDURE:
  1. Wait 20-30 seconds (use sleep or equivalent) to let the server-side install finish.
  2. Check the module's last_error field using execute_orm:
       mod = env['mcp.module'].sudo().browse(<module_id>)
       result = {'state': mod.state, 'last_error': mod.last_error or 'None'}
     - If state='installed' and last_error is empty → install succeeded.
     - If state='draft' and last_error has content → install failed; read the error,
       fix the file(s), and retry.
     - If state='draft' and last_error is empty → install may still be running;
       wait longer and check again.
  3. Do NOT retry the install blindly — a duplicate install while one is in progress
     can cause database locks or serialization errors.
  4. Only after confirming the previous install finished (via state or last_error)
     should you attempt another install.

IMPORTANT RULES:
- Model names MUST start with x_ (e.g., x_project_log)
- Field names MUST start with x_ (e.g., x_name, x_partner_id)
- Manifest 'data' order is CRITICAL: models → security → views → actions → data (see LOAD ORDER above)
- ir.model.access CSV model_id uses format: model_x_model_name (dots replaced with underscores)
- Computed fields: use record['field'] = value (NOT record.field = value)
- Default values: use ir.default records (NOT field attributes)
- Asset paths: list each file explicitly (NO glob wildcards like *.js)
''',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'create_module': {
                        'type': 'object',
                        'description': 'Create new module record. Returns module_id. Combine with create_files to add __manifest__.py and other files.',
                        'properties': {
                            'name': {'type': 'string', 'description': 'Display name'},
                            'technical_name': {'type': 'string', 'description': 'Technical name (auto x_ prefixed if missing)'},
                        },
                        'required': ['name', 'technical_name'],
                    },
                    'update_module': {
                        'type': 'object',
                        'description': 'Update existing module. Provides module context for file operations.',
                        'properties': {
                            'module_id': {'type': 'integer', 'description': 'ID of module to update (required)'},
                            'name': {'type': 'string'},
                        },
                        'required': ['module_id'],
                    },
                    'create_files': {
                        'type': 'array',
                        'description': "Add files to module. Requires create_module or update_module context. Include __manifest__.py as a regular file.",
                        'items': {
                            'type': 'object',
                            'properties': {
                                'file_path': {'type': 'string', 'description': "Relative path, e.g. '__manifest__.py', 'data/models.xml', 'static/description/icon.png'"},
                                'content': {'type': 'string', 'description': 'File content. Use for ALL normal files (.py, .xml, .csv, .js, .css, .html, .json, etc.)'},
                                'binary_content': {'type': 'string', 'description': 'Base64-encoded content. ONLY use for actual binary data (images like .png/.jpg/.ico, fonts, etc.). Do NOT use for code or data files — use content instead.'},
                                'sequence': {'type': 'integer', 'default': 10, 'description': 'Display order in file list'},
                            },
                            'required': ['file_path'],
                        },
                    },
                    'update_files': {
                        'type': 'array',
                        'description': 'Update existing files. Use content for full replacement or content_patches for surgical find/replace edits.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'file_id': {'type': 'integer', 'description': 'ID of file to update (required)'},
                                'content': {'type': 'string', 'description': 'Full replacement content (mutually exclusive with content_patches and binary_content)'},
                                'binary_content': {'type': 'string', 'description': 'Base64-encoded binary content for replacing image/binary files'},
                                'content_patches': {
                                    'type': 'array',
                                    'description': 'Surgical find/replace edits on existing file content. Each patch needs a unique find string (must appear exactly once) replaced with replace string. Applied sequentially. Cannot combine with content.',
                                    'items': {
                                        'type': 'object',
                                        'properties': {
                                            'find': {'type': 'string'},
                                            'replace': {'type': 'string'},
                                        },
                                        'required': ['find', 'replace'],
                                    },
                                },
                                'file_path': {'type': 'string'},
                                'sequence': {'type': 'integer'},
                            },
                            'required': ['file_id'],
                        },
                    },
                    'delete_file_ids': {
                        'type': 'array',
                        'description': 'Remove files by ID',
                        'items': {'type': 'integer'},
                    },
                    'skip_install': {
                        'type': 'boolean',
                        'default': False,
                        'description': 'Skip auto package+install after file mutations. Use for multi-step builds: set true while adding files across calls, then false (or omit) on the final call to trigger install.',
                    },
                    'force': {
                        'type': 'boolean',
                        'default': False,
                        'description': 'Force re-init on install (overwrites noupdate=1 records). Only relevant when files are mutated.',
                    },
                    'uninstall_first': {
                        'type': 'boolean',
                        'default': False,
                        'description': 'Uninstall existing module before reinstalling. Cleanly drops ALL previous tables, columns, and data. Use when field types changed or fields were removed between versions. DATA LOSS WARNING: all user data in the module models will be permanently lost. IMPORTANT: Always ask the user for explicit confirmation before setting this to true.',
                    },
                },
            },
            'required_scope': 'odoo.write',
        },
    }

    # Tools that require allowlist checking
    ALLOWLIST_TOOLS = {
        'create_record',
        'update_record',
        'delete_record',
        'execute_method',
        'execute_orm',
        'code_search',
        'code_read',
        'create_echart',
        'search_webapp_code',
        'search_module_code',
        'manage_webapp',
        'manage_module',
    }

    @classmethod
    def get_tools_list(cls, env, scope):
        """
        Get list of available tools based on user scope and permissions.

        :param env: Odoo environment
        :param scope: User's granted scope string
        :return: List of tool definitions
        """
        config = env['mcp.config'].sudo().get_config()

        scope_set = set(scope.split()) if scope else set()
        tools = []

        for tool_name, tool_def in cls.TOOLS.items():
            # Check scope - only include tools the user has permission for
            required_scope = tool_def.get('required_scope', 'odoo.read')
            if required_scope not in scope_set:
                continue

            # Check allowlist for write tools
            if tool_name in cls.ALLOWLIST_TOOLS:
                if not config.is_user_allowed_for_tool(env.user, tool_name):
                    continue

            # Build tool info for MCP
            tools.append({
                'name': tool_def['name'],
                'description': tool_def['description'],
                'inputSchema': tool_def['inputSchema'],
            })

        return tools

    @classmethod
    def call_tool(cls, env, tool_name, arguments, token_data, log_model, ip_address=None):
        """
        Execute a tool and return the result in MCP format.

        Per MCP spec, tool execution errors should be returned as successful
        responses with isError=true, not raised as exceptions.

        :param env: Odoo environment
        :param tool_name: Name of the tool to call
        :param arguments: Tool arguments dictionary
        :param token_data: Token validation data (user_id, scope, etc.)
        :param log_model: mcp.execution.log model for audit logging
        :param ip_address: Client IP address
        :return: MCP tool result dict with 'content' and optional 'isError'
        """
        start_time = time.time()

        # Validate tool exists
        tool_def = cls.TOOLS.get(tool_name)
        if not tool_def:
            return {
                'content': [{'type': 'text', 'text': f"Unknown tool: {tool_name}"}],
                'isError': True,
            }

        # Check scope
        scope = token_data.get('scope', '')
        required_scope = tool_def.get('required_scope', 'odoo.read')
        if required_scope not in scope.split():
            return {
                'content': [{'type': 'text', 'text': f"Insufficient scope. Required: {required_scope}"}],
                'isError': True,
            }

        # Get MCP config
        config = env['mcp.config'].sudo().get_config()

        # Check allowlist for write tools
        if tool_name in cls.ALLOWLIST_TOOLS:
            if not config.is_user_allowed_for_tool(env.user, tool_name):
                return {
                    'content': [{'type': 'text', 'text': (
                        f"User not authorized for {tool_name}. "
                        f"Contact your MCP administrator to be added to the allowlist."
                    )}],
                    'isError': True,
                }

        # Get implementation
        impl_method = getattr(cls, f'_impl_{tool_name}', None)
        if not impl_method:
            return {
                'content': [{'type': 'text', 'text': f"Tool not implemented: {tool_name}"}],
                'isError': True,
            }

        # Apply MCP model/field access restrictions via cursor attributes (except for execute_orm)
        # Using cursor attributes instead of context provides stronger guarantees:
        # - Cannot be bypassed by with_context(), sudo(), with_user(), etc.
        # - Cursor object is shared across all environments in a transaction
        # - Enforced even on sudo() for true data isolation
        # This enables the BaseModel._check_access() override to enforce restrictions
        if tool_name != 'execute_orm':
            restricted_models = config.get_restricted_models_for_user(env.user)
            restricted_fields = config.get_restricted_fields_for_user(env.user)
            if restricted_models is not None:
                env.cr._mcp_restricted_models = restricted_models
            if restricted_fields is not None:
                env.cr._mcp_restricted_fields = restricted_fields

        # Capture user_id before execution for logging (defensive measure)
        user_id = env.user.id

        # Use savepoint for tool execution so failed tools rollback their changes
        # This ensures partial writes from failed tools don't persist, while still
        # allowing error logging and response building to complete normally
        savepoint = env.cr.savepoint()

        try:
            result = impl_method(env, arguments, token_data)
            execution_time = time.time() - start_time

            # Extract embedded resources metadata (added by read tools for binary fields)
            embedded_resources_meta = None
            if isinstance(result, dict):
                embedded_resources_meta = result.pop('_embedded_resources', None)
                binary_fields = result.pop('_binary_fields', None)

                # Add log-friendly note about excluded binary fields
                if binary_fields:
                    result['note'] = f"Binary fields excluded and embedded as resources: {binary_fields}"

            # Tool succeeded - release savepoint to keep changes
            # Wrapped in try/except in case a commit in tool code already released it
            try:
                savepoint.close(rollback=False)
            except Exception:
                pass

            # Log successful execution
            log_model.log_execution(
                user_id=user_id,
                client_id=token_data.get('client_id'),
                api_key_id=token_data.get('api_key_id'),
                tool_name=tool_name,
                code=arguments.get('code') if tool_name == 'execute_orm' else None,
                parameters=json.dumps(arguments, indent=2, default=json_default) if tool_name != 'execute_orm' else None,
                result=json.dumps(result, indent=2, default=json_default) if result else None,
                success=True,
                execution_time=execution_time,
                ip_address=ip_address,
            )

            # Build content array with main JSON result
            content = [
                {
                    'type': 'text',
                    'text': json.dumps(result, indent=2, default=json_default)
                }
            ]

            # Add embedded resources for binary fields
            if embedded_resources_meta:
                for resource_meta in embedded_resources_meta:
                    resource_content = cls._fetch_binary_for_embedded_resource(
                        env, resource_meta
                    )
                    if resource_content:
                        content.append(resource_content)

            return {'content': content}

        except Exception as e:
            execution_time = time.time() - start_time

            # Tool failed - rollback and release savepoint to discard partial changes
            # Wrapped in try/except in case a commit in tool code already released it
            try:
                savepoint.close(rollback=True)
            except Exception:
                pass

            # Capture full traceback for debugging
            tb = traceback.format_exc()
            error_with_tb = f"{e}\n\nTraceback:\n{tb}"

            # Log failed execution to database (includes traceback for debugging)
            log_model.log_execution(
                user_id=user_id,
                client_id=token_data.get('client_id'),
                api_key_id=token_data.get('api_key_id'),
                tool_name=tool_name,
                code=arguments.get('code') if tool_name == 'execute_orm' else None,
                parameters=json.dumps(arguments, indent=2, default=json_default) if tool_name != 'execute_orm' else None,
                error=error_with_tb,
                success=False,
                execution_time=execution_time,
                ip_address=ip_address,
            )

            return {
                'content': [{'type': 'text', 'text': f"Error: {e}"}],
                'isError': True,
            }

        finally:
            # Clear cursor attributes after tool execution to prevent affecting
            # any subsequent operations in the same request (e.g., controller cleanup)
            if hasattr(env.cr, '_mcp_restricted_models'):
                del env.cr._mcp_restricted_models
            if hasattr(env.cr, '_mcp_restricted_fields'):
                del env.cr._mcp_restricted_fields

    # Tool implementations

    @classmethod
    def _check_restricted_fields(cls, env, model_name, field_names, operation='write'):
        """
        Check if any fields are restricted and raise error if so.

        :param env: Odoo environment with _mcp_restricted_fields on cursor
        :param model_name: Name of the model
        :param field_names: Iterable of field names to check
        :param operation: Operation type for error message ('read' or 'write')
        :raises ValueError: If any restricted fields are found
        """
        restricted_fields = getattr(env.cr, '_mcp_restricted_fields', None) or set()
        if not restricted_fields:
            return

        violations = [f for f in field_names if (model_name, f) in restricted_fields]
        if violations:
            verb = 'read' if operation == 'read' else 'written'
            raise ValueError(
                f"Restricted field(s) cannot be {verb} via MCP: {', '.join(sorted(violations))}"
            )

    @classmethod
    def _filter_restricted_fields(cls, env, model_name, record_data):
        """
        Remove restricted fields from record data dict.

        :param env: Odoo environment with _mcp_restricted_fields on cursor
        :param model_name: Name of the model
        :param record_data: Dict of field values
        :return: Filtered dict with restricted fields removed
        """
        restricted_fields = getattr(env.cr, '_mcp_restricted_fields', None) or set()
        if not restricted_fields:
            return record_data

        return {
            k: v for k, v in record_data.items()
            if (model_name, k) not in restricted_fields
        }

    @classmethod
    def _fetch_binary_for_embedded_resource(cls, env, resource_meta):
        """
        Fetch binary data for an embedded resource and return MCP content block.

        Uses the centralized fetch_field_resource_content utility which handles
        direct binary fields, attachment-backed fields, URL attachments, and
        text/binary detection automatically.

        Returns ImageContent format for images (better client support) and
        EmbeddedResource format for other binary types.

        :param env: Odoo environment
        :param resource_meta: Dict with uri, field, record_id keys
        :return: MCP content block (ImageContent or EmbeddedResource), or None
        """
        uri = resource_meta['uri']
        field_name = resource_meta['field']
        record_id = resource_meta['record_id']

        # Parse model from URI: odoo://record/{model}/{field}?ids={id}
        base_path = uri.split('?ids=')[0]
        parts = base_path.replace('odoo://record/', '').split('/')
        if len(parts) < 2:
            return None
        model_name = parts[0]

        try:
            content = fetch_field_resource_content(
                env, model_name, field_name, record_id, uri
            )
            if not content:
                return None

            # Use ImageContent format for images (better MCP client support)
            # ImageContent: {type: "image", data: "base64...", mimeType: "..."}
            mimetype = content.get('mimeType', '')
            if mimetype.startswith('image/') and 'blob' in content:
                return {
                    'type': 'image',
                    'data': content['blob'],
                    'mimeType': mimetype,
                }

            # Use EmbeddedResource format for non-images
            # EmbeddedResource: {type: "resource", resource: {uri, mimeType, blob/text}}
            return {
                'type': 'resource',
                'resource': content,
            }

        except AccessError as e:
            logging.getLogger(__name__).warning(
                "Access denied fetching embedded resource %s: %s", uri, str(e)
            )
            return None
        except Exception as e:
            logging.getLogger(__name__).exception(
                "Error fetching embedded resource %s: %s", uri, str(e)
            )
            return None

    @classmethod
    def _impl_list_models(cls, env, arguments, token_data):
        """List accessible models with optional regex pattern and pagination."""
        pattern = arguments.get('pattern')
        filter_str = arguments.get('filter', '').lower()
        limit = min(arguments.get('limit', 50), 200)
        offset = arguments.get('offset', 0)

        IrModel = env['ir.model'].sudo()

        # Build domain for simple filter
        domain = []
        if filter_str:
            domain = ['|',
                      ('model', 'ilike', filter_str),
                      ('name', 'ilike', filter_str)]

        # Search with domain
        models = IrModel.search(domain, order='model')

        # Apply regex pattern filter if specified
        if pattern:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
                models = models.filtered(lambda m: regex.search(m.model))
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}")

        # Filter out restricted models (blocklist approach)
        restricted = getattr(env.cr, '_mcp_restricted_models', None)
        if restricted is not None:
            # Exclude models that have read blocked
            # Model is readable if: not in restricted list, OR read is not blocked
            models = models.filtered(
                lambda m: m.model not in restricted or not restricted[m.model].get('read', True)
            )

        # Get total before pagination
        total = len(models)

        # Apply pagination
        models = models[offset:offset + limit]

        models_list = [{
            'model': m.model,
            'name': m.name,
            'transient': m.transient,
        } for m in models]
        return {
            'total': total,
            'offset': offset,
            'returned': len(models_list),
            'models': models_list,
        }

    @classmethod
    def _impl_get_model_schema(cls, env, arguments, token_data):
        """Get model field definitions with optional filters."""
        model_name = arguments['model']
        field_names = arguments.get('field_names')
        field_types = arguments.get('field_types')
        stored_only = arguments.get('stored_only', False)
        required_only = arguments.get('required_only', False)
        include_relational = arguments.get('include_relational', True)
        no_default = arguments.get('no_default', True)

        # Get restricted models and fields from cursor (set by call_tool)
        restricted_models = getattr(env.cr, '_mcp_restricted_models', None)
        restricted_fields = getattr(env.cr, '_mcp_restricted_fields', None) or set()

        # Validate model exists and is accessible
        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        Model = env[model_name]

        # If specific fields explicitly requested, error on restricted fields
        # (if field_names is empty/None, silently filter instead)
        if field_names:
            cls._check_restricted_fields(env, model_name, field_names, operation='read')
            fields_info = Model.fields_get(field_names)
        else:
            fields_info = Model.fields_get()

        # Relational field types
        relational_types = ('many2one', 'one2many', 'many2many')

        # Pre-filter fields to build list of allowed field names for default_get
        # This prevents access errors when calling default_get on restricted fields
        # or fields pointing to restricted models
        allowed_field_names = []
        for field_name, field_data in fields_info.items():
            # Skip directly restricted fields
            if (model_name, field_name) in restricted_fields:
                continue
            # Skip relational fields pointing to restricted models
            field_type = field_data.get('type')
            if field_type in relational_types:
                related_model = field_data.get('relation')
                if restricted_models is not None and related_model in restricted_models:
                    if restricted_models[related_model].get('read', True):
                        continue
            allowed_field_names.append(field_name)

        # Get default values only for allowed fields (single efficient call)
        defaults = Model.default_get(allowed_field_names)

        # Build filtered schema
        schema = {}
        for field_name, field_data in fields_info.items():
            field_type = field_data.get('type')

            # Apply filters
            if field_types and field_type not in field_types:
                continue
            if stored_only and not field_data.get('store', True):
                continue
            if required_only and not field_data.get('required', False):
                continue
            if not include_relational and field_type in relational_types:
                continue
            # Skip restricted fields
            if (model_name, field_name) in restricted_fields:
                continue
            # Skip fields with default values when no_default filter is enabled
            if no_default and field_name in defaults:
                continue

            # Build field info
            field_info = {
                'type': field_type,
                'string': field_data.get('string'),
                'required': field_data.get('required', False),
                'readonly': field_data.get('readonly', False),
                'stored': field_data.get('store', True),
            }

            # Only include help if present (saves context)
            if field_data.get('help'):
                field_info['help'] = field_data['help']

            # Add selection options
            if field_type == 'selection':
                field_info['selection'] = field_data.get('selection', [])

            # Add relation for relational fields (skip if related model is restricted)
            if field_type in relational_types:
                related_model = field_data.get('relation')
                # Skip if related model has read blocked
                if restricted_models is not None and related_model in restricted_models:
                    if restricted_models[related_model].get('read', True):
                        continue  # Skip fields pointing to restricted models
                field_info['relation'] = related_model

            # Add default value if present
            if field_name in defaults:
                field_info['default'] = defaults[field_name]

            schema[field_name] = field_info

        return {
            'model': model_name,
            'field_count': len(schema),
            'fields': schema,
        }

    @classmethod
    def _fields_to_specification(cls, model, fields, restricted_fields=None):
        """
        Convert a list of field names to a web_read specification dict.

        For Many2one fields, automatically includes display_name so AI agents
        can understand the referenced record.

        Binary/image fields are detected and returned separately so they can
        be handled as embedded resources instead of inline JSON.

        :param model: The Odoo model object
        :param fields: List of field names, or None for all fields
        :param restricted_fields: Set of (model, field) tuples to exclude
        :return: Tuple of (specification_dict, binary_fields_set)
        """
        model_name = model._name
        restricted_fields = restricted_fields or set()
        binary_fields = set()

        if not fields:
            # Get all readable fields
            fields = list(model.fields_get(attributes=()))

        spec = {}
        model_fields = model._fields

        for field_name in fields:
            # Skip restricted fields
            if (model_name, field_name) in restricted_fields:
                continue

            field = model_fields.get(field_name)
            if field:
                if field.type in ('binary', 'image'):
                    # Track binary fields separately - don't include in spec
                    binary_fields.add(field_name)
                    continue
                elif field.type == 'many2one':
                    # Include display_name for Many2one fields
                    spec[field_name] = {'fields': {'display_name': {}}}
                else:
                    spec[field_name] = {}
            else:
                spec[field_name] = {}

        return spec, binary_fields

    @classmethod
    def _impl_search_read(cls, env, arguments, token_data):
        """Search and read records using web_search_read for proper JSON serialization."""
        model_name = arguments['model']
        domain = arguments.get('domain', [])
        fields = arguments.get('fields', [])
        limit = arguments.get('limit', 100)
        offset = arguments.get('offset', 0)
        order = arguments.get('order')

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        Model = env[model_name]
        restricted_fields = getattr(env.cr, '_mcp_restricted_fields', None) or set()

        # If specific fields explicitly requested, error on restricted fields
        # (if fields is empty/None, silently filter instead)
        if fields:
            cls._check_restricted_fields(env, model_name, fields, operation='read')

        # Build specification for web_search_read (excludes restricted and binary fields)
        specification, binary_fields = cls._fields_to_specification(
            Model, fields, restricted_fields
        )

        # Use web_search_read - returns JSON-serializable data
        result = Model.web_search_read(
            domain=domain,
            specification=specification,
            offset=offset,
            limit=limit,
            order=order,
        )

        response = {
            'model': model_name,
            'total': result['length'],
            'count': len(result['records']),
            'offset': offset,
            'limit': limit,
            'records': result['records'],
        }

        # Add binary field metadata for embedded resources
        if binary_fields and result['records']:
            response['_binary_fields'] = list(binary_fields)
            response['_embedded_resources'] = []
            for record in result['records']:
                for bf in binary_fields:
                    response['_embedded_resources'].append({
                        'uri': f"odoo://record/{model_name}/{bf}?ids={record['id']}",
                        'field': bf,
                        'record_id': record['id'],
                    })

        return response

    @classmethod
    def _impl_read_record(cls, env, arguments, token_data):
        """Read a single record using web_read for proper JSON serialization."""
        model_name = arguments['model']
        record_id = arguments['id']
        fields = arguments.get('fields', [])

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        Model = env[model_name]
        record = Model.browse(record_id)

        if not record.exists():
            raise ValueError(f"Record not found: {model_name}({record_id})")

        restricted_fields = getattr(env.cr, '_mcp_restricted_fields', None) or set()

        # If specific fields explicitly requested, error on restricted fields
        # (if fields is empty/None, silently filter instead)
        if fields:
            cls._check_restricted_fields(env, model_name, fields, operation='read')

        # Build specification for web_read (excludes restricted and binary fields)
        specification, binary_fields = cls._fields_to_specification(
            Model, fields, restricted_fields
        )

        # Use web_read - returns JSON-serializable data
        data = record.web_read(specification)
        response = data[0] if data else None

        # Add binary field metadata for embedded resources
        if response and binary_fields:
            response['_binary_fields'] = list(binary_fields)
            response['_embedded_resources'] = [
                {
                    'uri': f"odoo://record/{model_name}/{bf}?ids={record_id}",
                    'field': bf,
                    'record_id': record_id,
                }
                for bf in binary_fields
            ]

        return response

    @classmethod
    def _impl_read_group(cls, env, arguments, token_data):
        """Aggregate records using read_group."""
        model_name = arguments['model']
        domain = arguments.get('domain', [])
        groupby = arguments.get('groupby', [])
        fields = arguments.get('fields', [])
        limit = arguments.get('limit')
        offset = arguments.get('offset', 0)
        orderby = arguments.get('orderby')

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        # Validate that groupby/field fields are not restricted
        restricted_fields = getattr(env.cr, '_mcp_restricted_fields', None) or set()
        if restricted_fields:
            groupby_fields = [g.split(':')[0].split('.')[0] for g in groupby]
            aggregate_fields = [f.split(':')[0] for f in fields]
            all_fields = groupby_fields + aggregate_fields
            violations = [f for f in all_fields if (model_name, f) in restricted_fields]
            if violations:
                raise ValueError(
                    f"Restricted field(s) cannot be read via MCP: {', '.join(sorted(set(violations)))}"
                )

        Model = env[model_name]

        result = Model.read_group(
            domain=domain,
            fields=fields,
            groupby=groupby,
            offset=offset,
            limit=limit,
            orderby=orderby or False,
            lazy=False,
        )

        # Clean up internal keys and rename __domain to __extra_domain
        groups = []
        for group in result:
            cleaned = {}
            for k, v in group.items():
                if k in ('__context', '__fold'):
                    continue
                if k == '__domain':
                    cleaned['__extra_domain'] = v
                else:
                    cleaned[k] = v
            groups.append(cleaned)

        return {
            'model': model_name,
            'count': len(groups),
            'offset': offset,
            'groups': groups,
        }

    @classmethod
    def _impl_create_record(cls, env, arguments, token_data):
        """Create one or more records. Values is an array of field value dicts."""
        model_name = arguments['model']
        values_list = arguments['values']

        if not isinstance(values_list, list):
            raise ValueError("'values' must be an array of field value objects")

        if not values_list:
            raise ValueError("'values' array cannot be empty")

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        # Check for restricted fields in all value dicts
        for values in values_list:
            cls._check_restricted_fields(env, model_name, values.keys())

        Model = env[model_name]
        # Odoo's create() natively handles list of dicts for bulk creation
        records = Model.create(values_list)

        return {
            'ids': records.ids,
            'model': model_name,
            'created': True,
            'count': len(records),
        }

    @classmethod
    def _impl_update_record(cls, env, arguments, token_data):
        """
        Update one or more records.

        Two modes:
        - ids + values: Apply same values to all records (efficient bulk write)
        - updates: Array of {id, values} for different values per record
        """
        model_name = arguments['model']
        ids = arguments.get('ids')
        values = arguments.get('values')
        updates = arguments.get('updates')

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        Model = env[model_name]

        # Mode 1: ids + values (same values for all)
        if ids is not None and values is not None:
            if updates is not None:
                raise ValueError("Cannot use both 'ids'+'values' and 'updates'. Choose one mode.")

            if not isinstance(ids, list) or not ids:
                raise ValueError("'ids' must be a non-empty array of integers")

            # Check for restricted fields
            cls._check_restricted_fields(env, model_name, values.keys())

            records = Model.browse(ids)
            missing = set(ids) - set(records.exists().ids)
            if missing:
                raise ValueError(f"Records not found: {model_name}({sorted(missing)})")

            records.write(values)

            return {
                'ids': ids,
                'model': model_name,
                'updated': True,
                'count': len(ids),
            }

        # Mode 2: updates array (different values per record)
        if updates is not None:
            if not isinstance(updates, list) or not updates:
                raise ValueError("'updates' must be a non-empty array of {id, values} objects")

            updated_ids = []
            for update in updates:
                record_id = update.get('id')
                record_values = update.get('values')

                if record_id is None or record_values is None:
                    raise ValueError("Each update must have 'id' and 'values'")

                # Check for restricted fields
                cls._check_restricted_fields(env, model_name, record_values.keys())

                record = Model.browse(record_id)
                if not record.exists():
                    raise ValueError(f"Record not found: {model_name}({record_id})")

                record.write(record_values)
                updated_ids.append(record_id)

            return {
                'ids': updated_ids,
                'model': model_name,
                'updated': True,
                'count': len(updated_ids),
            }

        raise ValueError("Must provide either 'ids'+'values' or 'updates' array")

    @classmethod
    def _impl_delete_record(cls, env, arguments, token_data):
        """Delete one or more records."""
        model_name = arguments['model']
        ids = arguments['ids']

        if not isinstance(ids, list) or not ids:
            raise ValueError("'ids' must be a non-empty array of integers")

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        Model = env[model_name]
        records = Model.browse(ids)

        missing = set(ids) - set(records.exists().ids)
        if missing:
            raise ValueError(f"Records not found: {model_name}({sorted(missing)})")

        records.unlink()

        return {
            'ids': ids,
            'model': model_name,
            'deleted': True,
            'count': len(ids),
        }

    @classmethod
    def _sanitize_result(cls, value, _depth=0):
        """
        Recursively sanitize method results for JSON serialization.

        Handles recordsets, nested structures, and falls back to str() for
        non-serializable objects.
        """
        if _depth > 10:  # Prevent infinite recursion
            return str(value)

        # None, bool, int, float, str - JSON-safe primitives
        if value is None or isinstance(value, (bool, int, float, str)):
            return value

        # Recordset - convert to id/ids representation
        if hasattr(value, '_name') and hasattr(value, 'ids'):
            if len(value) == 1:
                return {'id': value.id, 'model': value._name}
            return {'ids': value.ids, 'model': value._name}

        # Dict - recurse into values
        if isinstance(value, dict):
            return {k: cls._sanitize_result(v, _depth + 1) for k, v in value.items()}

        # List/tuple - recurse into items
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_result(item, _depth + 1) for item in value]

        # Fallback - convert to string representation
        return str(value)

    # Methods blocked from execute_method for security reasons
    BLOCKED_METHODS = {
        # Privilege escalation - could bypass access controls
        'sudo': 'Method could bypass access controls',
        'with_user': 'Method could impersonate another user',
        'with_env': 'Method could modify execution environment',
        'with_company': 'Method could bypass company security',
        # CRUD - use dedicated tools which enforce field restrictions
        'write': 'Use update_record tool instead',
        'create': 'Use create_record tool instead',
        'unlink': 'Use delete_record tool instead',
        # Low-level methods that bypass ORM protections
        '_write': 'Low-level method bypasses access controls',
        '_create': 'Low-level method bypasses access controls',
    }

    @classmethod
    def _impl_execute_method(cls, env, arguments, token_data):
        """Execute a model method."""
        model_name = arguments['model']
        method_name = arguments['method']
        ids = arguments.get('ids', [])
        args = arguments.get('args', [])
        kwargs = arguments.get('kwargs', {})

        if model_name not in env:
            raise ValueError(f"Model not found: {model_name}")

        # Safety check - block specific dangerous methods
        if method_name in cls.BLOCKED_METHODS:
            reason = cls.BLOCKED_METHODS[method_name]
            raise AccessError(f"Method '{method_name}' not allowed: {reason}")

        Model = env[model_name]

        if ids:
            records = Model.browse(ids)
            method = getattr(records, method_name, None)
        else:
            method = getattr(Model, method_name, None)

        if not method or not callable(method):
            raise ValueError(f"Method not found: {model_name}.{method_name}")

        result = method(*args, **kwargs)
        return cls._sanitize_result(result)

    @classmethod
    def _impl_execute_orm(cls, env, arguments, token_data):
        """Execute arbitrary ORM code."""
        from .executor import MCPExecutor

        return MCPExecutor.execute(env, arguments['code'])

    # -------------------------------------------------------------------------
    # Code Search Tools
    # -------------------------------------------------------------------------

    # Allowed file extensions for code search
    CODE_FILE_EXTENSIONS = {
        '.py', '.xml', '.js', '.ts', '.css', '.scss',
        '.csv', '.txt', '.md', '.rst', '.html', '.json',
    }

    # Check if ripgrep is available (cached)
    _ripgrep_available = None

    @classmethod
    def _is_ripgrep_available(cls):
        """Check if ripgrep (rg) is installed and available."""
        if cls._ripgrep_available is None:
            cls._ripgrep_available = shutil.which('rg') is not None
        return cls._ripgrep_available

    @classmethod
    def _get_addons_paths(cls):
        """Get deduplicated list of addon paths that exist."""
        seen = set()
        paths = []
        for p in __addons_path__:
            norm_p = os.path.normpath(os.path.abspath(p))
            if norm_p not in seen and os.path.isdir(norm_p):
                seen.add(norm_p)
                paths.append(norm_p)
        return paths

    @classmethod
    def _validate_path_security(cls, path):
        """
        Validate user-supplied path for basic security issues.

        Note: This is input sanitization only. Actual containment security
        is enforced at file access time using os.path.commonpath().
        """
        if not path or not isinstance(path, str):
            raise ValueError("Invalid path: must be a non-empty string")

        # Null byte injection (can truncate paths in some contexts)
        if '\x00' in path:
            raise ValueError("Invalid path: null bytes not allowed")

        # Absolute paths
        if os.path.isabs(path):
            raise ValueError("Invalid path: absolute paths not allowed")

        # Directory traversal - check path components
        # Normalize separators and check for '..' as a path component
        normalized = path.replace('\\', '/')
        if '..' in normalized.split('/'):
            raise ValueError("Invalid path: directory traversal not allowed")

        # Home directory expansion
        if path.startswith('~'):
            raise ValueError("Invalid path: home directory expansion not allowed")

    @classmethod
    def _impl_code_search(cls, env, arguments, token_data):
        """
        Search addon source code using ripgrep (fast) or Python fallback.
        """
        config = env['mcp.config'].sudo().get_config()

        pattern = arguments['pattern']
        module = arguments.get('module') or None  # Normalize empty string to None
        file_pattern = arguments.get('file_pattern', '**/*')
        case_sensitive = arguments.get('case_sensitive', False)
        output_mode = arguments.get('output_mode', 'files_with_matches')
        context_before = arguments.get('context_before', 0)
        context_after = arguments.get('context_after', 0)
        limit = arguments.get('limit', 100)
        offset = arguments.get('offset', 0)

        # Validate output_mode
        valid_modes = ('files_with_matches', 'content', 'count')
        if output_mode not in valid_modes:
            raise ValueError(f"Invalid output_mode: must be one of {valid_modes}")

        # Security validations
        cls._validate_path_security(file_pattern)
        if module:
            cls._validate_path_security(module)
        limit = min(limit, config.code_search_max_matches or 500)
        offset = max(0, int(offset or 0))

        # Validate regex pattern
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        addons_paths = cls._get_addons_paths()
        if not addons_paths:
            return {'pattern': pattern, 'total_files': 0, 'offset': 0, 'returned': 0, 'files': [] if output_mode == 'files_with_matches' else [], 'results': []}

        # If module specified, find and filter to that specific module path
        search_paths = []
        if module:
            for addons_path in addons_paths:
                module_path = os.path.join(addons_path, module)
                if os.path.isdir(module_path):
                    search_paths.append(module_path)
                    break  # Use first match
            if not search_paths:
                raise ValueError(f"Module not found: {module}")
        else:
            search_paths = addons_paths

        # Try ripgrep first, fall back to Python
        if cls._is_ripgrep_available():
            return cls._code_search_ripgrep(
                pattern, file_pattern, case_sensitive, output_mode,
                context_before, context_after, limit, offset, search_paths, module
            )
        else:
            return cls._code_search_python(
                pattern, file_pattern, case_sensitive, output_mode,
                context_before, context_after, limit, offset, search_paths, module
            )

    @classmethod
    def _code_search_ripgrep(cls, pattern, file_pattern, case_sensitive, output_mode,
                              context_before, context_after, limit, offset, search_paths, module):
        """Search using ripgrep for high performance."""
        import subprocess

        all_results = []
        all_files_matched = []
        module_counts = defaultdict(int)
        total_matches = 0

        for search_path in search_paths:
            # Determine base path for relative paths
            # If module specified, search_path IS the module dir, so parent is addons_path
            if module:
                addons_path = os.path.dirname(search_path)
            else:
                addons_path = search_path

            # Build ripgrep command
            cmd = ['rg', '--no-heading']
            if not case_sensitive:
                cmd.append('--ignore-case')

            # Add glob pattern
            cmd.extend(['--glob', file_pattern])

            # Skip hidden and cache directories
            cmd.extend(['--glob', '!.*', '--glob', '!__pycache__'])

            # Output format based on mode
            if output_mode == 'files_with_matches':
                cmd.append('--files-with-matches')
            elif output_mode == 'count':
                cmd.append('--count')
            else:  # content
                cmd.append('--line-number')
                if context_before > 0:
                    cmd.extend(['-B', str(context_before)])
                if context_after > 0:
                    cmd.extend(['-A', str(context_after)])

            # Add pattern and path
            cmd.extend(['--', pattern, search_path])

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                output = result.stdout or ''
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                continue

            if not output or not output.strip():
                continue

            # Parse output based on mode
            for line in output.strip().split('\n'):
                if not line:
                    continue

                if output_mode == 'files_with_matches':
                    rel_path = os.path.relpath(line.strip(), addons_path)
                    if rel_path not in all_files_matched:
                        all_files_matched.append(rel_path)

                elif output_mode == 'count':
                    if ':' in line:
                        filepath, count_str = line.rsplit(':', 1)
                        rel_path = os.path.relpath(filepath, addons_path)
                        module_name = rel_path.split(os.sep)[0]
                        count = int(count_str)
                        module_counts[module_name] += count
                        total_matches += count

                else:  # content
                    match = re.match(r'^(.+?)[:\-](\d+)[:\-](.*)$', line)
                    if match:
                        filepath, line_num, content = match.groups()
                        rel_path = os.path.relpath(filepath, addons_path)
                        module_name = rel_path.split(os.sep)[0]
                        module_counts[module_name] += 1
                        total_matches += 1
                        all_results.append({
                            'file': rel_path,
                            'line': int(line_num),
                            'match': content,
                        })

        # Apply offset and limit
        if output_mode == 'files_with_matches':
            paginated = all_files_matched[offset:offset + limit]
            return {
                'pattern': pattern,
                'total_files': len(all_files_matched),
                'offset': offset,
                'returned': len(paginated),
                'files': paginated,
            }
        elif output_mode == 'count':
            return {
                'pattern': pattern,
                'total_matches': total_matches,
                'by_module': dict(sorted(module_counts.items())),
            }
        else:
            paginated = all_results[offset:offset + limit]
            return {
                'pattern': pattern,
                'total_matches': total_matches,
                'offset': offset,
                'returned': len(paginated),
                'results': paginated,
            }

    @classmethod
    def _code_search_python(cls, pattern, file_pattern, case_sensitive, output_mode,
                             context_before, context_after, limit, offset, search_paths, module):
        """Fallback Python search when ripgrep is not available."""

        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        all_results = []
        all_files_matched = []
        module_counts = defaultdict(int)
        total_matches = 0

        for search_path in search_paths:
            # Determine base path for relative paths
            if module:
                addons_path = os.path.dirname(search_path)
            else:
                addons_path = search_path

            # Use glob to find matching files
            glob_path = os.path.join(search_path, file_pattern)
            matching_files = glob_module.glob(glob_path, recursive=True)

            for filepath in matching_files:
                # Skip hidden and cache directories
                rel_path = os.path.relpath(filepath, addons_path)
                path_parts = rel_path.replace('\\', '/').split('/')
                if any(p.startswith('.') or p == '__pycache__' for p in path_parts):
                    continue

                # Check extension
                ext = os.path.splitext(filepath)[1].lower()
                if ext not in cls.CODE_FILE_EXTENSIONS:
                    continue

                if not os.path.isfile(filepath):
                    continue

                module_name = path_parts[0]

                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                except (IOError, OSError):
                    continue

                file_has_match = False
                for line_num, line in enumerate(lines, 1):
                    if regex.search(line):
                        file_has_match = True
                        total_matches += 1
                        module_counts[module_name] += 1

                        if output_mode == 'content':
                            result = {
                                'file': rel_path,
                                'line': line_num,
                                'match': line.rstrip('\n\r'),
                            }
                            if context_before > 0:
                                start = max(0, line_num - 1 - context_before)
                                result['before'] = [l.rstrip('\n\r') for l in lines[start:line_num - 1]]
                            if context_after > 0:
                                end = min(len(lines), line_num + context_after)
                                result['after'] = [l.rstrip('\n\r') for l in lines[line_num:end]]
                            all_results.append(result)

                if file_has_match and rel_path not in all_files_matched:
                    all_files_matched.append(rel_path)

        # Apply offset and limit
        if output_mode == 'files_with_matches':
            paginated = all_files_matched[offset:offset + limit]
            return {
                'pattern': pattern,
                'total_files': len(all_files_matched),
                'offset': offset,
                'returned': len(paginated),
                'files': paginated,
            }
        elif output_mode == 'count':
            return {
                'pattern': pattern,
                'total_matches': total_matches,
                'by_module': dict(sorted(module_counts.items())),
            }
        else:
            paginated = all_results[offset:offset + limit]
            return {
                'pattern': pattern,
                'total_matches': total_matches,
                'offset': offset,
                'returned': len(paginated),
                'results': paginated,
            }

    @classmethod
    def _impl_code_read(cls, env, arguments, token_data):
        """Read source code from an addon file."""
        config = env['mcp.config'].sudo().get_config()

        file_path = arguments['file_path']
        offset = arguments.get('offset', 1)
        limit = arguments.get('limit')

        # Security validations
        cls._validate_path_security(file_path)

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in cls.CODE_FILE_EXTENSIONS:
            raise ValueError(f"File type not allowed: {ext}")

        max_lines = config.code_read_max_lines or 500
        limit = min(limit, max_lines) if limit else max_lines
        offset = max(1, offset)

        # Find file in addons paths
        addons_paths = cls._get_addons_paths()
        full_path = None

        for addons_path in addons_paths:
            candidate = os.path.join(addons_path, file_path)
            real_candidate = os.path.realpath(candidate)
            real_addons = os.path.realpath(addons_path)

            # Security: ensure resolved path is within addons_path using commonpath
            # This properly handles edge cases that startswith() misses
            try:
                common = os.path.commonpath([real_addons, real_candidate])
                is_contained = (common == real_addons)
            except ValueError:
                # commonpath raises ValueError if paths are on different drives (Windows)
                is_contained = False

            if is_contained and os.path.isfile(real_candidate):
                full_path = real_candidate
                break

        if not full_path:
            raise ValueError(f"File not found: {file_path}")

        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
        except (IOError, OSError) as e:
            raise ValueError(f"Cannot read file: {e}")

        total_lines = len(all_lines)
        start_idx = offset - 1
        end_idx = start_idx + limit
        selected_lines = all_lines[start_idx:end_idx]

        return {
            'file': file_path,
            'start_line': offset,
            'end_line': min(offset + len(selected_lines) - 1, total_lines) if selected_lines else offset,
            'total_lines': total_lines,
            'content': ''.join(selected_lines),
        }

    # -------------------------------------------------------------------------
    # WebApp Code Search Tool
    # -------------------------------------------------------------------------

    @classmethod
    def _impl_search_webapp_code(cls, env, arguments, token_data):
        """Search code within a webapp's stored code fields."""
        config = env['mcp.config'].sudo().get_config()

        webapp_id = arguments['webapp_id']
        pattern = arguments['pattern']
        scope = arguments.get('scope') or None
        case_sensitive = arguments.get('case_sensitive', False)
        context_before = max(arguments.get('context_before', 0), 0)
        context_after = max(arguments.get('context_after', 0), 0)
        max_limit = config.code_search_max_matches or 500
        limit = min(max(arguments.get('limit', 50), 1), max_limit)
        offset = max(arguments.get('offset', 0), 0)

        # Validate regex
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        # Load webapp
        webapp = env['mcp.webapp'].browse(webapp_id)
        if not webapp.exists():
            raise ValueError(f"Webapp not found: {webapp_id}")

        scope_set = set(scope) if scope else None
        all_matches = []

        def _in_scope(name):
            return scope_set is None or name in scope_set

        def search_field(code_text, location, field, **ids):
            """Search a single text field and append matches to all_matches."""
            for m in cls._search_text_lines(regex, code_text, context_before, context_after):
                m.update(location=location, field=field, **ids)
                all_matches.append(m)

        # --- Webapp-level fields ---
        if _in_scope('shared_components'):
            search_field(webapp.shared_components, 'webapp', 'shared_components')
        if _in_scope('shared_styles'):
            search_field(webapp.shared_styles, 'webapp', 'shared_styles')
        if _in_scope('data_code'):
            search_field(webapp.data_code, 'webapp', 'data_code')
        if _in_scope('global_state_code'):
            search_field(webapp.global_state_code, 'webapp', 'global_state_code')
        if _in_scope('custom_imports'):
            search_field(webapp.custom_imports, 'webapp', 'custom_imports')

        # --- Page-level fields ---
        if _in_scope('pages'):
            for page in webapp.page_ids:
                search_field(
                    page.component_code, 'page', 'component_code',
                    page_id=page.id, page_name=page.name,
                )
                search_field(
                    page.data_code, 'page', 'data_code',
                    page_id=page.id, page_name=page.name,
                )

        # --- Page file fields ---
        if _in_scope('page_files'):
            for page in webapp.page_ids:
                for pf in page.component_file_ids.sorted('sequence'):
                    search_field(
                        pf.code, 'page_file', 'code',
                        page_id=page.id, page_name=page.name,
                        file_id=pf.id, file_name=pf.name,
                    )

        # --- Endpoint fields ---
        if _in_scope('endpoints'):
            for ep in webapp.endpoint_ids:
                search_field(
                    ep.handler_code, 'endpoint', 'handler_code',
                    endpoint_id=ep.id, endpoint_name=ep.name,
                )

        # Pagination
        total_matches = len(all_matches)
        paginated = all_matches[offset:offset + limit]

        return {
            'webapp_id': webapp.id,
            'webapp_name': webapp.name,
            'pattern': pattern,
            'total_matches': total_matches,
            'offset': offset,
            'returned': len(paginated),
            'results': paginated,
        }

    # -------------------------------------------------------------------------
    # Module Code Search Tool
    # -------------------------------------------------------------------------

    @classmethod
    def _impl_search_module_code(cls, env, arguments, token_data):
        """Search code within a module's stored file contents."""
        config = env['mcp.config'].sudo().get_config()

        module_id = arguments['module_id']
        pattern = arguments['pattern']
        file_pattern = arguments.get('file_pattern') or None
        case_sensitive = arguments.get('case_sensitive', False)
        context_before = max(arguments.get('context_before', 0), 0)
        context_after = max(arguments.get('context_after', 0), 0)
        max_limit = config.code_search_max_matches or 500
        limit = min(max(arguments.get('limit', 50), 1), max_limit)
        offset = max(arguments.get('offset', 0), 0)

        # Validate regex
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        # Load module
        module = env['mcp.module'].browse(module_id)
        if not module.exists():
            raise ValueError(f"Module not found: {module_id}")

        all_matches = []

        for mfile in module.file_ids:
            # Skip binary-only files (no text content)
            if not mfile.content:
                continue

            # Apply file_pattern filter
            if file_pattern and not fnmatch.fnmatch(mfile.file_path, file_pattern):
                continue

            for m in cls._search_text_lines(regex, mfile.content, context_before, context_after):
                m.update(file_id=mfile.id, file_path=mfile.file_path)
                all_matches.append(m)

        # Pagination
        total_matches = len(all_matches)
        paginated = all_matches[offset:offset + limit]

        return {
            'module_id': module.id,
            'module_name': module.name,
            'pattern': pattern,
            'total_matches': total_matches,
            'offset': offset,
            'returned': len(paginated),
            'results': paginated,
        }

    # -------------------------------------------------------------------------
    # EChart Tool
    # -------------------------------------------------------------------------

    @classmethod
    def _impl_create_echart(cls, env, arguments, token_data):
        """Create an ECharts dashboard record after validating the data code."""
        from .executor import MCPExecutor

        name = arguments['name']
        data_code = arguments['data_code']
        description = arguments.get('description', '')
        share_with_all_users = arguments.get('share_with_all_users', False)

        # Chart options: either chart_options (single) or chart_panels (multi-panel)
        chart_options = arguments.get('chart_options')
        chart_panels = arguments.get('chart_panels')

        # Validate that at least one is provided
        if not chart_options and not chart_panels:
            raise ValueError("Either chart_options or chart_panels must be provided")

        # Use chart_panels if provided, otherwise chart_options
        # Store as chart_options in the model (supports both dict and list)
        if chart_panels:
            if not isinstance(chart_panels, list):
                raise ValueError("chart_panels must be an array of objects")
            if not chart_panels:
                raise ValueError("chart_panels array cannot be empty")
            if not all(isinstance(opt, dict) for opt in chart_panels):
                raise ValueError("chart_panels array must contain only objects")
            chart_options = chart_panels
        else:
            if not isinstance(chart_options, dict):
                raise ValueError("chart_options must be a JSON object")

        # Renderer and responsive options
        renderer = arguments.get('renderer', 'canvas')
        media_queries = arguments.get('media_queries')

        # Advanced options (optional)
        extension_urls = arguments.get('extension_urls')
        pre_init_js = arguments.get('pre_init_js')
        post_init_js = arguments.get('post_init_js')

        # Validate data_code by executing it BEFORE creating the record
        # This ensures the code is valid and works correctly
        # mcp_chart_id=0 is a safe mock — browse(0) returns empty recordset
        try:
            validation_result = MCPExecutor.execute(env, data_code, extra_locals={'mcp_chart_id': 0})
        except Exception as e:
            raise ValueError(
                f"Data code validation failed: {e}\n\n"
                "The code must follow execute_orm rules:\n"
                "- No imports allowed (uses safe_eval)\n"
                "- Assign output to 'result' variable\n"
                "- Use pre-imported helpers (env, datetime, requests, re, hashlib, etc.)"
            )

        # Validate that result is a dict (required for $data.xxx placeholders)
        if not isinstance(validation_result, dict):
            raise ValueError(
                f"Data code must return a dict, got {type(validation_result).__name__}. "
                "Assign a dict to 'result' variable for use with $data.xxx placeholders."
            )

        # Build create values
        create_vals = {
            'name': name,
            'description': description,
            'data_code': data_code,
            'chart_options': chart_options,
            'renderer': renderer,
            'user_id': env.user.id,
            'client_id': token_data.get('client_id'),
            'share_with_all_users': share_with_all_users,
        }

        # Add optional fields only if provided (to use model defaults otherwise)
        if media_queries:
            create_vals['media_queries'] = media_queries
        if extension_urls is not None:
            create_vals['extension_urls'] = extension_urls
        if pre_init_js:
            create_vals['pre_init_js'] = pre_init_js
        if post_init_js:
            create_vals['post_init_js'] = post_init_js

        # Create the echart record
        EChart = env['mcp.echart'].sudo()
        echart = EChart.create(create_vals)

        # Build URLs to view the chart
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        dashboard_url = f"{base_url}/mcp/echart/{echart.id}"
        form_url = f"{base_url}/odoo/mcp.echart/{echart.id}"

        # Build response - conditionally include validation data based on config
        config = env['mcp.config'].sudo().get_config()
        result = {
            'id': echart.id,
            'name': echart.name,
            'created': True,
            'dashboard_url': dashboard_url,
            'form_url': form_url,
            'message': f"EChart dashboard '{name}' created successfully. View dashboard at: {dashboard_url}",
        }

        # Include validation data only if expose setting is enabled
        if config.echart_expose_data:
            result['validation_result'] = validation_result
        else:
            # Provide minimal metadata without actual data values
            result['data_keys'] = list(validation_result.keys()) if validation_result else []

        return result

    # -------------------------------------------------------------------------
    # Shared Code Search Helper
    # -------------------------------------------------------------------------

    @staticmethod
    def _search_text_lines(regex, code_text, context_before=0, context_after=0):
        """Search text line-by-line, return list of match dicts with line/match/context."""
        if not code_text:
            return []
        matches = []
        lines = code_text.splitlines()
        for line_num, line in enumerate(lines, 1):
            if regex.search(line):
                result = {'line': line_num, 'match': line.rstrip()}
                if context_before > 0:
                    start = max(0, line_num - 1 - context_before)
                    result['before'] = [l.rstrip() for l in lines[start:line_num - 1]]
                if context_after > 0:
                    end = min(len(lines), line_num + context_after)
                    result['after'] = [l.rstrip() for l in lines[line_num:end]]
                matches.append(result)
        return matches

    # -------------------------------------------------------------------------
    # WebApp Tool
    # -------------------------------------------------------------------------

    @staticmethod
    def _apply_code_patches(current_code, patches, field_label):
        """Apply find/replace patches to existing code."""
        if not current_code:
            raise ValueError(f"Cannot patch {field_label}: field is empty")
        for i, patch in enumerate(patches):
            find = patch.get('find', '')
            replace = patch.get('replace', '')
            if not find:
                raise ValueError(f"Patch {i+1} for {field_label}: 'find' string is required")
            count = current_code.count(find)
            if count == 0:
                preview = find[:80]
                raise ValueError(
                    f"Patch {i+1} for {field_label}: string not found.\n"
                    f"Looking for: {preview!r}"
                )
            if count > 1:
                preview = find[:80]
                raise ValueError(
                    f"Patch {i+1} for {field_label}: string found {count} times (must be unique).\n"
                    f"Looking for: {preview!r}\n"
                    f"Add more surrounding context to make it unique."
                )
            current_code = current_code.replace(find, replace, 1)
        return current_code

    @classmethod
    def _impl_manage_webapp(cls, env, arguments, token_data):
        """Manage React web applications with pages and endpoints."""
        from .executor import MCPExecutor

        create_webapp = arguments.get('create_webapp')
        update_webapp = arguments.get('update_webapp')

        # Hoist operations that AI models commonly nest inside create_webapp/update_webapp
        # e.g. {"update_webapp": {"webapp_id": 1, "create_pages": [...]}} → top-level create_pages
        _nested_keys = ('create_pages', 'update_pages', 'delete_page_ids',
                        'create_page_files', 'update_page_files', 'delete_page_file_ids',
                        'create_endpoints', 'update_endpoints', 'delete_endpoint_ids',
                        'upload_assets', 'delete_asset_ids')
        for webapp_op in (create_webapp, update_webapp):
            if webapp_op:
                for key in _nested_keys:
                    if key in webapp_op:
                        arguments.setdefault(key, webapp_op.pop(key))

        create_pages = arguments.get('create_pages', [])
        update_pages = arguments.get('update_pages', [])
        delete_page_ids = arguments.get('delete_page_ids', [])
        create_page_files = arguments.get('create_page_files', [])
        update_page_files = arguments.get('update_page_files', [])
        delete_page_file_ids = arguments.get('delete_page_file_ids', [])
        create_endpoints = arguments.get('create_endpoints', [])
        update_endpoints = arguments.get('update_endpoints', [])
        delete_endpoint_ids = arguments.get('delete_endpoint_ids', [])
        upload_assets = arguments.get('upload_assets', [])
        delete_asset_ids = arguments.get('delete_asset_ids', [])

        # Validate mutual exclusivity
        if create_webapp and update_webapp:
            raise ValueError("Cannot use both 'create_webapp' and 'update_webapp'. Choose one.")

        # create_pages/create_endpoints/upload_assets require a webapp context (create_webapp or update_webapp)
        if (create_pages or create_endpoints or upload_assets) and not create_webapp and not update_webapp:
            raise ValueError(
                "create_pages/create_endpoints/upload_assets require 'create_webapp' or 'update_webapp' in the same call to specify the target webapp. "
                "For an existing webapp, include: \"update_webapp\": {\"webapp_id\": <id>}"
            )

        WebApp = env['mcp.webapp']
        Page = env['mcp.webapp.page']
        PageFile = env['mcp.webapp.page.file']
        Endpoint = env['mcp.webapp.endpoint']

        webapp = None
        operation_summary = []

        # =====================================================================
        # Resolve *_patches fields into full field values
        # =====================================================================
        _webapp_patch_fields = {
            'shared_components_patches': 'shared_components',
            'shared_styles_patches': 'shared_styles',
            'data_code_patches': 'data_code',
        }
        if update_webapp:
            wa_record = WebApp.browse(update_webapp['webapp_id'])
            if not wa_record.exists():
                raise ValueError(f"Webapp {update_webapp['webapp_id']} not found")
            for patch_key, base_field in _webapp_patch_fields.items():
                patches = update_webapp.pop(patch_key, None)
                if patches:
                    if update_webapp.get(base_field):
                        raise ValueError(
                            f"Cannot provide both '{base_field}' and '{patch_key}'. Use one or the other."
                        )
                    current = wa_record[base_field] or ''
                    update_webapp[base_field] = cls._apply_code_patches(current, patches, f"webapp {base_field}")

        _page_patch_fields = {
            'component_code_patches': 'component_code',
            'data_code_patches': 'data_code',
        }
        for page in update_pages:
            has_patches = any(page.get(pk) for pk in _page_patch_fields)
            if has_patches:
                page_record = Page.browse(page['page_id'])
                if not page_record.exists():
                    raise ValueError(f"Page {page['page_id']} not found")
                for patch_key, base_field in _page_patch_fields.items():
                    patches = page.pop(patch_key, None)
                    if patches:
                        if page.get(base_field):
                            raise ValueError(
                                f"Cannot provide both '{base_field}' and '{patch_key}' for page {page['page_id']}. Use one or the other."
                            )
                        current = page_record[base_field] or ''
                        page[base_field] = cls._apply_code_patches(current, patches, f"page {page['page_id']} {base_field}")

        _endpoint_patch_fields = {
            'handler_code_patches': 'handler_code',
        }
        for ep in update_endpoints:
            has_patches = any(ep.get(pk) for pk in _endpoint_patch_fields)
            if has_patches:
                ep_record = Endpoint.browse(ep['endpoint_id'])
                if not ep_record.exists():
                    raise ValueError(f"Endpoint {ep['endpoint_id']} not found")
                for patch_key, base_field in _endpoint_patch_fields.items():
                    patches = ep.pop(patch_key, None)
                    if patches:
                        if ep.get(base_field):
                            raise ValueError(
                                f"Cannot provide both '{base_field}' and '{patch_key}' for endpoint {ep['endpoint_id']}. Use one or the other."
                            )
                        current = ep_record[base_field] or ''
                        ep[base_field] = cls._apply_code_patches(current, patches, f"endpoint {ep['endpoint_id']} {base_field}")

        _page_file_patch_fields = {
            'code_patches': 'code',
        }
        for pf in update_page_files:
            has_patches = any(pf.get(pk) for pk in _page_file_patch_fields)
            if has_patches:
                pf_record = PageFile.browse(pf['file_id'])
                if not pf_record.exists():
                    raise ValueError(f"Page file {pf['file_id']} not found")
                for patch_key, base_field in _page_file_patch_fields.items():
                    patches = pf.pop(patch_key, None)
                    if patches:
                        if pf.get(base_field):
                            raise ValueError(
                                f"Cannot provide both '{base_field}' and '{patch_key}' for page file {pf['file_id']}. Use one or the other."
                            )
                        current = pf_record[base_field] or ''
                        pf[base_field] = cls._apply_code_patches(current, patches, f"page file {pf['file_id']} {base_field}")

        # =====================================================================
        # Validate all data code BEFORE any writes
        # Tuple format: (label, code, extra_locals)
        # =====================================================================
        codes_to_validate = []

        # Determine mcp_webapp_id for validation context
        # On update the real ID is available; on create use 0 (browse(0) = empty recordset)
        if update_webapp:
            mock_webapp_id = update_webapp['webapp_id']
        else:
            mock_webapp_id = 0

        # Webapp data_code context
        webapp_context = {'mcp_webapp_id': mock_webapp_id}
        if create_webapp and create_webapp.get('data_code'):
            codes_to_validate.append(('webapp initial data', create_webapp['data_code'], webapp_context))
        if update_webapp and update_webapp.get('data_code'):
            codes_to_validate.append(('webapp initial data', update_webapp['data_code'], webapp_context))

        # Page data_code has route_params available
        # Use defaultdict returning '0' so route_params['id'] works (returns '0' for any key)
        mock_route_params = defaultdict(lambda: '0')
        page_context = {'route_params': mock_route_params, 'mcp_webapp_id': mock_webapp_id, 'mcp_page_id': 0}
        for i, page in enumerate(create_pages):
            if page.get('data_code'):
                codes_to_validate.append((f"page '{page.get('name', i)}' data", page['data_code'], page_context))

        for page in update_pages:
            ctx = {**page_context, 'mcp_page_id': page['page_id']}
            if page.get('data_code'):
                codes_to_validate.append((f"page {page['page_id']} data", page['data_code'], ctx))

        # Endpoint handler_code has query_params, body, route_params available
        # Use defaultdict so direct key access like route_params['id'] returns '0'
        mock_query_params = defaultdict(lambda: '')
        _ns = type('NS', (), {'__getattr__': lambda s, k: ''})
        mock_httprequest = _ns()
        mock_httprequest.remote_addr = '127.0.0.1'
        mock_httprequest.headers = {}
        mock_httprequest.cookies = {}
        mock_httprequest.method = 'GET'
        mock_httprequest.content_type = 'application/json'
        mock_httprequest.data = b'{}'
        mock_request = _ns()
        mock_request.httprequest = mock_httprequest
        mock_request.session = _ns()
        mock_request.session.sid = 'mock-session'
        endpoint_context = {
            'query_params': mock_query_params, 'body': {}, 'route_params': mock_route_params,
            'mcp_webapp_id': mock_webapp_id, 'mcp_endpoint_id': 0,
            'request': mock_request,
        }
        for i, ep in enumerate(create_endpoints):
            if ep.get('handler_code'):
                codes_to_validate.append((f"endpoint '{ep.get('name', i)}' handler", ep['handler_code'], endpoint_context))

        for ep in update_endpoints:
            ctx = {**endpoint_context, 'mcp_endpoint_id': ep['endpoint_id']}
            if ep.get('handler_code'):
                codes_to_validate.append((f"endpoint {ep['endpoint_id']} handler", ep['handler_code'], ctx))

        # Validate all code
        for label, code, extra_locals in codes_to_validate:
            try:
                MCPExecutor.execute(env, code, extra_locals=extra_locals)
            except Exception as e:
                raise ValueError(
                    f"Code validation failed for {label}: {e}\n\n"
                    "Code must follow execute_orm rules:\n"
                    "- No imports allowed\n"
                    "- Assign output to 'result' variable\n"
                    "- Use pre-imported helpers (env, datetime, etc.)"
                )

        # =====================================================================
        # Create or Update WebApp
        # =====================================================================
        if create_webapp:
            # Start with provided fields, then set ownership
            webapp_vals = dict(create_webapp)
            webapp_vals['user_id'] = env.user.id
            webapp_vals['client_id'] = token_data.get('client_id')

            webapp = WebApp.create(webapp_vals)
            operation_summary.append(f"Created webapp '{webapp.name}' (id={webapp.id})")

        elif update_webapp:
            webapp_id = update_webapp['webapp_id']
            webapp = WebApp.browse(webapp_id)
            if not webapp.exists():
                raise ValueError(f"Webapp not found: {webapp_id}")

            # Pass through all fields except webapp_id (used to identify record)
            update_vals = {k: v for k, v in update_webapp.items() if k != 'webapp_id'}

            if update_vals:
                webapp.write(update_vals)
                operation_summary.append(f"Updated webapp '{webapp.name}' (id={webapp.id})")

        # =====================================================================
        # Derive webapp from pages/endpoints if not provided via create/update_webapp
        # =====================================================================
        if webapp is None:
            # Try to derive webapp from the first page, file, or endpoint operation
            if update_pages:
                first_page = Page.browse(update_pages[0]['page_id'])
                if first_page.exists():
                    webapp = first_page.webapp_id
            elif delete_page_ids:
                first_page = Page.browse(delete_page_ids[0])
                if first_page.exists():
                    webapp = first_page.webapp_id
            elif create_page_files:
                first_page = Page.browse(create_page_files[0]['page_id'])
                if first_page.exists():
                    webapp = first_page.webapp_id
            elif update_page_files:
                first_file = PageFile.browse(update_page_files[0]['file_id'])
                if first_file.exists():
                    webapp = first_file.page_id.webapp_id
            elif delete_page_file_ids:
                first_file = PageFile.browse(delete_page_file_ids[0])
                if first_file.exists():
                    webapp = first_file.page_id.webapp_id
            elif update_endpoints:
                first_endpoint = Endpoint.browse(update_endpoints[0]['endpoint_id'])
                if first_endpoint.exists():
                    webapp = first_endpoint.webapp_id
            elif delete_endpoint_ids:
                first_endpoint = Endpoint.browse(delete_endpoint_ids[0])
                if first_endpoint.exists():
                    webapp = first_endpoint.webapp_id
            elif delete_asset_ids:
                WebApp = env['mcp.webapp']
                found = WebApp.search([('asset_ids', 'in', delete_asset_ids[0])], limit=1)
                if found:
                    webapp = found

            if webapp is None:
                raise ValueError("Could not determine target webapp. Provide 'update_webapp' with webapp_id or valid page/endpoint/file/asset IDs.")

        # =====================================================================
        # Page Operations
        # =====================================================================
        # Delete pages first
        if delete_page_ids:
            pages_to_delete = Page.browse(delete_page_ids)
            # When webapp was derived, allow deleting any pages the user has access to
            # When webapp was explicitly provided, filter to only that webapp's pages
            if create_webapp or update_webapp:
                valid_pages = pages_to_delete.filtered(lambda p: p.webapp_id.id == webapp.id)
            else:
                valid_pages = pages_to_delete.filtered(lambda p: p.exists())
            if valid_pages:
                valid_pages.unlink()
                operation_summary.append(f"Deleted {len(valid_pages)} page(s)")

        # Update pages
        for page_update in update_pages:
            page_id = page_update['page_id']
            page = Page.browse(page_id)
            if not page.exists():
                raise ValueError(f"Page not found: {page_id}")

            # Pass through all fields except page_id (used to identify record)
            page_vals = {k: v for k, v in page_update.items() if k != 'page_id'}

            if page_vals:
                page.write(page_vals)
                operation_summary.append(f"Updated page '{page.name}' (id={page.id})")

        # Create pages
        created_pages = []
        for page_data in create_pages:
            # Extract inline component_files before creating the page
            inline_files = page_data.pop('component_files', None)

            # Start with provided fields, then set webapp_id
            page_vals = dict(page_data)
            page_vals['webapp_id'] = webapp.id

            page = Page.create(page_vals)
            created_pages.append({'id': page.id, 'name': page.name, 'route_path': page.route_path})
            operation_summary.append(f"Created page '{page.name}' (id={page.id})")

            # Create inline component files for this page
            if inline_files:
                for file_data in inline_files:
                    file_vals = dict(file_data)
                    file_vals['page_id'] = page.id
                    PageFile.create(file_vals)
                operation_summary.append(f"Created {len(inline_files)} component file(s) for page '{page.name}'")

        # =====================================================================
        # Page File Operations
        # =====================================================================
        # Delete files first
        if delete_page_file_ids:
            files_to_delete = PageFile.browse(delete_page_file_ids).filtered(lambda f: f.exists())
            if files_to_delete:
                files_to_delete.unlink()
                operation_summary.append(f"Deleted {len(files_to_delete)} component file(s)")

        # Update files
        for pf_update in update_page_files:
            file_id = pf_update['file_id']
            pf = PageFile.browse(file_id)
            if not pf.exists():
                raise ValueError(f"Page file not found: {file_id}")

            file_vals = {k: v for k, v in pf_update.items() if k != 'file_id'}
            if file_vals:
                pf.write(file_vals)
                operation_summary.append(f"Updated component file '{pf.name}' (id={pf.id})")

        # Create files (standalone, for existing pages)
        for pf_data in create_page_files:
            file_vals = dict(pf_data)
            pf = PageFile.create(file_vals)
            operation_summary.append(f"Created component file '{pf.name}' (id={pf.id})")

        # =====================================================================
        # Endpoint Operations
        # =====================================================================
        # Delete endpoints first
        if delete_endpoint_ids:
            endpoints_to_delete = Endpoint.browse(delete_endpoint_ids)
            # When webapp was explicitly provided, filter to only that webapp's endpoints
            if create_webapp or update_webapp:
                valid_endpoints = endpoints_to_delete.filtered(lambda e: e.webapp_id.id == webapp.id)
            else:
                valid_endpoints = endpoints_to_delete.filtered(lambda e: e.exists())
            if valid_endpoints:
                valid_endpoints.unlink()
                operation_summary.append(f"Deleted {len(valid_endpoints)} endpoint(s)")

        # Update endpoints
        for ep_update in update_endpoints:
            ep_id = ep_update['endpoint_id']
            endpoint = Endpoint.browse(ep_id)
            if not endpoint.exists():
                raise ValueError(f"Endpoint not found: {ep_id}")

            # Pass through all fields except endpoint_id (used to identify record)
            ep_vals = {k: v for k, v in ep_update.items() if k != 'endpoint_id'}

            if ep_vals:
                endpoint.write(ep_vals)
                operation_summary.append(f"Updated endpoint '{endpoint.name}' (id={endpoint.id})")

        # Create endpoints
        created_endpoints = []
        for ep_data in create_endpoints:
            # Start with provided fields, then set webapp_id
            ep_vals = dict(ep_data)
            ep_vals['webapp_id'] = webapp.id

            endpoint = Endpoint.create(ep_vals)
            created_endpoints.append({
                'id': endpoint.id,
                'name': endpoint.name,
                'path': endpoint.endpoint_path,
                'method': endpoint.method
            })
            operation_summary.append(f"Created endpoint '{endpoint.name}' (id={endpoint.id})")

        # =====================================================================
        # Asset Operations
        # =====================================================================
        # Delete assets first
        if delete_asset_ids:
            Attachment = env['ir.attachment']
            assets_to_delete = Attachment.browse(delete_asset_ids).filtered(
                lambda a: a.exists() and a.id in webapp.asset_ids.ids
            )
            if assets_to_delete:
                assets_to_delete.unlink()
                operation_summary.append(f"Deleted {len(assets_to_delete)} asset(s)")

        # Upload assets
        if upload_assets:
            Attachment = env['ir.attachment']
            for asset_data in upload_assets:
                filename = asset_data['filename']
                url = asset_data.get('url')
                data = asset_data.get('data')
                mime_type = asset_data.get('mime_type')

                if not url and not data:
                    raise ValueError(f"Asset '{filename}': provide either 'url' or 'data'.")

                if url:
                    response = requests.get(url, timeout=30)
                    response.raise_for_status()
                    file_data = base64.b64encode(response.content)
                    if not mime_type:
                        mime_type = response.headers.get('Content-Type', '').split(';')[0].strip()
                else:
                    file_data = data

                att_vals = {
                    'name': filename,
                    'type': 'binary',
                    'datas': file_data,
                    'res_model': 'mcp.webapp',
                    'res_id': webapp.id,
                    'res_field': 'asset_ids',
                }
                if mime_type:
                    att_vals['mimetype'] = mime_type

                att = Attachment.create(att_vals)
                operation_summary.append(f"Uploaded asset '{filename}' (id={att.id})")

        # =====================================================================
        # Build Response
        # =====================================================================
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        # Use slug for URL if available, otherwise fall back to ID
        # Prefer bare slug URL, fall back to /mcp/webapp/<id>
        if webapp.slug:
            app_url = f"{base_url}/{webapp.slug}"
        else:
            app_url = f"{base_url}/mcp/webapp/{webapp.id}"
        form_url = f"{base_url}/odoo/mcp.webapp/{webapp.id}"

        return {
            'webapp_id': webapp.id,
            'slug': webapp.slug,
            'name': webapp.name,
            'app_url': app_url,
            'form_url': form_url,
            'operations': operation_summary,
            'pages': [{
                          'id': p.id, 'name': p.name, 'route_path': p.route_path,
                          'files': [{'id': f.id, 'name': f.name} for f in p.component_file_ids],
                      } for p in webapp.page_ids],
            'endpoints': [{'id': e.id, 'name': e.name, 'path': e.endpoint_path, 'method': e.method}
                          for e in webapp.endpoint_ids],
            'assets': [{'id': a.id, 'filename': a.name, 'url': f"{app_url}/asset/{a.name}",
                         'size': a.file_size, 'mimetype': a.mimetype}
                        for a in webapp.asset_ids],
            'message': f"WebApp '{webapp.name}' managed successfully. View app at: {app_url}",
        }

    # -------------------------------------------------------------------------
    # Module Maker Tool
    # -------------------------------------------------------------------------

    @classmethod
    def _impl_manage_module(cls, env, arguments, token_data):
        """Manage importable Odoo data modules."""
        create_module = arguments.get('create_module')
        update_module = arguments.get('update_module')

        # Hoist nested operations (AI models commonly nest these)
        _nested_keys = ('create_files', 'update_files', 'delete_file_ids',
                        'skip_install', 'force', 'uninstall_first')
        for module_op in (create_module, update_module):
            if module_op:
                for key in _nested_keys:
                    if key in module_op:
                        arguments.setdefault(key, module_op.pop(key))

        create_files = arguments.get('create_files', [])
        update_files = arguments.get('update_files', [])
        delete_file_ids = arguments.get('delete_file_ids', [])
        skip_install = arguments.get('skip_install', False)
        force = arguments.get('force', False)
        uninstall_first = arguments.get('uninstall_first', False)

        # Validate mutual exclusivity
        if create_module and update_module:
            raise ValueError("Cannot use both 'create_module' and 'update_module'. Choose one.")

        # All operations require explicit module context
        has_file_ops = create_files or update_files or delete_file_ids
        if has_file_ops and not create_module and not update_module:
            raise ValueError(
                "File operations require 'create_module' or 'update_module' in the same call. "
                "For an existing module, include: \"update_module\": {\"module_id\": <id>}"
            )
        if not create_module and not update_module:
            raise ValueError(
                "Either 'create_module' or 'update_module' is required."
            )

        Module = env['mcp.module']
        File = env['mcp.module.file']

        module = None
        operation_summary = []

        # =====================================================================
        # Resolve content_patches for update_files
        # =====================================================================
        for file_update in update_files:
            patches = file_update.pop('content_patches', None)
            if patches:
                if file_update.get('content'):
                    raise ValueError(
                        f"Cannot provide both 'content' and 'content_patches' for file {file_update['file_id']}. Use one or the other."
                    )
                file_record = File.browse(file_update['file_id'])
                if not file_record.exists():
                    raise ValueError(f"File {file_update['file_id']} not found")
                current = file_record.content or ''
                file_update['content'] = cls._apply_code_patches(current, patches, f"file {file_update['file_id']}")

        # =====================================================================
        # Validate XML well-formedness for .xml files
        # =====================================================================
        def _validate_xml(content, label):
            if not content or not content.strip():
                return
            try:
                etree.fromstring(content.encode('utf-8'))
            except etree.XMLSyntaxError as e:
                raise ValueError(f"XML validation failed for {label}: {e}")

        for i, f in enumerate(create_files):
            if f.get('content') and f.get('binary_content'):
                raise ValueError(
                    f"File '{f.get('file_path', f'index {i}')}': cannot provide both 'content' and "
                    f"'binary_content'. Use 'content' for normal files, 'binary_content' only for "
                    f"actual binary data (images, fonts, etc.)."
                )
            if f.get('file_path', '').endswith('.xml') and f.get('content'):
                _validate_xml(f['content'], f"new file '{f['file_path']}'")

        for f in update_files:
            if f.get('content') and f.get('binary_content'):
                raise ValueError(
                    f"File {f['file_id']}: cannot provide both 'content' and "
                    f"'binary_content'. Use 'content' for normal files, 'binary_content' only for "
                    f"actual binary data (images, fonts, etc.)."
                )
            if f.get('content'):
                file_path = f.get('file_path')
                if not file_path:
                    file_record = File.browse(f['file_id'])
                    if file_record.exists():
                        file_path = file_record.file_path
                if file_path and file_path.endswith('.xml'):
                    _validate_xml(f['content'], f"file {f['file_id']} ({file_path})")

        # =====================================================================
        # Create or Update Module
        # =====================================================================
        if create_module:
            module_vals = dict(create_module)
            module_vals['user_id'] = env.user.id
            module = Module.create(module_vals)
            operation_summary.append(f"Created module '{module.name}' (id={module.id}, technical_name={module.technical_name})")

        elif update_module:
            module_id = update_module['module_id']
            module = Module.browse(module_id)
            if not module.exists():
                raise ValueError(f"Module not found: {module_id}")
            update_vals = {k: v for k, v in update_module.items() if k != 'module_id'}
            if update_vals:
                module.write(update_vals)
                operation_summary.append(f"Updated module '{module.name}' (id={module.id})")

        # =====================================================================
        # Validate update_files ownership
        # =====================================================================
        if update_files:
            update_ids = [f['file_id'] for f in update_files]
            files_to_update = File.browse(update_ids).filtered(lambda f: f.exists())
            wrong_module = files_to_update.filtered(lambda f: f.module_id != module)
            if wrong_module:
                raise ValueError(
                    f"Cannot update files from other modules: "
                    + ", ".join(f"'{f.file_path}' (module={f.module_id.name})" for f in wrong_module)
                )

        # =====================================================================
        # Delete files
        # =====================================================================
        if delete_file_ids:
            files_to_delete = File.browse(delete_file_ids).filtered(lambda f: f.exists())
            # Validate all files belong to this module
            wrong_module = files_to_delete.filtered(lambda f: f.module_id != module)
            if wrong_module:
                raise ValueError(
                    f"Cannot delete files from other modules: "
                    + ", ".join(f"'{f.file_path}' (module={f.module_id.name})" for f in wrong_module)
                )
            if files_to_delete:
                files_to_delete.unlink()
                operation_summary.append(f"Deleted {len(files_to_delete)} file(s)")

        # =====================================================================
        # Create files
        # =====================================================================
        created_file_infos = []
        for file_data in create_files:
            file_vals = dict(file_data)
            file_vals['module_id'] = module.id
            f = File.create(file_vals)
            created_file_infos.append({'id': f.id, 'file_path': f.file_path})
            operation_summary.append(f"Created file '{f.file_path}' (id={f.id})")

        # =====================================================================
        # Update files
        # =====================================================================
        updated_file_infos = []
        for file_update in update_files:
            file_id = file_update['file_id']
            f = File.browse(file_id)
            if not f.exists():
                raise ValueError(f"File not found: {file_id}")
            file_vals = {k: v for k, v in file_update.items() if k != 'file_id'}
            if file_vals:
                f.write(file_vals)
                updated_file_infos.append({'id': f.id, 'file_path': f.file_path})
                operation_summary.append(f"Updated file '{f.file_path}' (id={f.id})")

        # =====================================================================
        # Auto Package + Install
        # Triggers when: not skipped AND manifest exists AND
        #   (files changed in this call OR pending changes from prior calls)
        # =====================================================================
        files_mutated = bool(create_files or update_files or delete_file_ids)
        manifest_warnings = []

        should_install = (
            not skip_install
            and module._get_manifest_file()
            and (files_mutated or module.files_changed)
        )

        if skip_install and files_mutated:
            operation_summary.append(
                "Install skipped (skip_install=true). Files are saved — "
                "call again without skip_install to package and install."
            )
        elif should_install:
            # Check manifest file references (warnings for unreferenced files)
            manifest_warnings = module._validate_manifest_files()
            if manifest_warnings:
                operation_summary.append(
                    f"Warning: {len(manifest_warnings)} file(s) not referenced in "
                    f"__manifest__.py 'data' list and will be ignored on install: "
                    + ", ".join(manifest_warnings)
                )

            # Installation requires MCP Admin access
            if not env.user.has_group('odoo_remote_mcp.group_mcp_admin'):
                # Package only — user can manage files but not install
                module.action_package()
                operation_summary.append(
                    f"Packaged module '{module.name}' as {module.zip_filename}. "
                    f"Installation requires MCP Admin access."
                )
            else:
                # Use a savepoint so that if install fails, only the install
                # is rolled back — file writes in the outer transaction survive.
                try:
                    with env.cr.savepoint():
                        module.action_install(
                            force=force, uninstall_first=uninstall_first,
                        )
                    label = "Reinstalled (uninstall+install)" if uninstall_first else "Installed"
                    operation_summary.append(f"{label} module '{module.name}' (state={module.state})")
                    if module.last_error:
                        operation_summary.append(f"Import warnings: {module.last_error}")
                except Exception as e:
                    # Install failed — savepoint rolled back the partial install,
                    # but file changes are preserved in the outer transaction.
                    # Invalidate ORM cache after rollback to prevent stale cache
                    # errors (e.g. "dictionary changed size during iteration")
                    # when accessing computed fields in the response builder.
                    module.invalidate_recordset()
                    error_msg = str(e)
                    module.write({
                        'last_error': error_msg,
                        'files_changed': True,
                    })
                    operation_summary.append(
                        f"Install FAILED (file changes saved): {error_msg}"
                    )

        # =====================================================================
        # Build Response
        # =====================================================================
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        form_url = f"{base_url}/odoo/mcp.module/{module.id}"

        result = {
            'module_id': module.id,
            'name': module.name,
            'technical_name': module.technical_name,
            'state': module.state,
            'files_changed': module.files_changed,
            'installed_module_id': module.installed_module_id.id if module.installed_module_id else False,
            'form_url': form_url,
            'operations': operation_summary,
            'message': f"Module '{module.name}' ({module.technical_name}) managed successfully. State: {module.state}",
        }
        if created_file_infos:
            result['created_files'] = created_file_infos
        if updated_file_infos:
            result['updated_files'] = updated_file_infos
        if manifest_warnings:
            result['manifest_warnings'] = manifest_warnings
        return result
