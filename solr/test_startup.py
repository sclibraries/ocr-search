"""Tests for OCR core discovery before the Solr precreate helper runs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / 'solr' / 'ocr-entrypoint.sh'
FAKE_PROGRAMS = ROOT / 'solr' / 'test_fakes'

def write_core_files(data_dir, core_files):
    for relative_path, contents in (core_files or {}).items():
        core_properties = data_dir / relative_path
        core_properties.parent.mkdir(parents=True, exist_ok=True)
        core_properties.write_text(contents, encoding='utf-8')


def build_call_environment(
    data_dir, curl_args_path, solr_args_path, curl_status, curl_response, solr_delay, env,
):
    call_env = {
        **os.environ,
        'PATH': f'{FAKE_PROGRAMS}:{os.environ["PATH"]}',
        'SOLR_HOME': str(data_dir),
        'OCR_SOLR_DOCKER_ENTRYPOINT': str(FAKE_PROGRAMS / 'solr-entrypoint'),
        'OCR_TEST_CURL_ARGS': str(curl_args_path),
        'OCR_TEST_CURL_RESPONSE': curl_response,
        'OCR_TEST_CURL_STATUS': str(curl_status),
        'OCR_TEST_SOLR_ARGS': str(solr_args_path),
        'OCR_TEST_SOLR_DELAY': str(solr_delay),
    }
    call_env.update(env or {})
    return call_env


def run_entrypoint_function(function, args, call_env):
    return subprocess.run(
        [
            'bash', '-c', 'source "$1"; shift; "$@"',
            'call-entrypoint', str(ENTRYPOINT), function, *args,
        ],
        capture_output=True,
        text=True,
        env=call_env,
        timeout=10,
    )


def read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def read_json_lines(path):
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines]


def call_entrypoint(
    function,
    *args,
    curl_status=0,
    curl_response='',
    env=None,
    core_files=None,
    solr_delay=0,
):
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        data_dir = directory / 'data'
        data_dir.mkdir()
        write_core_files(data_dir, core_files)
        curl_args_path = directory / 'curl-args.jsonl'
        solr_args_path = directory / 'solr-args.json'
        call_env = build_call_environment(
            data_dir, curl_args_path, solr_args_path, curl_status,
            curl_response, solr_delay, env,
        )
        result = run_entrypoint_function(function, args, call_env)
        return result, read_json(solr_args_path), read_json_lines(curl_args_path)


def assert_duplicate_diagnostic(test_case, stderr):
    lines = stderr.splitlines()
    test_case.assertEqual(1, len(lines), stderr)
    prefix = 'OCR Solr startup error: core "ocr" has 2 core.properties definitions under '
    test_case.assertTrue(lines[0].startswith(prefix), stderr)
    test_case.assertTrue(lines[0].endswith('; refusing to start.'), stderr)


class CoreDefinitionTest(unittest.TestCase):
    def test_empty_data_directory_has_no_duplicate_definition(self):
        result, _, _ = call_entrypoint('refuse_duplicate_definitions', 'ocr')

        self.assertEqual(0, result.returncode)
        self.assertEqual('', result.stderr)

    def test_named_core_is_found_below_a_different_directory(self):
        result, _, _ = call_entrypoint(
            'refuse_duplicate_definitions', 'ocr',
            core_files={'ocr_build/core.properties': 'name=ocr\n'},
        )

        self.assertEqual(0, result.returncode)
        self.assertEqual('', result.stderr)

    def test_implicit_core_name_uses_its_instance_directory(self):
        result, _, _ = call_entrypoint(
            'refuse_duplicate_definitions', 'ocr',
            core_files={'ocr/core.properties': ''},
        )

        self.assertEqual(0, result.returncode)
        self.assertEqual('', result.stderr)

    def test_duplicate_names_are_rejected_with_a_diagnostic(self):
        result, _, _ = call_entrypoint(
            'refuse_duplicate_definitions', 'ocr',
            core_files={
                'ocr_build/core.properties': 'name=ocr\n',
                'rollback/ocr/core.properties': 'name=ocr\n',
            },
        )

        self.assertEqual(1, result.returncode)
        assert_duplicate_diagnostic(self, result.stderr)


class MainEntrypointTest(unittest.TestCase):
    def test_empty_data_directory_passes_live_core_creation_through(self):
        result, solr_args, curl_calls = call_entrypoint(
            'main', 'solr-precreate', 'ocr', '/opt/ocr-config', solr_delay=0.2,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['solr-precreate', 'ocr', '/opt/ocr-config'], solr_args)
        self.assertTrue(any(
            'http://127.0.0.1:8983/solr/ocr/admin/ping' in call
            for call in curl_calls
        ))

    def test_existing_live_core_uses_foreground_start(self):
        result, solr_args, _ = call_entrypoint(
            'main', 'solr-precreate', 'ocr', '/opt/ocr-config',
            core_files={'ocr_build/core.properties': 'name=ocr\n'},
            solr_delay=0.2,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['solr-foreground'], solr_args)

    def test_two_live_core_definitions_fail_before_start(self):
        result, solr_args, _ = call_entrypoint(
            'main', 'solr-precreate', 'ocr', '/opt/ocr-config',
            core_files={
                'ocr_build/core.properties': 'name=ocr\n',
                'rollback/ocr/core.properties': 'name=ocr\n',
            },
        )

        self.assertEqual(1, result.returncode)
        self.assertIsNone(solr_args)
        assert_duplicate_diagnostic(self, result.stderr)

    def test_non_live_core_creation_passes_through_unchanged(self):
        result, solr_args, curl_calls = call_entrypoint(
            'main', 'solr-precreate', 'ocr_build', '/opt/ocr-config',
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['solr-precreate', 'ocr_build', '/opt/ocr-config'], solr_args)
        self.assertEqual([], curl_calls)

    def test_live_core_startup_timeout_returns_failure(self):
        result, _, _ = call_entrypoint(
            'main', 'solr-precreate', 'ocr', '/opt/ocr-config',
            curl_status=22,
            env={'OCR_SOLR_STARTUP_TIMEOUT': '1'},
            solr_delay=5,
        )

        self.assertEqual(1, result.returncode)
        self.assertIn('live core "ocr" did not become ready', result.stderr)

    def test_named_init_failure_is_reported(self):
        result, _, _ = call_entrypoint(
            'live_core_has_init_failure', 'ocr',
            curl_response='{"initFailures":{"ocr":"bad config"}}',
        )

        self.assertEqual(0, result.returncode)

    def test_empty_init_failures_are_not_reported(self):
        result, _, _ = call_entrypoint(
            'live_core_has_init_failure', 'ocr',
            curl_response='{"initFailures":{}}',
        )

        self.assertEqual(1, result.returncode)


if __name__ == '__main__':
    unittest.main()
