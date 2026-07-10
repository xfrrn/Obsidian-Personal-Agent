import unittest

from oka_domain.tasks import Task, TaskStatus
from oka_domain.exceptions import InvalidStateTransition


class TaskTests(unittest.TestCase):
    def test_task_status_lifecycle(self) -> None:
        task = Task.create(title="Implement contracts")
        task.start()
        task.complete()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertIsNotNone(task.completed_at)
        task.reopen()
        self.assertEqual(task.status, TaskStatus.TODO)

    def test_done_task_cannot_be_cancelled(self) -> None:
        task = Task.create(title="Implement contracts")
        task.complete()
        with self.assertRaises(InvalidStateTransition):
            task.cancel()


if __name__ == "__main__":
    unittest.main()
