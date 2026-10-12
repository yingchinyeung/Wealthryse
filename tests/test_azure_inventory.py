import unittest
from ibkr_etl.azure_inventory import InventoryError, read_collection

BASE = '/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-test'
COLLECTION = BASE + '/providers/Microsoft.App/jobs'
PATH = COLLECTION + '?api-version=2024-03-01'
NEXT = PATH + '&$skiptoken=opaque%2Fcursor'


class AzureInventoryTests(unittest.TestCase):
    def row(self, name='a'):
        return {'id': COLLECTION + '/' + name, 'name': name}

    def read(self, pages, **limits):
        calls = []
        def request(method, path):
            self.assertEqual(method, 'GET')
            self.assertTrue(path.startswith('/subscriptions/'))
            calls.append(path)
            value = pages[len(calls)-1]
            if isinstance(value, Exception):
                raise value
            return value
        return read_collection(request, PATH, **limits), calls

    def test_complete_two_pages_absolute_continuation(self):
        rows, calls = self.read([
            {'value': [self.row()], 'nextLink': 'https://management.azure.com' + NEXT},
            {'value': [self.row('b')]},
        ])
        self.assertEqual([r['name'] for r in rows], ['a', 'b'])
        self.assertEqual(calls, [PATH, NEXT])

    def test_empty_final_page_and_relative_continuation(self):
        rows, _ = self.read([{'value': [self.row()], 'nextLink': NEXT}, {'value': []}])
        self.assertEqual(len(rows), 1)

    def test_foreign_links_rejected_before_transport(self):
        for link in ('https://evil.example' + NEXT, 'http://management.azure.com' + NEXT,
                     'https://management.azure.com:443' + NEXT,
                     'https://user@management.azure.com' + NEXT,
                     NEXT.replace('rg-test', 'rg-other'), NEXT.replace('/jobs?', '/containerApps?'),
                     NEXT + '#fragment', NEXT.replace('2024-03-01', '2025-01-01'),
                     NEXT + '&api-version=2024-03-01', PATH, NEXT + '&$filter=x',
                     NEXT.replace('opaque%2Fcursor', ''), NEXT + '\n',
                     NEXT.replace('/jobs?', '/%6aobs?'), NEXT.replace('/jobs?', '/jobs\\?')):
            with self.subTest(link=link), self.assertRaises(InventoryError):
                self.read([{'value': [], 'nextLink': link}])

    def test_bad_pages_fail(self):
        for page in ({}, {'value': None}, {'value': {}}, {'value': [None]},
                     {'value': [{}]}, {'value': [self.row(), self.row()]}):
            with self.subTest(page=page), self.assertRaises(InventoryError):
                self.read([page])

    def test_foreign_and_malformed_resource_ids(self):
        for identity in (BASE + '/providers/Microsoft.App/containerApps/a',
                         self.row()['id'].replace('rg-test', 'rg-other'),
                         COLLECTION + '/a/../b', COLLECTION + '/a%2Fb',
                         COLLECTION + '/a?token=x', COLLECTION + '/a\n', COLLECTION + '/'):
            with self.subTest(identity=identity), self.assertRaises(InventoryError):
                self.read([{'value': [{'id': identity}]}])

    def test_duplicate_across_pages_and_case(self):
        with self.assertRaises(InventoryError):
            self.read([{'value': [self.row()], 'nextLink': NEXT},
                       {'value': [{'id': self.row()['id'].upper()}]}])

    def test_repeated_cursor_fails(self):
        with self.assertRaises(InventoryError):
            self.read([{'value': [], 'nextLink': NEXT},
                       {'value': [], 'nextLink': NEXT.replace('$skiptoken', '$skipToken')}])

    def test_page_and_item_bounds(self):
        with self.assertRaises(InventoryError):
            self.read([{'value': [], 'nextLink': NEXT}], max_pages=1)
        with self.assertRaises(InventoryError):
            self.read([{'value': [self.row(), self.row('b')]}], max_items=1)

    def test_final_page_transport_failure_does_not_return_partial_success(self):
        with self.assertRaises(RuntimeError):
            self.read([{'value': [self.row()], 'nextLink': NEXT}, RuntimeError('failed')])

    def test_initial_scope_and_limits(self):
        for path in ('https://management.azure.com' + PATH, PATH + '&$filter=x',
                     PATH + '#x', '/elsewhere?api-version=2024-03-01'):
            with self.assertRaises(InventoryError):
                read_collection(lambda *_: self.fail('transport called'), path)
        for kwargs in ({'max_pages': 0}, {'max_items': True}, {'max_pages': 1001}):
            with self.assertRaises(InventoryError):
                self.read([], **kwargs)

    def test_generic_resource_group_inventory(self):
        rows = read_collection(lambda *_: {'value': [self.row()]}, BASE + '/resources?api-version=2021-04-01')
        self.assertEqual(len(rows), 1)


if __name__ == '__main__':
    unittest.main()
