import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from sim2real.assets import write_assets
from sim2real.bundle import sha256
from sim2real.calibration import COLUMNS, evaluate, fit_axis, metadata_path, rise_time, write_report
from sim2real.policy import DEFAULTS, Sample, enu_ned, heading_flip, limit, observation
from sim2real.profile import EXAMPLE, load, validate
from sim2real.px4 import Control
from sim2real.sitl import write_world
from sim2real.web import create_app


class ProfileTest(unittest.TestCase):
    def test_gazebo_mass_and_rotor_order(self):
        profile = load(EXAMPLE)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_assets(profile, directory)
            sdf = ET.parse(directory / 'model.sdf')
            self.assertAlmostEqual(.8, sum(float(x.text) for x in sdf.findall('.//inertial/mass')))
            self.assertEqual(['ccw', 'ccw', 'cw', 'cw'], [x.text for x in sdf.findall('.//turningDirection')])
            self.assertTrue(sdf.findall('.//imu/angular_velocity/x/noise'))
        broken = copy.deepcopy(profile)
        broken['geometry']['wheelbase_m'] *= 2
        with self.assertRaises(ValueError):
            validate(broken)

    def test_sitl_world_preserves_plugins_and_attaches_custom_model(self):
        with tempfile.TemporaryDirectory() as temp:
            template, output = [Path(temp) / name for name in ('original.sdf', 'world.sdf')]
            template.write_text('<sdf version="1.9"><world name="default"><plugin name="physics"/></world></sdf>',
                                encoding='utf-8')
            write_world(template, output)
            world = ET.parse(output).getroot().find('world')
            self.assertEqual('physics', world.find('plugin').get('name'))
            self.assertEqual('model://sim2real_quad', world.find('include/uri').text)


class WebTest(unittest.TestCase):
    def test_tutorial_draft_publish_and_media(self):
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(temp).test_client()
            saved = client.post('/api/profiles', data={'profile': json.dumps(load(EXAMPLE))})
            url = f"/api/profiles/{saved.json['id']}/tutorial"
            self.assertIsNone(client.get(url + '/published').json)
            draft = {'materials': [{'title': '分电板', 'media': ['upload_0']}],
                     'tools': [{'title': '烙铁'}],
                     'steps': [{'title': '准备工位', 'instructions': '检查零件', 'outcome': '零件齐全',
                                'media': ['upload_1']}]}

            def submit(action, data, **uploads):
                return client.post(url, data={
                    'action': action,
                    'tutorial': (io.BytesIO(json.dumps(data).encode()), 'tutorial.json'),
                    **uploads,
                })

            photo = io.BytesIO()
            Image.new('RGB', (8, 8), 'red').save(photo, format='PNG')
            photo.seek(0)
            video = b'\0\0\0\x18ftypmp42' + bytes(24)
            response = submit('draft', draft, upload_0=(photo, 'part.png'),
                              upload_1=(io.BytesIO(video), 'demo.mp4'))
            self.assertEqual(200, response.status_code)
            self.assertIsNone(client.get(url + '/published').json)
            draft = response.json['draft']
            self.assertEqual(200, submit('publish', draft).status_code)
            self.assertEqual(draft, client.get(url + '/published').json)
            draft['steps'][0]['title'] = '未发布的修改'
            self.assertEqual(200, submit('draft', draft).status_code)
            self.assertEqual('准备工位', client.get(url + '/published').json['steps'][0]['title'])
            with client.get(url + '/media/' + draft['steps'][0]['media'][0],
                            headers={'Range': 'bytes=0-11'}) as response:
                self.assertEqual(206, response.status_code)
                self.assertEqual('video/mp4', response.mimetype)
                self.assertEqual(video[:12], response.data)
            draft['materials'][0]['media'] = []
            self.assertEqual(400, submit('publish', draft).status_code)
            self.assertEqual('准备工位', client.get(url + '/published').json['steps'][0]['title'])

    def test_pid_review_and_wiring_examples(self):
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(temp).test_client()
            baseline = b'1\t1\tMC_ROLLRATE_P\t0.15\t9\n1\t1\tMC_PITCHRATE_K\t1\t9\n'
            saved = client.post('/api/profiles', data={
                'profile': json.dumps(load(EXAMPLE)),
                'px4_params': (io.BytesIO(baseline), 'reference.params'),
            })
            self.assertEqual(201, saved.status_code)
            endpoint = f"/api/profiles/{saved.json['id']}"
            current = b'1 1 MC_ROLLRATE_P 0.16 9\n1 1 MC_YAW_P 2 9\n'
            comparison = client.post(endpoint + '/pid-compare', data={
                'current': (io.BytesIO(current), 'current.params'),
            })
            self.assertEqual(200, comparison.status_code)
            self.assertEqual(['missing', 'changed', 'missing'],
                             [row['status'] for row in comparison.json])
            invalid = client.post(endpoint + '/pid-compare', data={
                'current': (io.BytesIO(b'1 1 MC_ROLLRATE_P nan 9'), 'current.params'),
            })
            self.assertEqual(400, invalid.status_code)

            path = endpoint + '/wiring'
            self.assertEqual(400, client.post(path, data={
                'caption': '分电板', 'image': (io.BytesIO(b'not an image'), 'bad.jpg'),
            }).status_code)
            self.assertEqual([], client.get(path).json)
            photo = io.BytesIO()
            Image.new('RGB', (32, 24), 'red').save(photo, format='PNG')
            photo.seek(0)
            uploaded = client.post(path, data={'caption': '分电板', 'image': (photo, 'reference.png')})
            self.assertEqual(201, uploaded.status_code)
            self.assertEqual([uploaded.json], client.get(path).json)
            with client.get(f"{path}/{uploaded.json['id']}") as response:
                self.assertEqual('image/jpeg', response.mimetype)
                with Image.open(io.BytesIO(response.data)) as stored:
                    self.assertEqual((32, 24), stored.size)
            image_path = f"{path}/{uploaded.json['id']}"
            self.assertEqual(403, client.delete(image_path, headers={'Sec-Fetch-Site': 'cross-site'}).status_code)
            self.assertEqual([uploaded.json], client.get(path).json)
            self.assertEqual(204, client.delete(image_path).status_code)
            self.assertEqual([], client.get(path).json)
            self.assertEqual(404, client.get(image_path).status_code)
            self.assertEqual(404, client.delete(image_path).status_code)
            self.assertEqual(404, client.get('/api/profiles/other-r1/wiring').status_code)

    def test_save_immutable_version_and_register_ulog_without_mocap(self):
        with tempfile.TemporaryDirectory() as temp:
            client = create_app(temp).test_client()
            with client.get('/') as page:
                self.assertEqual(200, page.status_code)
            profile = client.get('/api/example').json
            profile['localization']['source'] = 'flow_range'
            payload = {'profile': json.dumps(profile)}
            self.assertEqual(201, client.post('/api/profiles', data=payload).status_code)
            self.assertEqual(409, client.post('/api/profiles', data=payload).status_code)
            data = {'profile': 'lab-quad-example-r1', 'source': 'flow_range', 'scenario': 'hover',
                    'purpose': 'fit', 'voltage_v': '15.8'}
            response = client.post('/api/flights', data={**data, 'ulog': (io.BytesIO(b'no'), 'test.ulg')})
            self.assertEqual(400, response.status_code)
            self.assertEqual([], client.get('/api/flights').json)
            header = b'ULog\x01\x12\x35\x01' + bytes(8)
            response = client.post('/api/flights', data={**data, 'ulog': (io.BytesIO(header), 'test.ulg')})
            self.assertEqual(201, response.status_code)
            self.assertEqual('px4_estimate', response.json['truth'])
            self.assertTrue(response.json['voltage_in_window'])
            self.assertTrue((Path(temp) / 'flights' / response.json['id'] / 'flight.ulg').is_file())
            with client.get(f"/api/flights/{response.json['id']}/ulog") as download:
                self.assertEqual(header, download.data)


