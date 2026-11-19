import hmac
import json
import math
import os
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import yaml
from flask import Flask, abort, jsonify, request, send_from_directory
from PIL import Image, ImageOps

from .assets import write_assets
from .profile import EXAMPLE, SOURCES, encode, key, load, number, validate
from .tutorial import register_tutorial_routes


WEB = Path(__file__).resolve().parents[1] / 'web'
PROFILE_ID = r'[a-z][a-z0-9-]{1,47}-r[1-9][0-9]{0,5}'
IMAGE_LIMIT = 10 * 1024 ** 2
PID_FIELDS = re.compile(r'MC_(?:ROLL|PITCH|YAW)(?:_P|RATE_(?:P|I|D|K|FF))')


def read_pid_snapshot(stream):
    contents = stream.read(1024 ** 2 + 1)
    if len(contents) > 1024 ** 2:
        raise ValueError('PX4 参数文件不能超过 1 MiB')
    try:
        lines = contents.decode('utf-8-sig').splitlines()
    except UnicodeError as exc:
        raise ValueError('请选择 QGroundControl 导出的文本参数文件') from exc
    parameters = {}
    for line in lines:
        parts = line.split('#', 1)[0].split()
        if len(parts) != 5 or not PID_FIELDS.fullmatch(parts[2]):
            continue
        name, raw = parts[2:4]
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f'{name} 不是有效数值') from exc
        if not math.isfinite(value) or name in parameters:
            raise ValueError(f'{name} 数值无效或重复，请导出单架飞控的参数')
        parameters[name] = value
    if not parameters:
        raise ValueError('没有找到多旋翼姿态或角速度 PID 参数，请检查导出文件')
    return parameters


def save_picture(upload, destination):
    contents = upload.stream.read(IMAGE_LIMIT + 1)
    if len(contents) > IMAGE_LIMIT:
        raise ValueError('单张图片不能超过 10 MiB')
    try:
        with Image.open(BytesIO(contents), formats=['JPEG', 'PNG']) as source:
            if source.width * source.height > 24_000_000 or getattr(source, 'is_animated', False):
                raise ValueError('请选择不超过 2400 万像素的单张照片')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((2000, 2000))
            image.save(destination, quality=88)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('无法读取图片，请上传有效的 JPG 或 PNG') from exc


