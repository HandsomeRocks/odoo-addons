MCP Server for Odoo
===================

Enable AI agents like Claude, Gemini, and ChatGPT to interact with your Odoo instance
via the Model Context Protocol (MCP).

Setup
-----

1. Install the module
2. Add users to the **MCP User** group
3. Configure tool allowlists in **MCP Server → Configuration**
4. Connect your AI client (see Quick Connect below)

Quick Connect
-------------

Replace ``https://your-odoo.com`` with your Odoo instance URL.

**Claude Code CLI**::

    claude mcp add odoo --transport http https://your-odoo.com/mcp
    within claude cli use /mcp command and authenticate.

**Claude Desktop / Claude.ai**

Settings → Connectors → Add Custom Connector → Enter URL::

    https://your-odoo.com/mcp

Browser opens for OAuth → Authorize → Ready

Guide: https://modelcontextprotocol.io/docs/develop/connect-remote-servers#connecting-to-a-remote-mcp-server

**Gemini CLI**

Edit ``~/.gemini/settings.json``::

    {
      "mcpServers": {
        "odoo": {
          "url": "https://your-odoo.com/mcp"
        }
      }
    }

within gemini cli, use /mcp command and authenticate

**ChatGPT (Developer Mode)**

In chatgpt settings → Apps menu → Advanced settings → Enable Developer Mode → Create app and add MCP server URL::

    https://your-odoo.com/mcp

Browser opens for OAuth → Authorize → Ready

Guide: https://platform.openai.com/docs/guides/developer-mode

Requirements
------------

- **HTTPS** required for production
- **ripgrep** (optional) - Faster code search

Install ripgrep
~~~~~~~~~~~~~~~

::

    # Ubuntu/Debian
    sudo apt install ripgrep

    # macOS
    brew install ripgrep

    # Windows
    choco install ripgrep

Testing without HTTPS (ngrok)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For local development without HTTPS::

    # Expose local Odoo via HTTPS tunnel
    ngrok http 8069

    # Use generated URL, set as web base url in system parameters: https://xxx.ngrok-free.app/mcp

Endpoints
---------

- ``/mcp`` - MCP protocol endpoint
- ``/mcp/docs`` - Built-in documentation
- ``/.well-known/oauth-protected-resource`` - OAuth resource metadata
- ``/.well-known/oauth-authorization-server`` - OAuth server metadata

Multi-Database Support
----------------------

For environments with multiple databases served from a single Odoo URL, the module
supports path-based database routing.

**Requirements**

Add the module to ``server_wide_modules`` in your ``odoo.conf``::

    server_wide_modules = web,base,odoo_remote_mcp

**Connection URLs**

Use the database name in the URL path when connecting::

    https://your-odoo.com/<database>/mcp

**Examples**

Claude Code CLI::

    claude mcp add production --transport http https://your-odoo.com/production_db/mcp
    claude mcp add staging --transport http https://your-odoo.com/staging_db/mcp

Gemini CLI (``~/.gemini/settings.json``)::

    {
      "mcpServers": {
        "odoo_production": {
          "url": "https://your-odoo.com/production_db/mcp"
        },
        "odoo_staging": {
          "url": "https://your-odoo.com/staging_db/mcp"
        }
      }
    }

**Path-based Endpoints**

All endpoints support the database path prefix:

- ``/<database>/mcp`` - MCP protocol endpoint
- ``/<database>/mcp/docs`` - Built-in documentation
- ``/<database>/oauth/authorize`` - OAuth authorization
- ``/<database>/oauth/token`` - Token endpoint
- ``/.well-known/oauth-protected-resource/<database>/mcp`` - Resource metadata

Changelog
---------
**17.0.4.7.0** — 2026-03-03 (read_group Tool)
    - New ``read_group`` tool for data aggregations using GROUP BY with SUM, AVG, COUNT, MIN, MAX, etc.
    - Uses Odoo's ``formatted_read_group()`` API — respects all access rights and record rules
    - Supports date/datetime granularity grouping (day, week, month, quarter, year)
    - Supports ``having`` clause for filtering on aggregate results
    - Auto-enabled for all MCP users with read scope (no allowlist required)
    - Tool count increased from 16 to 17

