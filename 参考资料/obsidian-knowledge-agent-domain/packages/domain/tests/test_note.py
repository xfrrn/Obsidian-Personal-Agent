import unittest

from oka_domain.common import TagName, VaultPath
from oka_domain.notes import Note


class NoteTests(unittest.TestCase):
    def test_note_content_change_updates_hash_revision_and_event(self) -> None:
        note = Note.create(path=VaultPath("Inbox/Test.md"), title="Test", body="old")
        note.pull_domain_events()
        old_hash = note.content_hash
        note.replace_body("new", expected_content_hash=old_hash)
        self.assertNotEqual(note.content_hash, old_hash)
        self.assertEqual(note.revision, 1)
        self.assertEqual(note.peek_domain_events()[0].event_type, "note.content-changed")

    def test_add_tag_is_idempotent(self) -> None:
        note = Note.create(path=VaultPath("Inbox/Test.md"), title="Test")
        note.pull_domain_events()
        note.add_tags(TagName("AutoUp"), TagName("AutoUp"))
        self.assertEqual(len(note.tags), 1)


if __name__ == "__main__":
    unittest.main()
