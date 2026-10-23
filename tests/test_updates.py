"""Synthetic stdlib updater tests: no live network or application operations."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("updater_under_test", HERE.parent / "mudweave_update.py")
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)
REAL_FETCH_JSON = U._fetch_json
REAL_OPEN = U._open
OUTPUT = Path(os.environ.get('MUDWEAVE_TEST_STORAGE',
                            Path.home() / 'Documents' / 'MudGPT Build Storage')).absolute()
SETTINGS = {"schema_version": 1, "project_id": 87773838, "iteration": "codex", "collection_version": "0.3.0"}
NOW = 1792713600.0
DATA = b'PK\x03\x04synthetic archive bytes\x00'


def fixtures():
    assets, links = [], []
    for iteration in ('codex', 'claude', 'muse'):
        row = {'iteration': iteration, 'name': f'mudweave-{iteration}-0.2.0-updates1.zip', 'version': '0.2.0',
               'size_bytes': len(DATA), 'sha256': hashlib.sha256(DATA).hexdigest(),
               'url': '/downloads/files/untrusted-legacy-portfolio.zip'}
        assets.append(row)
        links.append({'name': row['name'], 'url': f'https://gitlab.com/codalilitari/mudgpt/-/uploads/{"a"*32}/{row["name"]}'})
    return ({'tag_name': 'v0.4.0', 'released_at': U._iso(NOW - 60), 'upcoming_release': False,
             'commit': {'id': 'b' * 40}, 'assets': {'links': links}},
            {'collection_version': '0.4.0', 'assets': assets,
             'complete_collection': {'filename': 'mudweave-clients-0.4.0.zip', 'release_url': 'https://example.invalid'}})


class Response(io.BytesIO):
    def __init__(self, data, url='https://gitlab.com/api/v4/projects/87773838/releases/permalink/latest'):
        super().__init__(data)
        self.url = url
    def geturl(self):
        return self.url


class UpdateTests(unittest.TestCase):
    def setUp(self):
        U._guard(OUTPUT, directory=True)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        U._guard(OUTPUT, directory=True)
        self.temp = tempfile.TemporaryDirectory(prefix='updater-test-', dir=OUTPUT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.settings = dict(SETTINGS)
        self.release, self.manifest = fixtures()
        self.calls = []
        self.patches = [mock.patch.object(U, 'INSTALL_DIR', self.base / 'install'),
                        mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.base / 'cache'), 'XDG_CACHE_HOME': str(self.base / 'cache')}),
                        mock.patch.object(U.time, 'time', return_value=NOW),
                        mock.patch.object(U, '_fetch_json', side_effect=self.fetch),
                        mock.patch.object(U, '_open', side_effect=AssertionError('Unexpected real transport'))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def fetch(self, url):
        self.calls.append(url)
        if url.endswith('/releases/permalink/latest'):
            return copy.deepcopy(self.release)
        self.assertEqual(url, 'https://gitlab.com/api/v4/projects/87773838/repository/files/downloads.json/raw?ref=' + 'b' * 40)
        return copy.deepcopy(self.manifest)

    def state(self):
        return U._read(U._cache_root(self.settings), 'state.json')

    def write_state(self, value):
        U._write(U._cache_root(self.settings), 'state.json', value)

    def test_commit_pinned_check_and_numeric_semver(self):
        self.release['tag_name'] = 'v0.10.0'
        self.manifest['collection_version'] = '0.10.0'
        self.settings['collection_version'] = '0.9.0'
        result = U.check(self.settings)
        self.assertEqual(result['status'], 'update_available')
        self.assertTrue(result['update_available'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result['asset']['version'], '0.2.0')
        self.assertIn('/-/uploads/', result['asset']['url'])
        self.assertNotIn('/downloads/files/', result['asset']['url'])

    def test_older_release_does_not_downgrade(self):
        self.settings['collection_version'] = '1.0.0'
        self.assertEqual(U.check(self.settings)['status'], 'ahead')
        self.assertEqual(U.check(self.settings)['status'], 'ahead')
        self.assertEqual(U.download(self.settings)['status'], 'ahead')
        U._open.assert_not_called()
        self.assertFalse((U._cache_root(self.settings) / 'downloads').exists())

    def test_numeric_patch_version_and_equal_version(self):
        self.settings['collection_version'] = '0.2.9'
        self.release['tag_name'] = 'v0.2.10'
        self.manifest['collection_version'] = '0.2.10'
        self.assertEqual(U.check(self.settings)['status'], 'update_available')
        self.settings['collection_version'] = '0.2.10'
        self.assertEqual(U.check(self.settings)['status'], 'up_to_date')

    def test_fresh_cache_and_force(self):
        first = U.check(self.settings)
        second = U.check(self.settings)
        self.assertFalse(first['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(len(self.calls), 2)
        U.check(self.settings, force=True)
        self.assertEqual(len(self.calls), 4)

    def test_daily_expiration_fetches_again(self):
        U.check(self.settings)
        with mock.patch.object(U.time, 'time', return_value=NOW + U.DAY):
            self.assertFalse(U.check(self.settings)['cache_hit'])
        self.assertEqual(len(self.calls), 4)

    def test_stale_or_future_cache_never_reports_up_to_date(self):
        self.settings['collection_version'] = '1.0.0'
        U.check(self.settings)
        for offset in (-U.DAY, 60):
            state = self.state()
            state['result']['checked_at'] = U._iso(NOW + offset)
            state['last_attempt_at'] = NOW + offset
            state['retry_at'] = NOW + offset + U.DAY
            self.write_state(state)
            self.assertEqual(U.status(self.settings)['status'], 'stale')
            with mock.patch.object(U, '_fetch_json', side_effect=U.UpdateError('offline')):
                result = U.check(self.settings)
            self.assertNotEqual(result['status'], 'up_to_date')
            self.assertFalse(result['update_available'])

    def test_failure_backoff_force_and_no_raw_errors(self):
        with mock.patch.object(U, '_fetch_json', side_effect=OSError('private token untrusted server body')) as fetch:
            result = U.check(self.settings)
            self.assertNotIn('private token', json.dumps(result))
            self.assertEqual(U.check(self.settings)['status'], 'backoff')
            self.assertEqual(fetch.call_count, 1)
            U.check(self.settings, force=True)
            self.assertEqual(fetch.call_count, 2)

    def test_rate_limit_is_failsoft_and_backed_off(self):
        with mock.patch.object(U, '_fetch_json', side_effect=U.UpdateError('rate_limited')):
            self.assertEqual(U.check(self.settings)['status'], 'rate_limited')
            self.assertEqual(U.check(self.settings)['status'], 'backoff')

    def test_actual_http_header_failure_becomes_offline_and_backoff(self):
        opener = mock.Mock()
        opener.open.side_effect = U.http.client.BadStatusLine('private untrusted status line')
        with mock.patch.object(U, '_fetch_json', REAL_FETCH_JSON), mock.patch.object(U, '_open', REAL_OPEN), \
                mock.patch.object(U.urllib.request, 'build_opener', return_value=opener):
            result = U.check(self.settings)
            self.assertEqual(result['status'], 'offline')
            self.assertNotIn('private untrusted', json.dumps(result))
            self.assertEqual(U.check(self.settings)['status'], 'backoff')
            self.assertEqual(opener.open.call_count, 1)

    def test_actual_http_json_body_failure_becomes_offline_and_backoff(self):
        response = Response(b'')
        response.read1 = mock.Mock(side_effect=U.http.client.IncompleteRead(b'private partial response', 20))
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(U, '_fetch_json', REAL_FETCH_JSON), mock.patch.object(U, '_open', REAL_OPEN), \
                mock.patch.object(U.urllib.request, 'build_opener', return_value=opener):
            result = U.check(self.settings)
            self.assertEqual(result['status'], 'offline')
            self.assertNotIn('private partial', json.dumps(result))
            self.assertTrue(response.closed)
            self.assertEqual(U.check(self.settings)['status'], 'backoff')
            self.assertEqual(opener.open.call_count, 1)

    def test_actual_http_archive_failure_is_clean_and_never_published(self):
        for failure in ('headers', 'body'):
            responses = []
            def transport(request, timeout):
                url = request.full_url
                if url.endswith('/releases/permalink/latest'):
                    response = Response(json.dumps(self.release).encode(), url)
                elif '/repository/files/downloads.json/raw?ref=' in url:
                    response = Response(json.dumps(self.manifest).encode(), url)
                elif failure == 'headers':
                    raise U.http.client.BadStatusLine('private archive status line')
                else:
                    response = Response(b'', url)
                    response.read1 = mock.Mock(side_effect=[DATA[:4], U.http.client.IncompleteRead(b'private partial archive', 20)])
                responses.append(response)
                return response
            opener = mock.Mock()
            opener.open.side_effect = transport
            with self.subTest(failure=failure), mock.patch.object(U, '_fetch_json', REAL_FETCH_JSON), \
                    mock.patch.object(U, '_open', REAL_OPEN), \
                    mock.patch.object(U.urllib.request, 'build_opener', return_value=opener):
                result = U.download(self.settings)
            self.assertEqual(result['status'], 'offline')
            self.assertNotIn('private', json.dumps(result))
            self.assertEqual(opener.open.call_count, 3)
            self.assertTrue(all(response.closed for response in responses))
            self.assertEqual(list((U._cache_root(self.settings) / 'downloads').iterdir()), [])

    def test_nonblocking_os_lock_contention(self):
        root = U._cache_root(self.settings)
        with U._lock(root) as acquired:
            self.assertTrue(acquired)
            started = time.monotonic()
            self.assertEqual(U.check(self.settings)['status'], 'cache_busy')
            self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.calls, [])

    def test_enabled_preferences_external_and_force_once(self):
        self.assertEqual(U.configure(self.settings, False)['status'], 'configured')
        self.assertEqual(U.check(self.settings)['status'], 'disabled')
        self.assertEqual(self.calls, [])
        self.assertEqual(U.check(self.settings, force=True)['status'], 'update_available')
        self.assertFalse(U.status(self.settings)['enabled'])
        self.assertFalse((self.base / 'install').exists())

    def test_cache_isolated_by_install_and_iteration(self):
        first = U._cache_root(self.settings)
        other = U._cache_root(dict(self.settings, iteration='muse'))
        self.assertNotEqual(first, other)
        with mock.patch.object(U, 'INSTALL_DIR', self.base / 'second-install'):
            self.assertNotEqual(first, U._cache_root(self.settings))
        self.assertTrue(first.is_relative_to(self.base / 'cache'))

    def test_git_ancestor_and_reparse_guard(self):
        marker = self.base / '.git'
        marker.write_text('synthetic Git marker')
        self.assertEqual(U.check(self.settings)['status'], 'cache_unavailable')
        marker.unlink()
        original = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda path: path == self.base / 'cache' or original(path)):
            self.assertEqual(U.check(self.settings)['status'], 'cache_unavailable')

    def test_hardlinked_cache_file_refused(self):
        root = U._cache_root(self.settings)
        path = root / 'state.json'
        path.write_text('{}')
        os.link(path, root / 'hardlink')
        self.assertEqual(U.check(self.settings)['status'], 'cache_unavailable')

    def test_settings_schema_project_and_semver(self):
        for changes in ({'schema_version': True}, {'project_id': 1}, {'iteration': 'other'},
                        {'collection_version': '0.3'}, {'collection_version': '01.3.0'}, {'enabled': 1}):
            with self.subTest(changes=changes):
                self.assertEqual(U.check(dict(self.settings, **changes))['status'], 'configuration_error')
        self.assertEqual(U.load_settings(dict(self.settings, runtime_version='0.2.0', distribution_revision='updates1'))['iteration'], 'codex')

    def test_settings_beside_script_and_duplicate_json_rejected(self):
        U.INSTALL_DIR.mkdir()
        path = U.INSTALL_DIR / 'mudweave-update.json'
        path.write_text(json.dumps(self.settings))
        self.assertEqual(U.load_settings()['collection_version'], '0.3.0')
        path.write_text('{"schema_version":1,"schema_version":1}')
        self.assertEqual(U.check()['status'], 'configuration_error')

    def test_upcoming_future_and_prereleases_ignored(self):
        for change in ({'tag_name': 'v0.4.0-beta.1'}, {'tag_name': 'v01.4.0'}, {'upcoming_release': True},
                       {'released_at': U._iso(NOW + 3600)}):
            original = copy.deepcopy(self.release)
            self.release.update(change)
            with self.subTest(change=change):
                result = U.check(self.settings, force=True)
                self.assertEqual(result['status'], 'ignored_release')
                self.assertFalse(result['update_available'])
            self.release = original

    def test_missing_commit_and_collection_version_mismatch(self):
        self.release['commit']['id'] = 'main'
        self.assertEqual(U.check(self.settings)['status'], 'invalid_metadata')
        self.release['commit']['id'] = 'b' * 40
        self.manifest['collection_version'] = '0.5.0'
        self.assertEqual(U.check(self.settings, force=True)['status'], 'invalid_metadata')

    def test_malformed_asset_size_hash_name_or_iteration(self):
        for change in ({'size_bytes': True}, {'size_bytes': U.MAX_ARCHIVE + 1}, {'sha256': 'bad'},
                       {'name': '../escape.zip'}, {'name': 'mudweave-muse-0.2.0.zip'}, {'version': '0.2.0-dev'}):
            original = copy.deepcopy(self.manifest['assets'][0])
            self.manifest['assets'][0].update(change)
            with self.subTest(change=change):
                self.assertEqual(U.check(self.settings, force=True)['status'], 'invalid_metadata')
            self.manifest['assets'][0] = original

    def test_exact_release_filename_and_duplicate_links_required(self):
        original = copy.deepcopy(self.release['assets']['links'])
        for links in (original[1:], original + [original[0]]):
            self.release['assets']['links'] = links
            self.assertEqual(U.check(self.settings, force=True)['status'], 'invalid_metadata')

    def test_foreign_auth_port_http_query_traversal_urls_rejected(self):
        name = self.manifest['assets'][0]['name']
        good = self.release['assets']['links'][0]['url']
        for bad in (good.replace('https:', 'http:'), good.replace('gitlab.com', 'gitlab.com.evil.invalid'),
                    good.replace('gitlab.com', 'user:pass@gitlab.com'), good.replace('gitlab.com', 'gitlab.com:443'),
                    good.replace('codalilitari/mudgpt', 'other/project'), good + '?token=secret',
                    good.replace('/-/uploads/', '/-/uploads/%2e%2e/'), good.replace('.zip', '%252Ezip')):
            with self.subTest(url=bad), self.assertRaises(U.UpdateError):
                U._asset_url(bad, name)

    def test_encoded_generic_package_path_allowed_once(self):
        name = self.manifest['assets'][0]['name']
        url = 'https://gitlab.com/api/v4/projects/87773838/packages/generic/mudweave/0%2E4%2E0/' + name.replace('.', '%2E')
        self.assertEqual(U._asset_url(url, name), url)
        url = 'https://gitlab.com/api/v4/projects/codalilitari%2Fmudgpt/packages/generic/mudgpt-clients/v0%2E3%2E0/' + name.replace('.', '%2E')
        self.assertEqual(U._asset_url(url, name), url)
        bad = url.replace('codalilitari%2Fmudgpt', 'somebody%2Fmudgpt')
        with self.assertRaises(U.UpdateError):
            U._asset_url(bad, name)
        bad = url.replace('mudgpt-clients', 'unrelated-package')
        with self.assertRaises(U.UpdateError):
            U._asset_url(bad, name)

    def test_observed_numeric_project_upload_form(self):
        name = self.manifest['assets'][0]['name']
        url = f'https://gitlab.com/-/project/87773838/uploads/{"a"*32}/{name}'
        self.assertEqual(U._asset_url(url, name), url)
        with self.assertRaises(U.UpdateError):
            U._asset_url(url.replace('/87773838/', '/9999/'), name)

    def test_redirects_restricted_for_metadata_and_assets(self):
        request = U.urllib.request.Request('https://gitlab.com/api/v4/projects/87773838/releases/permalink/latest')
        with self.assertRaises(U.UpdateError):
            U._Redirect().redirect_request(request, None, 302, 'found', {}, 'https://example.invalid/')
        name = self.manifest['assets'][0]['name']
        with self.assertRaises(U.UpdateError):
            U._Redirect(name).redirect_request(request, None, 302, 'found', {}, 'https://gitlab.com/other/project/file.zip')

    def test_json_limit_duplicate_keys_and_nonfinite_values(self):
        for data in (b'{}' + b' ' * U.MAX_JSON, b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'):
            with self.subTest(length=len(data)), self.assertRaises(U.UpdateError):
                U._json_bytes(data)

    def test_collection_can_notify_but_not_download_without_integrity_metadata(self):
        settings = dict(self.settings, iteration='collection')
        result = U.check(settings)
        self.assertTrue(result['update_available'])
        self.assertIsNone(result['asset'])
        self.assertEqual(U.download(settings)['status'], 'download_unsupported')

    def test_check_never_opens_archive_or_creates_download_directory(self):
        result = U.check(self.settings)
        self.assertTrue(result['update_available'])
        self.assertFalse((U._cache_root(self.settings) / 'downloads').exists())
        U._open.assert_not_called()

    def test_successful_atomic_download_no_extract_and_repeat(self):
        url = self.release['assets']['links'][0]['url']
        with mock.patch.object(U, '_open', side_effect=lambda *args: Response(DATA, url)) as transport:
            result = U.download(self.settings)
            self.assertEqual(result['status'], 'downloaded')
            path = Path(result['download_path'])
            self.assertEqual(path.read_bytes(), DATA)
            self.assertTrue(path.is_relative_to(self.base / 'cache'))
            second = U.download(self.settings)
            self.assertTrue(second['download_cached'])
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_wrong_short_extra_hash_downloads_never_publish(self):
        url = self.release['assets']['links'][0]['url']
        for data in (DATA[:-1], DATA + b'x', b'X' * len(DATA)):
            with mock.patch.object(U, '_open', side_effect=lambda *args, data=data: Response(data, url)):
                result = U.download(self.settings)
            self.assertEqual(result['status'], 'download_integrity_error')
            self.assertEqual(list((U._cache_root(self.settings) / 'downloads').iterdir()), [])

    def test_existing_different_file_never_overwritten(self):
        asset = U.check(self.settings)['asset']
        folder = U._cache_root(self.settings) / 'downloads'
        folder.mkdir()
        path = folder / (asset['sha256'] + '-' + asset['name'])
        path.write_bytes(b'preserve existing bytes')
        self.assertEqual(U.download(self.settings)['status'], 'download_conflict')
        self.assertEqual(path.read_bytes(), b'preserve existing bytes')
        U._open.assert_not_called()

    def test_denied_partial_cleanup_preserves_sanitized_failure(self):
        url = self.release['assets']['links'][0]['url']
        original_unlink = U.os.unlink
        def denied(path, *args, **kwargs):
            if str(path).endswith('.part'):
                raise PermissionError('private denied cleanup path')
            return original_unlink(path, *args, **kwargs)
        with mock.patch.object(U, '_open', side_effect=lambda *args: Response(DATA[:-1], url)), \
                mock.patch.object(U.os, 'unlink', side_effect=denied):
            result = U.download(self.settings)
        self.assertEqual(result['status'], 'download_integrity_error')
        self.assertTrue(result['cleanup_pending'])
        self.assertNotIn('private denied', json.dumps(result))
        self.assertNotIn('download_path', result)
        remaining = list((U._cache_root(self.settings) / 'downloads').iterdir())
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].suffix, '.part')
        self.assertFalse(any(path.suffix == '.zip' for path in remaining))

    def test_already_removed_partial_cleanup_is_not_pending(self):
        url = self.release['assets']['links'][0]['url']
        original_unlink = U.os.unlink
        def already_removed(path, *args, **kwargs):
            original_unlink(path, *args, **kwargs)
            if str(path).endswith('.part'):
                raise FileNotFoundError('private already removed cleanup path')
        with mock.patch.object(U, '_open', side_effect=lambda *args: Response(DATA[:-1], url)), \
                mock.patch.object(U.os, 'unlink', side_effect=already_removed):
            result = U.download(self.settings)
        self.assertEqual(result['status'], 'download_integrity_error')
        self.assertFalse(result['cleanup_pending'])
        self.assertNotIn('private already', json.dumps(result))
        self.assertEqual(list((U._cache_root(self.settings) / 'downloads').iterdir()), [])

    def test_daemon_start_nonblocking_and_coalesced(self):
        entered, release = threading.Event(), threading.Event()
        def blocking(url):
            entered.set()
            release.wait(5)
            return self.fetch(url)
        try:
            with mock.patch.object(U, '_fetch_json', side_effect=blocking):
                started = time.monotonic()
                thread = U.start_check(self.settings, notify=lambda _: None)
                self.assertLess(time.monotonic() - started, .5)
                self.assertTrue(thread.daemon)
                self.assertTrue(entered.wait(2))
                self.assertIs(U.start_check(self.settings), thread)
                release.set()
                thread.join(3)
                self.assertFalse(thread.is_alive())
        finally:
            release.set()

    def test_callback_only_once_for_available_release(self):
        notices = []
        for _ in range(2):
            thread = U.start_check(self.settings, notify=notices.append)
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]['update_available'])
        self.assertEqual(len(self.calls), 2)

    def test_automatic_default_notice_stderr_only(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            U.start_check(self.settings).join(3)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('MUDweave 0.4.0 is available', stderr.getvalue())
        self.assertIn('gitlab.com/codalilitari/mudgpt', stderr.getvalue())

    def test_automatic_disabled_and_offline_are_silent(self):
        for disabled in (True, False):
            U.configure(self.settings, not disabled)
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(U, '_fetch_json', side_effect=U.UpdateError('offline')):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    U.start_check(self.settings).join(3)
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')

    def test_cli_machine_output_and_iteration_override(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = U.main(dict(self.settings, iteration='collection'), ['check', '--iteration', 'muse', '--json'])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result['iteration'], 'muse')
        self.assertEqual(result['asset']['iteration'], 'muse')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            U.main(self.settings, ['--json', 'config', 'enabled', 'off'])
        self.assertFalse(json.loads(output.getvalue())['enabled'])

    def test_status_does_not_access_network(self):
        self.assertEqual(U.status(self.settings)['status'], 'not_checked')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    U._guard(OUTPUT, directory=True)
    if not OUTPUT.is_dir():
        raise SystemExit('Create and verify the explicit external test directory first.')
    unittest.main(verbosity=2)
