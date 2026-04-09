"""Tests for scheduler integration and multi-session CLI."""
from __future__ import annotations

import unittest

from copilot_operator.scheduler import (
    SessionRole,
    create_scheduler_plan,
    get_next_runnable_slot,
    is_plan_complete,
    mark_slot_complete,
    mark_slot_running,
    render_scheduler_plan,
    update_plan_status,
)


class TestSchedulerPlanCreation(unittest.TestCase):

    def test_default_creates_coder_slot(self):
        plan = create_scheduler_plan('add feature X')
        self.assertEqual(len(plan.slots), 1)
        self.assertEqual(plan.slots[0].role, 'coder')

    def test_multiple_roles_creates_ordered_slots(self):
        plan = create_scheduler_plan('add feature X', roles=['coder', 'reviewer'])
        self.assertEqual(len(plan.slots), 2)
        roles = [s.role for s in plan.slots]
        # Coder should come before reviewer (dependency order)
        self.assertEqual(roles.index('coder'), 0)

    def test_slot_goal_includes_role_suffix(self):
        plan = create_scheduler_plan('fix bug', roles=['coder'])
        self.assertIn('fix bug', plan.slots[0].goal)
        self.assertIn('Role: coder', plan.slots[0].goal)


class TestSchedulerSlotLifecycle(unittest.TestCase):

    def test_get_next_runnable_slot_returns_first_pending(self):
        plan = create_scheduler_plan('goal', roles=['coder', 'reviewer'])
        slot = get_next_runnable_slot(plan)
        self.assertIsNotNone(slot)
        self.assertEqual(slot.role, 'coder')

    def test_reviewer_blocked_until_coder_completes(self):
        plan = create_scheduler_plan('goal', roles=['coder', 'reviewer'])
        # Mark coder as running, then try to get next
        plan.slots[0].status = 'running'
        slot = get_next_runnable_slot(plan)
        # Reviewer depends on coder, coder is not complete
        self.assertIsNone(slot)

    def test_reviewer_available_after_coder_complete(self):
        plan = create_scheduler_plan('goal', roles=['coder', 'reviewer'])
        plan.slots[0].status = 'complete'
        slot = get_next_runnable_slot(plan)
        self.assertIsNotNone(slot)
        self.assertEqual(slot.role, 'reviewer')

    def test_mark_slot_complete(self):
        plan = create_scheduler_plan('goal', roles=['coder'])
        slot = plan.slots[0]
        mark_slot_running(slot, 'run-123')
        self.assertEqual(slot.status, 'running')
        mark_slot_complete(slot, {'status': 'complete', 'score': 95})
        self.assertEqual(slot.status, 'complete')
        self.assertEqual(slot.final_score, 95)


class TestSchedulerPlanCompletion(unittest.TestCase):

    def test_plan_not_complete_with_pending_slots(self):
        plan = create_scheduler_plan('goal', roles=['coder'])
        self.assertFalse(is_plan_complete(plan))

    def test_plan_complete_when_all_slots_terminal(self):
        plan = create_scheduler_plan('goal', roles=['coder'])
        plan.slots[0].status = 'complete'
        self.assertTrue(is_plan_complete(plan))

    def test_update_plan_status(self):
        plan = create_scheduler_plan('goal', roles=['coder'])
        plan.slots[0].status = 'complete'
        update_plan_status(plan)
        self.assertEqual(plan.status, 'complete')

    def test_render_plan_returns_string(self):
        plan = create_scheduler_plan('goal', roles=['coder', 'reviewer'])
        text = render_scheduler_plan(plan)
        self.assertIn('coder', text)
        self.assertIn('reviewer', text)


class TestCustomRoles(unittest.TestCase):

    def test_custom_role_added(self):
        custom = SessionRole(name='security', goal_suffix='Run security audit', priority=15)
        plan = create_scheduler_plan('goal', roles=['coder'], custom_roles=[custom])
        roles = [s.role for s in plan.slots]
        self.assertIn('security', roles)
        self.assertIn('coder', roles)


if __name__ == '__main__':
    unittest.main()
