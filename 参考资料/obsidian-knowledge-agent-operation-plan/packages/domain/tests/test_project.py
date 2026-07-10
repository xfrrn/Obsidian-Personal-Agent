import unittest

from oka_domain.common import VaultPath
from oka_domain.projects import Project, ProjectDocumentRole, ProjectStatus


class ProjectTests(unittest.TestCase):
    def test_project_lifecycle_and_document_registry(self) -> None:
        project = Project.create(name="AutoUp", root_path=VaultPath("Projects/AutoUp"))
        project.activate()
        project.register_document(ProjectDocumentRole.OVERVIEW, VaultPath("Projects/AutoUp/项目总览.md"))
        self.assertEqual(project.status, ProjectStatus.ACTIVE)
        self.assertNotIn(ProjectDocumentRole.OVERVIEW, project.missing_standard_documents())


if __name__ == "__main__":
    unittest.main()
