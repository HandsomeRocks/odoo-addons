from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStagingNote(TransactionCase):
    """Verify the staging_test module is installed and functional."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Note = cls.env["staging.test.note"]

    def test_create_note(self):
        """A basic note can be created with required fields."""
        note = self.Note.create({"name": "Deployment check"})
        self.assertTrue(note.id)
        self.assertEqual(note.name, "Deployment check")
        self.assertEqual(note.tag, "info")
        self.assertFalse(note.is_resolved)

    def test_default_values(self):
        """Default field values are applied correctly."""
        note = self.Note.create({"name": "Defaults"})
        self.assertEqual(note.tag, "info")
        self.assertEqual(note.priority, "0")
        self.assertFalse(note.is_resolved)

    def test_tag_selection(self):
        """All tag values can be set."""
        for tag_val in ("info", "success", "warning", "bug"):
            note = self.Note.create({"name": f"Tag {tag_val}", "tag": tag_val})
            self.assertEqual(note.tag, tag_val)

    def test_resolve_note(self):
        """A note can be marked as resolved."""
        note = self.Note.create({"name": "Fix me"})
        note.is_resolved = True
        self.assertTrue(note.is_resolved)

    def test_ordering(self):
        """Notes are ordered by create_date descending (newest first)."""
        n1 = self.Note.create({"name": "First"})
        n2 = self.Note.create({"name": "Second"})
        notes = self.Note.search([("id", "in", [n1.id, n2.id])])
        self.assertEqual(notes[0].id, n2.id)

    def test_content_field(self):
        """The content text field stores and retrieves correctly."""
        body = "This staging instance is working as expected."
        note = self.Note.create({"name": "With content", "content": body})
        self.assertEqual(note.content, body)

    def test_access_rights(self):
        """Internal users can CRUD staging test notes."""
        user = self.env.ref("base.user_demo", raise_if_not_found=False)
        if not user:
            user = self.env["res.users"].create({
                "name": "Test User",
                "login": "staging_test_user",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            })
        note = self.Note.with_user(user).create({"name": "User note"})
        self.assertTrue(note.id)
        note.with_user(user).write({"tag": "success"})
        self.assertEqual(note.tag, "success")
        note.with_user(user).unlink()
