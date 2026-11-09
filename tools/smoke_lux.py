"""Validate credentials or generate/resume original helmet examples with real Lux3D."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio import lux


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "single", "multi"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--resume", help="existing task ID verified in the Aholo console")
    args = parser.parse_args()
    if args.mode == "check":
        key, region = lux._settings()
        lux._gateway(key, region, "generate/task/list?page=1&pagesize=1")
        print(f"Aholo {region}: authentication succeeded")
        return
    directory = args.directory or ROOT / "jobs" / ("lux-" + args.mode)
    images = [ROOT / "static/lux-example/front.png"]
    if args.mode == "multi":
        images.append(ROOT / "static/lux-example/side.png")
    def progress(message, task=None):
        print(message, f"[task {task}]" if task else "", flush=True)
    output = lux.generate(images, directory, progress, args.resume)
    print(f"GLB verified: {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    try:
        main()
    except lux.LuxError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
