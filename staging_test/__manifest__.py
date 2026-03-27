{
    "name": "Staging Test",
    "version": "17.0.1.0.0",
    "category": "Tools",
    "summary": "Simple module to verify Odoo staging deployments",
    "description": """
        Adds a 'Test Notes' menu under Settings to confirm that:
        - Custom addons are mounted and loaded correctly
        - The database was cloned and is writable
        - The Odoo instance is fully operational
    """,
    "author": "Staging Manager",
    "depends": ["base"],
    "data": [
        "security/ir.model.access.csv",
        "views/test_note_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
