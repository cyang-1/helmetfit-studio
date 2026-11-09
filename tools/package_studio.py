"""Create a distributable source/runtime application without copying user data or keys."""
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = [
    'start-studio.bat', 'STUDIO.md', 'fit_mesh.py', '.gitignore',
    'studio/*.py', 'static/studio/*.html', 'static/studio/*.css', 'static/studio/*.js',
    'static/studio/vendor/**/*.js', 'static/studio/vendor/LICENSE',
    'static/vendor/*.js', 'static/vendor/*LICENSE*', 'static/studio-example/helmet.glb',
    'static/lux-example/*.png', 'tools/render_lux_example.py', 'tools/smoke_lux.py', 'tools/package_studio.py',
    'tools/verify_studio.py',
    'tests/test_geometry*.py', 'tests/test_studio*.py', 'tests/studio/*.py',
    'docs/plans/2026-11-07-helmetfit-studio.md',
]


def build():
    files = sorted({p for pattern in MANIFEST for p in ROOT.glob(pattern) if p.is_file()})
    required = ['studio/lux.py', 'studio/lux_upload.py', 'studio/prompts.py', 'static/studio/upload-text.js',
                'static/studio/wear-structure.js', 'static/studio/upload-drafts.js',
                'static/studio/pose-tools.js', 'static/studio/pose-math.js', 'static/studio/pose-tools.css',
                'static/studio/vendor/controls/TransformControls.js',
                'studio/geometry.py', 'studio/geometry_cap.py', 'studio/geometry_preflight.py',
                'static/studio/index.html', 'static/studio/workspace.js', 'static/studio/upload-views.js',
                'static/studio/upload-slots.css', 'static/lux-example/angle-front.png', 'static/lux-example/angle-left.png',
                'static/studio/app.js', 'static/studio/vendor/loaders/GLTFLoader.js', 'static/studio-example/helmet.glb',
                'static/lux-example/front.png', 'static/lux-example/side.png',
                'start-studio.bat', 'STUDIO.md']
    if not all(ROOT / p in files for p in required):
        raise RuntimeError('应用构建输入不完整。')
    target = ROOT / 'release/HelmetFit-Studio-Windows.zip'
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in files:
            package.write(path, 'HelmetFit-Studio/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(temporary) as package:
        if package.testzip():
            raise RuntimeError('压缩包校验失败。')
    temporary.replace(target)
    return target


if __name__ == '__main__':
    print(build())