class ControlTest(unittest.TestCase):
    def test_frames_inference_timeout_and_estimator_reset(self):
        config = DEFAULTS
        control = Control(config, actor=None)
        state = Sample([1., 2., 3.], [0., .3, 0.], 0., 8., (0, 0, 0))
        control.update_state(state)
        reference = [1., 2., 3.5]
        control.reference = Sample(reference, [], 0., 8., ())
        control.infer(8.)
        np.testing.assert_allclose([0., 0., -.4], enu_ned(control.latest.velocity))
        self.assertAlmostEqual(np.pi/2, heading_flip(0.))
        inputs = observation(state.position, state.velocity, state.yaw, reference, config)
        np.testing.assert_allclose(inputs, [.5, 1., 1.5, 0., .5, 0., 0., 1., 0., 0., .25])
        self.assertTrue(control.ready(8.1))
        self.assertFalse(control.ready(8.5))
        with self.assertRaises(ValueError):
            limit([float('nan'), 0, 0], config['speed_limit_mps'])
        control.update_state(None)
        self.assertFalse(control.ready(8.1))
        control.update_state(Sample(state.position, state.velocity, 0., 8.1, (0, 1, 0)))
        control.infer(8.1)
        self.assertFalse(control.ready(8.1))


class CalibrationTest(unittest.TestCase):
    def test_fit_and_independent_trial_coverage(self):
        t = np.arange(0., 5., .02)
        command = np.where(t < 1, 0., .40)
        response = .36 * (1 - np.exp(-np.maximum(t - 1.10, 0.) / .3))
        result = fit_axis(t, command, response)
        self.assertAlmostEqual(.9, result['gain'], delta=.05)
        self.assertAlmostEqual(.3, result['tau_s'], delta=.05)
        self.assertAlmostEqual(.1, result['delay_s'], delta=.04)
        matrix = np.zeros((len(t), len(COLUMNS)))
        matrix[:, 0], matrix[:, 4], matrix[:, 10], matrix[:, -1] = t, response, command, 15.8
        self.assertAlmostEqual(.3*np.log(9), rise_time(matrix, 0), delta=.08)
        with tempfile.TemporaryDirectory() as temp:
            csv = Path(temp) / 'example.csv'
            np.savetxt(csv, matrix, delimiter=',', header=','.join(COLUMNS), comments='')
            report = evaluate([{'scenario': 'step-x', 'real': csv, 'sim': csv}], load(EXAMPLE))
            self.assertFalse(report['coverage_complete'])
            self.assertEqual('px4_estimate', report['trials'][0]['position_basis'])
            self.assertEqual(0., report['trials'][0]['rise_relative_error'])
            write_report(metadata_path(csv), {'ulog_sha256': 'same-flight', 'csv_sha256': sha256(csv)})
            with self.assertRaisesRegex(ValueError, '重复'):
                evaluate([{'scenario': 'step-x', 'real': csv, 'sim': csv}], load(EXAMPLE), ['same-flight'])


if __name__ == '__main__':
    unittest.main()
