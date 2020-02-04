"""Tests for runway.cfngin.actions.destroy."""
# pylint: disable=protected-access,unused-argument
import unittest

import mock

from runway.cfngin.actions import destroy
from runway.cfngin.context import Config, Context
from runway.cfngin.exceptions import StackDoesNotExist
from runway.cfngin.status import COMPLETE, PENDING, SKIPPED, SUBMITTED

from ..factories import MockProviderBuilder, MockThreadingEvent


class MockStack(object):
    """Mock our local Stacker stack and an AWS provider stack."""

    def __init__(self, name, tags=None, **kwargs):
        """Instantiate class."""
        self.name = name
        self.fqn = name
        self.region = None
        self.profile = None
        self.requires = []


class TestDestroyAction(unittest.TestCase):
    """Tests for runway.cfngin.actions.destroy.DestroyAction."""

    def setUp(self):
        """Run before tests."""
        config = Config({
            "namespace": "namespace",
            "stacks": [
                {"name": "vpc"},
                {"name": "bastion", "requires": ["vpc"]},
                {"name": "instance", "requires": ["vpc", "bastion"]},
                {"name": "db", "requires": ["instance", "vpc", "bastion"]},
                {"name": "other", "requires": ["db"]},
            ],
        })
        self.context = Context(config=config)
        self.action = destroy.Action(self.context,
                                     cancel=MockThreadingEvent())

    def test_generate_plan(self):
        """Test generate plan."""
        plan = self.action._generate_plan()
        self.assertEqual(
            {
                'vpc': set(
                    ['db', 'instance', 'bastion']),
                'other': set([]),
                'bastion': set(
                    ['instance', 'db']),
                'instance': set(
                    ['db']),
                'db': set(
                    ['other'])},
            plan.graph.to_dict()
        )

    def test_only_execute_plan_when_forced(self):
        """Test only execute plan when forced."""
        with mock.patch.object(self.action, "_generate_plan") as \
                mock_generate_plan:
            self.action.run(force=False)
            self.assertEqual(mock_generate_plan().execute.call_count, 0)

    def test_execute_plan_when_forced(self):
        """Test execute plan when forced."""
        with mock.patch.object(self.action, "_generate_plan") as \
                mock_generate_plan:
            self.action.run(force=True)
            self.assertEqual(mock_generate_plan().execute.call_count, 1)

    def test_destroy_stack_complete_if_state_submitted(self):
        """Test destroy stack complete if state submitted."""
        # Simulate the provider not being able to find the stack (a result of
        # it being successfully deleted)
        provider = mock.MagicMock()
        provider.get_stack.side_effect = StackDoesNotExist("mock")
        self.action.provider_builder = MockProviderBuilder(provider)
        status = self.action._destroy_stack(MockStack("vpc"), status=PENDING)
        # if we haven't processed the step (ie. has never been SUBMITTED,
        # should be skipped)
        self.assertEqual(status, SKIPPED)
        status = self.action._destroy_stack(MockStack("vpc"), status=SUBMITTED)
        # if we have processed the step and then can't find the stack, it means
        # we successfully deleted it
        self.assertEqual(status, COMPLETE)

    def test_destroy_stack_step_statuses(self):
        """Test destroy stack step statuses."""
        mock_provider = mock.MagicMock()
        stacks_dict = self.context.get_stacks_dict()

        def get_stack(stack_name):
            return stacks_dict.get(stack_name)

        plan = self.action._generate_plan()
        step = plan.steps[0]
        # we need the AWS provider to generate the plan, but swap it for
        # the mock one to make the test easier
        self.action.provider_builder = MockProviderBuilder(mock_provider)

        # simulate stack doesn't exist and we haven't submitted anything for
        # deletion
        mock_provider.get_stack.side_effect = StackDoesNotExist("mock")

        step.run()
        self.assertEqual(step.status, SKIPPED)

        # simulate stack getting successfully deleted
        mock_provider.get_stack.side_effect = get_stack
        mock_provider.is_stack_destroyed.return_value = False
        mock_provider.is_stack_in_progress.return_value = False

        step._run_once()
        self.assertEqual(step.status, SUBMITTED)
        mock_provider.is_stack_destroyed.return_value = False
        mock_provider.is_stack_in_progress.return_value = True

        step._run_once()
        self.assertEqual(step.status, SUBMITTED)
        mock_provider.is_stack_destroyed.return_value = True
        mock_provider.is_stack_in_progress.return_value = False

        step._run_once()
        self.assertEqual(step.status, COMPLETE)