**17.0.4.6.0** — 2026-02-28 (Safe Eval Utilities & API Keys)
    - Pre-imported Python utilities in safe_eval sandbox: ``requests``, ``re``, ``hashlib``, ``hmac``, ``base64``, ``math``, ``itertools``, ``html``
    - All modules wrapped via Odoo's ``wrap_module`` with whitelisted attributes for security
    - Enables server-side HTTP calls from webapp endpoints (call external APIs, webhooks, n8n, etc.)
    - Enables webhook signature verification via ``hashlib``/``hmac`` directly in endpoint handlers
    - Updated tool descriptions for ``execute_orm``, ``manage_webapp``, and ``create_echart`` to document new pre-imports
    - API Key model (``mcp.api.key``) documented in ``manage_webapp`` tool description for third-party key storage
    - API Key form view: updated usage guide with Claude Chat Bot quick-start link and clearer instructions
    - Updated documentation (index.html): Claude Chat Bot showcase, API Keys section, AI Chatbots link to GitHub app library

**17.0.4.5.0** — 2026-02-26 (Context Variables)
    - Auto-injected record IDs (``mcp_chart_id``, ``mcp_webapp_id``, ``mcp_page_id``, ``mcp_endpoint_id``) in all code execution contexts — eliminates hardcoded IDs
    - Validation mock contexts pass safe defaults during pre-create code validation
    - Documentation improvements for configuration dashboard and tool settings

**17.0.4.4.0** — 2026-02-16 (Bare Slug Routes & SEO)
    **Clean URLs & Social Sharing**

    - Bare slug routes: webapps with a slug are now accessible at ``/<slug>`` (e.g., ``/space-invaders``)
    - Existing ``/mcp/webapp/<id|slug>`` routes preserved for backwards compatibility
    - API base URL now uses slug instead of numeric ID for portability across environments
    - SEO meta tags: canonical URL, Open Graph (title, description, image, site name, type)
    - Twitter Card meta tags for rich link previews on Twitter/X
    - PWA scope and start URL updated to use bare slug for cleaner installed app experience
    - Slug help text updated with route overlap guidance

**17.0.4.3.0** — 2026-02-13 (Progressive Web App Support)
    **PWA for Web Applications**

    - Enable PWA on any webapp for installable, fullscreen, native-like app experience
    - Manifest, service worker, and icon routes auto-generated from webapp settings
    - Configurable display mode (standalone/fullscreen), theme color, and background color
    - Automatic icon generation from webapp thumbnail with solid-color fallback
    - Apple iOS meta tags for Add to Home Screen support
    - PWA fields included in webapp CSV export/import
    - PWA fields exposed in ``manage_webapp`` tool schema for AI agent configuration

**17.0.4.2.0** — 2026-02-13 (Webapp Asset Support)
    **Binary Assets for Web Applications**

    - Upload images, audio, sprites, and other binary files as webapp assets
    - Two upload methods: URL-based (server fetches with auto MIME detection) or base64-encoded data
    - Serve assets via cacheable public endpoint ``/mcp/webapp/<slug>/asset/<filename>``
    - ``asset(filename)`` helper available in all page components via props and ``useApp()`` hook
    - Assets tab in webapp form view with usage examples
    - Full export/import support — assets included in webapp CSV export with slug-based URLs

**17.0.4.1.0** — 2026-02-12 (Code Search Tools)
    **Search Within WebApp & Module Code**

    - New ``search_webapp_code`` tool — search within webapp code fields (components, styles,
      data code, endpoints) by regex pattern with pagination and context lines
    - New ``search_module_code`` tool — search within module file contents (XML, Python, CSV,
      JS, CSS) by regex pattern with file glob filtering, pagination, and context lines
    - Both tools enable finding exact code locations before applying surgical ``content_patches``
    - Shared ``_search_text_lines`` helper extracted to DRY up the line-by-line search logic
    - Tool count increased from 14 to 16