def create_app(data_dir=None):
    app = Flask(__name__, static_folder=str(WEB), static_url_path='/static')
    app.config['MAX_CONTENT_LENGTH'] = 1024 ** 3
    root = Path(data_dir or os.environ.get('SIM2REAL_DATA', 'workspace')).resolve()
    profiles, flights = root / 'profiles', root / 'flights'
    for directory in (profiles, flights):
        directory.mkdir(parents=True, exist_ok=True)
    token = os.environ.get('SIM2REAL_WEB_PASSWORD')

    @app.before_request
    def authenticate():
        if request.method in {'POST', 'DELETE'} and request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='请从工作台页面提交'), 403
        auth = request.authorization
        if token and (not auth or not hmac.compare_digest((auth.password or '').encode(), token.encode())):
            return ('需要登录', 401, {'WWW-Authenticate': 'Basic realm="Sim2Real", charset="UTF-8"'})

    @app.after_request
    def protect(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='本次上传总大小需低于 1 GiB'), 413

    @app.get('/')
    def index():
        return send_from_directory(WEB, 'index.html')

    @app.get('/api/example')
    def example():
        return jsonify(load(EXAMPLE))

    @app.post('/api/parse-profile')
    def parse_profile():
        upload = request.files.get('yaml')
        if not upload:
            raise ValueError('请选择 YAML 档案')
        try:
            contents = upload.stream.read(65537)
            if len(contents) > 65536:
                raise ValueError('机型档案不能超过 64 KiB')
            profile = yaml.safe_load(contents)
        except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
            raise ValueError('无法解析 YAML 档案') from exc
        return jsonify(validate(profile))

    @app.get('/api/profiles')
    def list_profiles():
        return jsonify([load(path) for path in sorted(profiles.glob('*/profile.yaml'))])

    @app.post('/api/profiles')
    def save_profile():
        details = request.form.get('profile', '')
        if len(details) > 65536:
            raise ValueError('机型档案不能超过 64 KiB')
        profile = validate(json.loads(details))
        identifier = key(profile)
        destination = profiles / identifier
        if destination.exists():
            return jsonify(error='该机型版本已存在，请提高版本号后保存'), 409
        with TemporaryDirectory(dir=profiles, prefix='.profile-') as stage:
            stage = Path(stage)
            (stage / 'profile.yaml').write_text(encode(profile), encoding='utf-8')
            snapshot = request.files.get('px4_params')
            if snapshot:
                store_upload(snapshot, stage / 'px4.params', ('.params', '.param', '.txt'))
            write_assets(profile, stage / 'assets')
            try:
                stage.rename(destination)
            except OSError:
                if destination.exists():
                    return jsonify(error='该机型版本已存在，请提高版本号后保存'), 409
                raise
        return jsonify(id=identifier, profile=profile, px4_snapshot=bool(snapshot)), 201

    @app.get('/api/profiles/<identifier>/download')
    def download_profile(identifier):
        return send_from_directory(profiles, f'{identifier}/profile.yaml', as_attachment=True,
                                   download_name=f'{identifier}.yaml')

    def saved_profile(identifier):
        if not re.fullmatch(PROFILE_ID, identifier) or not (profiles / identifier / 'profile.yaml').is_file():
            abort(404)
        return profiles / identifier

    register_tutorial_routes(app, saved_profile, save_picture)

    @app.route('/api/profiles/<identifier>/wiring', methods=['GET', 'POST'])
    def wiring_examples(identifier):
        directory = saved_profile(identifier) / 'wiring'
        if request.method == 'GET':
            records = []
            for path in sorted(directory.glob('*/manifest.json')):
                if not re.fullmatch('[a-f0-9]{32}', path.parent.name):
                    continue
                try:
                    records.append(json.loads(path.read_text(encoding='utf-8')))
                except FileNotFoundError:
                    continue
            return jsonify(records)
        upload = request.files.get('image')
        caption = request.form.get('caption', '').strip()
        if not upload or not caption or len(caption) > 120:
            raise ValueError('请选择图片，并填写 1–120 字的焊接部位说明')
        directory.mkdir(exist_ok=True)
        record = dict(id=uuid4().hex, caption=caption)
        with TemporaryDirectory(dir=directory, prefix='.upload-') as stage:
            stage = Path(stage)
            save_picture(upload, stage / 'image.jpg')
            (stage / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            stage.rename(directory / record['id'])
        return jsonify(record), 201

    @app.route('/api/profiles/<identifier>/wiring/<image_id>', methods=['GET', 'DELETE'])
    def wiring_image(identifier, image_id):
        directory = saved_profile(identifier) / 'wiring'
        if not re.fullmatch('[a-f0-9]{32}', image_id):
            abort(404)
        example = directory / image_id
        if request.method == 'DELETE':
            if not (example / 'manifest.json').is_file():
                abort(404)
            with TemporaryDirectory(dir=directory, prefix='.deleted-') as stage:
                try:
                    example.rename(Path(stage) / image_id)
                except FileNotFoundError:
                    abort(404)
            return '', 204
        return send_from_directory(example, 'image.jpg', mimetype='image/jpeg')

    @app.post('/api/profiles/<identifier>/pid-compare')
    def compare_pid(identifier):
        baseline_path = saved_profile(identifier) / 'px4.params'
        baseline_upload = request.files.get('baseline')
        current = request.files.get('current')
        if not current:
            raise ValueError('请上传当前 PX4 参数文件')
        if baseline_upload:
            baseline = read_pid_snapshot(baseline_upload.stream)
        elif baseline_path.is_file():
            with baseline_path.open('rb') as stream:
                baseline = read_pid_snapshot(stream)
        else:
            raise ValueError('该版本暂无参数快照，请上传参考机的 PX4 参数文件')
        actual = read_pid_snapshot(current.stream)
        rows = []
        for name in sorted(baseline.keys() | actual.keys()):
            before, after = baseline.get(name), actual.get(name)
            status = 'missing' if before is None or after is None else 'same' if before == after else 'changed'
            rows.append(dict(name=name, baseline=before, current=after, status=status))
        return jsonify(rows)

    @app.get('/api/flights')
    def list_flights():
        records = [json.loads(path.read_text(encoding='utf-8')) for path in flights.glob('*/manifest.json')]
        return jsonify(sorted(records, key=lambda record: record['created_at'], reverse=True))

    @app.get('/api/flights/<run_id>/<kind>')
    def download_flight(run_id, kind):
        names = {'ulog': 'flight.ulg', 'commands': 'commands.csv', 'reference': 'reference.csv'}
        if not re.fullmatch('[a-f0-9]{32}', run_id) or kind not in names:
            abort(404)
        manifest = flights / run_id / 'manifest.json'
        if not manifest.is_file():
            abort(404)
        metadata = json.loads(manifest.read_text(encoding='utf-8'))['files'].get(kind)
        if not metadata:
            abort(404)
        return send_from_directory(flights / run_id, names[kind], as_attachment=True, download_name=metadata['name'])

    @app.post('/api/flights')
    def record_flight():
        data = request.form
        identifier = data.get('profile', '')
        if not re.fullmatch(PROFILE_ID, identifier):
            raise ValueError('请选择已保存的机型版本')
        path = profiles / identifier / 'profile.yaml'
        if not path.is_file():
            raise ValueError('请选择已保存的机型版本')
        if data.get('source') not in SOURCES - {'none'}:
            raise ValueError('请选择本次实际使用的定位来源')
        if data.get('scenario') not in {'hover', 'step-x', 'step-y', 'step-z', 'trajectory'}:
            raise ValueError('请选择试飞科目')
        if data.get('purpose') not in {'fit', 'validation'}:
            raise ValueError('请选择日志用途')
        voltage = number(float(data.get('voltage_v', 'nan')), '起飞电压', positive=True)
        ulog = request.files.get('ulog')
        if not ulog:
            raise ValueError('请上传 PX4 ULog 文件')
        run_id = uuid4().hex
        with TemporaryDirectory(dir=flights, prefix='.flight-') as stage:
            stage = Path(stage)
            files = {'ulog': store_upload(ulog, stage / 'flight.ulg', ('.ulg',))}
            with (stage / 'flight.ulg').open('rb') as stream:
                if stream.read(7) != b'ULog\x01\x12\x35' or len(stream.read(9)) != 9:
                    raise ValueError('文件缺少有效的 PX4 ULog 文件头')
            for name in ('commands', 'reference'):
                attachment = request.files.get(name)
                if attachment:
                    files[name] = store_upload(attachment, stage / f'{name}.csv', ('.csv',))
            low, high = load(path)['battery']['voltage_window_v']
            record = dict(id=run_id, profile=identifier, source=data['source'], scenario=data['scenario'],
                          purpose=data['purpose'], voltage_v=voltage, voltage_in_window=low <= voltage <= high,
                          notes=data.get('notes', '').strip()[:1500], files=files,
                          truth='independent_reference_uploaded' if 'reference' in files else 'px4_estimate',
                          created_at=datetime.now(timezone.utc).isoformat())
            (stage / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            stage.rename(flights / run_id)
        return jsonify(record), 201

    return app


def store_upload(upload, path, allowed):
    name = upload.filename.replace('\\', '/').rsplit('/', 1)[-1]
    if Path(name).suffix.lower() not in allowed:
        raise ValueError(f'{name} 文件类型错误，允许 {" / ".join(allowed)}')
    upload.save(path)
    if not path.stat().st_size:
        raise ValueError(f'{name} 是空文件')
    return {'name': name, 'bytes': path.stat().st_size}
