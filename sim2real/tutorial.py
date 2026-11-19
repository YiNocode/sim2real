import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from flask import abort, jsonify, request, send_from_directory


PICTURES = {'.jpg': 'image/jpeg', '.png': 'image/png'}
MEDIA = {**PICTURES, '.mp4': 'video/mp4', '.webm': 'video/webm'}
ASSET = re.compile(r'[a-f0-9]{32}\.(?:jpg|png|mp4|webm)')
UPLOAD_KEY = re.compile(r'upload_[0-9]{1,4}')
EMPTY = {'materials': [], 'tools': [], 'steps': []}


def register_tutorial_routes(app, saved_profile, save_picture):
    @app.get('/api/profiles/<identifier>/tutorial')
    def read_tutorial(identifier):
        return jsonify(read_state(saved_profile(identifier) / 'tutorial'))

    @app.get('/api/profiles/<identifier>/tutorial/published')
    def published_tutorial(identifier):
        return jsonify(read_state(saved_profile(identifier) / 'tutorial')['published'])

    @app.post('/api/profiles/<identifier>/tutorial')
    def save_tutorial(identifier):
        directory = saved_profile(identifier) / 'tutorial'
        action = request.form.get('action')
        if action not in {'draft', 'publish'}:
            raise ValueError('请选择保存草稿或发布')
        state = read_state(directory)
        document = request.files.get('tutorial')
        if not document:
            raise ValueError('缺少教程内容')
        contents = document.stream.read(2 * 1024 ** 2 + 1)
        if len(contents) > 2 * 1024 ** 2:
            raise ValueError('教程文字内容不能超过 2 MiB')
        try:
            draft = json.loads(contents)
        except (ValueError, RecursionError) as exc:
            raise ValueError('教程内容无法读取') from exc
        directory.mkdir(exist_ok=True)
        assets = directory / 'media'
        assets.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=directory, prefix='.save-') as temporary:
            stage = Path(temporary)
            state['draft'] = validate_tutorial(draft, state['draft'], stage, save_picture, action)
            if action == 'publish':
                state['published'] = state['draft']
            for upload in stage.iterdir():
                upload.rename(assets / upload.name)
            manifest = stage / 'tutorial.json'
            manifest.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            manifest.replace(directory / 'tutorial.json')
        return jsonify(state)

    @app.get('/api/profiles/<identifier>/tutorial/media/<filename>')
    def tutorial_media(identifier, filename):
        directory = saved_profile(identifier) / 'tutorial'
        if not ASSET.fullmatch(filename):
            abort(404)
        return send_from_directory(directory / 'media', filename, mimetype=MEDIA[Path(filename).suffix])


def read_state(directory):
    try:
        return json.loads((directory / 'tutorial.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'draft': EMPTY, 'published': None}


def validate_tutorial(draft, previous, stage, save_picture, action):
    def rows(data, name):
        values = data.get(name)
        if not isinstance(values, list) or len(values) > 50 or any(not isinstance(row, dict) for row in values):
            raise ValueError('材料、工具和操作步骤各最多 50 项')
        return values

    def field(row, name, label, limit, required=True):
        value = row.get(name, '')
        if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
            raise ValueError(f'请填写{label}（最多 {limit} 字）')
        return value.strip()

    def media(row, allowed, maximum):
        references = row.get('media', [])
        if not isinstance(references, list) or len(references) > maximum:
            raise ValueError(f'此处最多添加 {maximum} 个附件')
        result = []
        for reference in references:
            if not isinstance(reference, str):
                raise ValueError('附件格式无效')
            if reference in existing and Path(reference).suffix in allowed:
                result.append(reference)
                continue
            upload = request.files.get(reference) if UPLOAD_KEY.fullmatch(reference) else None
            if not upload:
                raise ValueError('附件已找不到，请重新选择')
            suffix = Path(upload.filename).suffix.lower()
            suffix = '.jpg' if suffix == '.jpeg' else suffix
            if suffix not in allowed:
                raise ValueError('图片支持 JPG / PNG，视频支持 MP4 / WebM')
            if suffix in PICTURES:
                filename = uuid4().hex + '.jpg'
                save_picture(upload, stage / filename)
            else:
                filename = uuid4().hex + suffix
                save_video(upload, stage / filename, suffix)
            result.append(filename)
        return result

    if not isinstance(draft, dict):
        raise ValueError('教程内容格式无效')
    existing = {item for key in EMPTY for row in previous[key] for item in row['media']}
    required = action == 'publish'
    result = {}
    for key, label in [('materials', '材料名称'), ('tools', '工具名称')]:
        result[key] = [dict(title=field(row, 'title', label, 120, required),
                            media=media(row, PICTURES, 1)) for row in rows(draft, key)]
    result['steps'] = [dict(title=field(row, 'title', '步骤标题', 120, required),
                            instructions=field(row, 'instructions', '操作步骤', 6000, required),
                            outcome=field(row, 'outcome', '完成结果', 3000, required),
                            media=media(row, MEDIA, 8)) for row in rows(draft, 'steps')]
    if required and (not result['materials'] or not result['tools'] or not result['steps']):
        raise ValueError('发布前请至少添加一项材料、一项工具和一个步骤')
    if required and any(not item['media'] for item in result['materials']):
        raise ValueError('发布前请为每项材料添加对应图片')
    return result


def save_video(upload, target, suffix):
    limit = 100 * 1024 ** 2
    header = upload.stream.read(12)
    valid = header[4:8] == b'ftyp' if suffix == '.mp4' else header.startswith(b'\x1a\x45\xdf\xa3')
    if not valid:
        raise ValueError('视频文件无法识别，请上传 MP4 或 WebM')
    size = len(header)
    with target.open('wb') as output:
        output.write(header)
        while chunk := upload.stream.read(1024 ** 2):
            size += len(chunk)
            if size > limit:
                raise ValueError('单个视频不能超过 100 MiB')
            output.write(chunk)
