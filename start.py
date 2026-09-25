#!/usr/bin/env python3
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    print("=" * 60)
    print("Starting Swiggy Django Application")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print(f"CWD: {os.getcwd()}")

    os.chdir(BASE_DIR)
    print(f"Changed to: {os.getcwd()}\n")

    manage_py = os.path.join(BASE_DIR, "manage.py")
    gunicorn_app = "Settings.wsgi:application"
    port = os.environ.get("PORT", "7860")

    def run_step(step_name, command):
        print(f"→ {step_name}...")
        result = subprocess.run(
            [sys.executable, manage_py, *command],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout:
            print(result.stdout)
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"{step_name} failed with exit code {result.returncode}")
        print(f"✓ {step_name} complete\n")

    run_step("Running migrations", ["migrate", "--noinput"])
    run_step("Collecting static files", ["collectstatic", "--noinput", "--clear"])

    # Start Gunicorn
    print("→ Starting Gunicorn server...")
    print("=" * 60)

    os.execvp(sys.executable, [
        sys.executable, '-m', 'gunicorn',
        gunicorn_app,
        '--bind', f'0.0.0.0:{port}',
        '--workers', os.environ.get('WEB_CONCURRENCY', '2'),
        '--threads', '4',
        '--timeout', '120',
        '--worker-class', 'gthread',
        '--log-level', 'info',
        '--access-logfile', '-',
        '--error-logfile', '-',
        '--capture-output',
        '--enable-stdio-inheritance'
    ])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        try:
            main()
        except KeyboardInterrupt:
            print("\n⚠ Shutdown requested")
            sys.exit(0)
        sys.exit(1)
