import hashlib
import json
import shutil
from pathlib import Path

from .policy import ACTIONS, DEFAULTS, OBSERVATIONS
from .profile import encode, key


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def create(profile, directory, actor_path=None):
    directory = Path(directory)
    torch_version = None
    if actor_path is not None:
        import torch

        actor = torch.jit.load(str(actor_path), map_location='cpu').eval()
        with torch.inference_mode():
            output = actor(torch.zeros(3, len(OBSERVATIONS)))
        if output.shape != (3, len(ACTIONS)) or not torch.isfinite(output).all():
            raise ValueError('TorchScript actor 必须接受 [N,11]，并返回有限的 [N,3] m/s 输出')
        torch_version = torch.__version__
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'profile.yaml').write_text(encode(profile), encoding='utf-8')
    metadata = dict(version=1, profile=key(profile), firmware=profile['hardware']['firmware'],
                    localization=profile['localization']['source'], example=bool(profile.get('example')),
                    algorithm='torchscript' if actor_path else 'baseline', torch_version=torch_version,
                    observations=OBSERVATIONS, actions=ACTIONS, frame='ENU', **DEFAULTS)
    if actor_path is not None:
        shutil.copyfile(actor_path, directory / 'actor.ts')
        metadata['actor_sha256'] = sha256(directory / 'actor.ts')
    metadata['profile_sha256'] = sha256(directory / 'profile.yaml')
    (directory / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    runtime = directory / 'sim2real'
    runtime.mkdir()
    for name in ('__init__.py', 'policy.py', 'bundle.py', 'profile.py', 'px4.py', 'reference.py'):
        shutil.copyfile(Path(__file__).with_name(name), runtime / name)
    (directory / 'requirements.txt').write_text('numpy>=1.24,<3\nPyYAML>=6,<7\n', encoding='utf-8')
    return metadata


def open_bundle(directory):
    directory = Path(directory)
    config = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    if (config.get('version'), config.get('observations'), config.get('actions')) != (1, OBSERVATIONS, ACTIONS):
        raise ValueError('部署包接口版本不匹配')
    if config['profile_sha256'] != sha256(directory / 'profile.yaml'):
        raise ValueError('部署包机型档案校验失败')
    actor = None
    if config['algorithm'] == 'torchscript':
        import torch

        path = directory / 'actor.ts'
        if config['actor_sha256'] != sha256(path):
            raise ValueError('部署包 actor 校验失败')
        torch.set_num_threads(1)
        actor = torch.jit.load(str(path), map_location='cpu').eval()
    return config, actor
