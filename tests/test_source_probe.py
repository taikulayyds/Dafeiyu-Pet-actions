import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_probe_sync', ROOT / 'scripts/sync_upstream.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

class IndependentSourceProbe(unittest.TestCase):
    def probe(self, stream=None, duration='10.042000'):
        data = {'streams': [stream or {'width':640, 'height':360, 'codec_name':'vp9'}],
                'format': {'duration': duration}}
        result = subprocess.CompletedProcess(['ffprobe'], 0, json.dumps(data), '')
        with patch.object(sync.subprocess, 'run', return_value=result) as run:
            answer = sync.independent_source_duration(Path('/synthetic/source.webm'))
            call = run.call_args
            self.assertEqual(call.args[0][0], 'ffprobe')
            self.assertEqual(call.kwargs['timeout'], 120)
            self.assertNotIn('GH_TOKEN', call.kwargs['env'])
        return answer

    def test_real_duration_classes(self):
        for seconds, expected in [('10.042000',10042), ('5.125000',5125), ('4.917000',4917)]:
            with self.subTest(seconds=seconds):
                self.assertEqual(self.probe(duration=seconds), expected)

    def test_stream_ticks_take_precedence(self):
        self.assertEqual(self.probe({'width':640, 'height':360, 'codec_name':'vp9',
            'duration_ts':123, 'time_base':'1/24'}, duration='99'), 5125)

    def test_reject_wrong_dimensions_codec_and_duration(self):
        for stream in [{'width':320,'height':180,'codec_name':'vp9'},
                       {'width':640,'height':360,'codec_name':'h264'}]:
            with self.assertRaisesRegex(RuntimeError, 'manual review'):
                self.probe(stream)
        for duration in ['0', '-1', '60.001']:
            with self.assertRaisesRegex(RuntimeError, 'duration'):
                self.probe(duration=duration)

    def test_negative_preview_limit_rejected(self):
        with patch('sys.argv', ['sync_upstream.py', '--max-actions', '-1']):
            with self.assertRaises(SystemExit) as error:
                sync.main()
            self.assertNotEqual(error.exception.code, 0)

    def test_workflow_preview_default_and_schedule_gated(self):
        text = (ROOT / '.github/workflows/sync-upstream.yml').read_text()
        self.assertIn('default: preview', text)
        self.assertIn("vars.ACTION_SYNC_ENABLED == 'true'", text)
        self.assertIn('args+=(--publish)', text)
        self.assertIn('args+=(--only-action youxian-hengga)', text)
        self.assertIn('python3 scripts/sync_upstream.py "${args[@]}"', text)
        self.assertIn("github.event.inputs.action || 'all'", text)
        self.assertIn('sync-report.json', text)

if __name__ == '__main__':
    unittest.main()
