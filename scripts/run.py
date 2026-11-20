#!/usr/bin/env python3
"""Run Factor Lab on localhost, or API+Vite with --dev."""
import argparse
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    python = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists() or not (ROOT / '.engine/build-info.json').exists():
        parser.error('Сначала выполните python3 scripts/bootstrap.py --install-rust')
    if not args.dev and not (ROOT / 'frontend/dist/index.html').exists():
        parser.error('Сначала выполните npm ci --prefix frontend && npm run build --prefix frontend')
    if args.dev and args.port != 8000:
        parser.error('--dev использует API порт 8000 (Vite proxy)')
    api = [str(python), '-m', 'uvicorn', 'app.main:app', '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1', '--port', str(args.port)]
    if args.dev:
        api.append('--reload')
    processes = []
    def stop(*_):
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        processes.append(subprocess.Popen(api, cwd=ROOT))
        if args.dev:
            processes.append(subprocess.Popen(['npm', 'run', 'dev', '--prefix', str(ROOT / 'frontend'), '--', '--host', '127.0.0.1'], cwd=ROOT))
        print(f'Factor Lab: http://127.0.0.1:{5173 if args.dev else args.port}', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        sys.exit(next((p.returncode for p in processes if p.returncode), 0))
    except KeyboardInterrupt:
        pass
    finally:
        stop()


if __name__ == '__main__':
    main()
