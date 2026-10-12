"""Regression for the exact native empty startup container observation."""
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import owner_task_aca_readiness as readiness
from test_owner_task_aca_native import nested_replica
from test_owner_task_aca_runtime_named import EXECUTION


def empty_replica():
    value = nested_replica()
    value['value'][0]['properties'].update(containers=[], runningState='NotRunning', runningStateDetails='')
    return value


class StartupReadinessTests(unittest.TestCase):
    def test_observed_empty_startup_is_pending_without_mutation_or_running_evidence(self):
        value = empty_replica(); before = copy.deepcopy(value)
        self.assertEqual('pending', readiness.classify_replica(value, EXECUTION))
        for running in (True, False):
            with self.assertRaises(readiness.ReplicaPending):
                readiness.validate_replica(value, EXECUTION, running=running)
        self.assertEqual(before, value)

    def test_known_parent_status_lag_never_makes_empty_containers_ready(self):
        for state in ('Running', 'NotRunning', 'Unknown'):
            value = empty_replica(); value['value'][0]['properties']['runningState'] = state
            self.assertEqual('pending', readiness.classify_replica(value, EXECUTION))

    def test_empty_metadata_still_requires_exact_scope_schema_time_and_known_state(self):
        for change in ('id','name','type','created','state','details','extra','init','duplicate','next','arraytype'):
            value = empty_replica(); row = value['value'][0]; props = row['properties']
            if change == 'id': row['id'] += '-foreign'
            elif change == 'name': row['name'] += '-foreign'
            elif change == 'type': row['type'] = 'unknown'
            elif change == 'created': props['createdTime'] = '2026-10-04T23:27:13'
            elif change == 'state': props['runningState'] = 'Healthy'
            elif change == 'details': props['runningStateDetails'] = 'x' * 4097
            elif change == 'extra': props['containerReady'] = True
            elif change == 'init': props['initContainers'] = [{'name':'sidecar'}]
            elif change == 'duplicate': value['value'].append(copy.deepcopy(row))
            elif change == 'next': value['nextLink'] = 'next'
            else: props['containers'] = ()
            with self.subTest(change=change), self.assertRaises(ValueError):
                readiness.classify_replica(value, EXECUTION)

    def test_populated_ready_row_retains_exact_frozen_validation(self):
        value = nested_replica(); before = copy.deepcopy(value)
        self.assertEqual('ready', readiness.classify_replica(value, EXECUTION))
        self.assertEqual(value['value'][0], readiness.validate_replica(value, EXECUTION))
        self.assertEqual(before, value)
        for change in ('restart','name','endpoint','terminated'):
            value = nested_replica(); container = value['value'][0]['properties']['containers'][0]
            if change == 'restart': container['restartCount'] = 1
            elif change == 'name': container['name'] = 'foreign'
            elif change == 'endpoint': container['execEndpoint'] += '?token=unsafe'
            else: container.update(runningState='Terminated', ready=False, started=False)
            with self.subTest(change=change), self.assertRaises(ValueError):
                readiness.classify_replica(value, EXECUTION)

    def test_empty_containers_cannot_supply_log_ownership(self):
        with self.assertRaises(ValueError):
            readiness.validate_log_binding([], empty_replica()['value'][0], {})
        with self.assertRaises(ValueError):
            readiness.validate_replica(empty_replica(), EXECUTION, running=1)


if __name__ == '__main__': unittest.main()
