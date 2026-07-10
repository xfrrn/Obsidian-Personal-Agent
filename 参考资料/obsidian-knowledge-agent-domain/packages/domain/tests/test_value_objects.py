import unittest

from oka_domain.common import TagName, VaultPath
from oka_domain.exceptions import ValidationError


class ValueObjectTests(unittest.TestCase):
    def test_vault_path_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValidationError):
            VaultPath("Projects/../Secret.md")

    def test_tag_name_is_normalized(self) -> None:
        self.assertEqual(str(TagName("#Artificial Intelligence")), "artificial-intelligence")


if __name__ == "__main__":
    unittest.main()
