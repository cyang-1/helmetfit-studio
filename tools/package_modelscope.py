"""Package only files needed to build the hosted HelmetFit Studio."""
from zipfile import ZIP_DEFLATED, ZipFile

from package_studio import MANIFEST, ROOT


def build():
    required = {
        'Dockerfile', '.dockerignore', 'studio/cloud.py', 'fit_mesh.py',
        'deploy/modelscope/install-system.sh', 'deploy/modelscope/install-engines.sh',
        'deploy/modelscope/bambu-cli.sh', 'deploy/modelscope/smoke.py',
        'deploy/modelscope/README.md',
    }
    files = {name: ROOT / name for name in required}
    for pattern in MANIFEST:
        if pattern.startswith(('studio/', 'static/')):
            for path in ROOT.glob(pattern):
                if path.is_file():
                    files[path.relative_to(ROOT).as_posix()] = path
    materials = ROOT / 'release/参赛提交'
    files['review/demo.mp4'] = materials / '量身造盔-Demo.mp4'
    files['review/report.pdf'] = materials / '量身造盔-项目报告书.pdf'
    for name, path in files.items():
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError(f'无效构建文件：{name}')
    target = ROOT / 'release/参赛提交/量身造盔-魔搭完整工作台.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    with ZipFile(temp, 'w', ZIP_DEFLATED) as package:
        guide = files['deploy/modelscope/README.md'].read_text(encoding='utf-8-sig')
        package.writestr('README.md', '# 量身造盔 · HelmetFit Studio\n\n'
            '输入主题或参考图，确认头盔外形，按头围适配并导出 P1S 打印文件。\n\n'
            '[查看真实操作演示](review/demo.mp4) · [阅读项目报告书](review/report.pdf)\n\n' + guide)
        for name, path in sorted(files.items()):
            package.write(path, name)
    with ZipFile(temp) as package:
        if package.testzip():
            raise RuntimeError('工作台压缩包校验失败')
    temp.replace(target)
    return target


if __name__ == '__main__':
    print(build())
