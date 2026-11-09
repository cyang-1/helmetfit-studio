"""Exercise native geometry and slicing as the image's runtime user, without a cloud key."""
import argparse
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.runtime import geometry
from studio.slicer import slice_plate


def cube(path):
    vertices = [(x * 20, y * 20, z * 20) for x, y, z in [
        (0,0,0), (1,0,0), (1,1,0), (0,1,0), (0,0,1), (1,0,1), (1,1,1), (0,1,1)]]
    surfaces = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
    with path.open('wb') as out:
        out.write(bytes(80) + struct.pack('<I', 12))
        for a,b,c,d in surfaces:
            for i,j,k in [(a,b,c), (a,c,d)]:
                out.write(struct.pack('<12fH', 0,0,0,*vertices[i],*vertices[j],*vertices[k],0))
    return path


def smoke(folder, full):
    prepared = folder / 'source'
    asset = ROOT / 'static/studio-example/helmet.glb' if full else cube(folder / 'cube.stl')
    report = geometry('prepare', prepared, asset)
    if not (prepared / 'display.glb').is_file():
        raise RuntimeError('Blender did not export the display GLB')
    if not full:
        if report['dimensions'] != [20, 20, 20]:
            raise RuntimeError(f"Unexpected cube dimensions: {report['dimensions']}")
        sources = [(prepared / 'model.stl', False)]
    else:
        checked = folder / 'checked'
        quality = geometry('preflight', checked, prepared / 'model.stl',
                           {'generated':False, 'repair':'auto', 'structure':'full_shell'})['quality']
        if quality['status'] not in ('passed','repaired'):
            raise RuntimeError(quality['message'])
        manufacturing = folder / 'manufacturing'
        fitted = geometry('fit', manufacturing, checked / 'model.stl', report['defaults'])
        if fitted['blocking'] or not fitted['ring_valid'] or not fitted['parts']:
            raise RuntimeError(f"Local example failed fit checks: {fitted}")
        sources = [(manufacturing / 'ring.stl', False)]
        sources.extend((manufacturing / name, True) for name in fitted['parts'])
    for number, (model, supports) in enumerate(sources, 1):
        _, inspection = slice_plate(model, folder / f'plate-{number}', supports)
        if inspection['layers'] <= 0 or inspection['grams'] <= 0:
            raise RuntimeError('The P1S file contains no usable toolpath')
        print(json.dumps(dict(model=model.name, **inspection), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helmet', action='store_true', help='Also manufacture the bundled full-size helmet.')
    parser.add_argument('--output', type=Path, help='Keep generated GLB, STL, 3MF, and process logs.')
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        smoke(args.output.resolve(), args.helmet)
    else:
        with tempfile.TemporaryDirectory(prefix='helmetfit-native-smoke-') as directory:
            smoke(Path(directory), args.helmet)