**17.0.4.0.0** — 2026-02-09 (AI Module Builder)
    **Create Custom Odoo Data Modules with AI**

    - New ``manage_module`` tool for creating and managing custom Odoo data modules
    - Create models, fields, views, menus, security rules, and server actions via XML/CSV data files
    - Automatic ZIP packaging and one-click installation via ``base_import_module``
    - Modules persist across restarts and appear in the standard Apps menu
    - Full lifecycle management: create, package, install, update, and uninstall
    - Download packaged ZIP files for backup or manual deployment
    - Per-user allowlist access control for the manage_module tool
    - Module form view with file editor, download tab, and chatter activity tracking
    - Tool count increased from 13 to 14
    - Major rebrand: "Odoo MCP Studio — AI React App, Module & EChart Builder"

**17.0.3.2.0** — 2026-02-06 (Code Patches & Embed)
    - Code patching for ``manage_webapp``: surgical find/replace edits via ``*_patches`` fields
      (e.g. ``component_code_patches``, ``shared_components_patches``, ``handler_code_patches``)
      instead of rewriting entire code fields — saves context and reduces errors
    - Iframe embed code for web applications: one-click copy snippet in the Sharing tab
    - Improved storage documentation in tool description: clarifies ``mcp.webapp.user.storage``
      model is queryable server-side for cross-user features (leaderboards, aggregations)

**17.0.3.1.0** — 2026-01-30 (Tags, Gallery and webapp slugs)
    - Tags system for categorizing webapps/echarts with management view
    - Webapp enhancements: thumbnail, view count, sequence ordering, webapp slug support
    - EChart iframe embed option with one-click copy in Sharing tab

**17.0.3.0.0** — 2026-01-20 (React Web Applications)
    **Build Full React Apps from Odoo**

    - New ``manage_webapp`` tool for creating and managing React 19 web applications
    - Multi-page routing with React Router 6 (HashRouter or MemoryRouter)
    - Tailwind CSS enabled by default for utility-first styling
    - ESM import maps: add any library from esm.sh (Chart.js, Recharts, etc.)
    - Browser-based JSX compilation with Babel — no server build step
    - Python data binding: fetch Odoo data with Python, access via React props
    - Custom API endpoints: define GET/POST/PUT/DELETE handlers with Python
    - Persistent user storage: server-side (5MB) with localStorage cache
    - User context in components: id, name, email, roles, company info
    - Flexible sharing: public, portal, groups, or specific users
    - Version control: export/import webapps as XLSX
    - Tool count increased from 12 to 13
    - Major rebrand: "Odoo MCP Studio — AI App & EChart Builder"

**17.0.2.1.0** — 2026-01-12 (Advanced ECharts)
    **Extensions & Custom JavaScript Support**

    - ECharts extensions: 4 popular extensions loaded by default (echarts-gl, wordcloud, liquidfill, stat)
    - Pre-init JavaScript: code that runs before chart renders (registerMap, registerTheme, registerTransform)
    - Post-init JavaScript: code that runs after chart renders (event handlers, drill-down, exports)
    - New prompt templates: "3D Globe Command Center" and "Word Cloud Dashboard"
    - Advanced tab in EChart form view for extension and JavaScript configuration
    - CORS-friendly texture URLs documented for 3D globe visualizations

**17.0.2.0.0** — 2026-01-08 (Major Release)
    **ECharts Interactive Dashboards**

    - New ``create_echart`` tool for AI-generated dashboards using Apache ECharts
    - Vibe coding for dashboards: describe what you want to visualize, AI builds it
    - Blank canvas for AI: no rigid widgets, complete creative freedom
    - No configuration lock-in: pure Python data code and JSON chart options
    - Full data isolation support: toggle "Expose EChart Data in Tool Response" to keep data on server
    - Flexible sharing: public links via secure tokens or internal user/group sharing
    - Tool count increased from 11 to 12

**17.0.1.1.2** — 2026-01-05
    - Improved transaction handling: tool execution now uses savepoints to rollback partial changes on failure
    - Added full traceback logging to execution logs for easier debugging
    - Defensive user_id capture before tool execution

**17.0.1.1.1**
    - Fixed "Invalid access mode" error affecting Calendar and other views

**17.0.1.1.0** — 2026-01-03
    - Added support for multi-database environments
    - Minor fixes

**17.0.1.0.0** — 2026-01-01
    Initial release
