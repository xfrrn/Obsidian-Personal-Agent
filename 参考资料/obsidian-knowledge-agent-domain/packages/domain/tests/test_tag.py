import unittest

from oka_domain.common import TagName
from oka_domain.tags import Tag, TagCategory, TagStatus


class TagTests(unittest.TestCase):
    def test_tag_alias_and_deprecation(self) -> None:
        tag = Tag.create(name=TagName("人工智能"), category=TagCategory.DOMAIN)
        tag.add_alias(TagName("AI"))
        tag.deprecate()
        self.assertEqual(tag.status, TagStatus.DEPRECATED)
        self.assertIn(TagName("ai"), tag.aliases)


if __name__ == "__main__":
    unittest.main()
