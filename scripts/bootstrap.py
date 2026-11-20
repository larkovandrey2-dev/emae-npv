#!/usr/bin/env python3
"""Install locked dependencies and build the local application."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-rust', action='store_true', help='Install project-local pinned Rust toolchain if needed')
    options = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ required')
    if not shutil.which('npm') or not shutil.which('git'):
        parser.error('Install Node.js 22.12+ (with npm) and git first')
    engine = [sys.executable, ROOT / 'scripts/bootstrap_engine.py']
    if options.install_rust:
        engine.append('--install-rust')
    run(engine)
    python = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        run([sys.executable, '-m', 'venv', ROOT / '.venv'])
    run([python, '-m', 'pip', 'install', '-r', ROOT / 'backend/requirements.lock.txt'])
    run(['npm', 'ci', '--prefix', ROOT / 'frontend'])
    run(['npm', 'run', 'build', '--prefix', ROOT / 'frontend'])
    print('\nГотово. Запуск: python3 scripts/run.py\nСтенд: http://127.0.0.1:8000')


if __name__ == '__main__':
    main()
