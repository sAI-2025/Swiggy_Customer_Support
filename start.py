#!/usr/bin/env python3
import os
import sys
import subprocess


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

    # Migrations (don't fail on error)
    print("→ Running migrations...")
    result = subprocess.run(
        [sys.executable, manage_py, 'migrate', '--noinput'],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("✓ Migrations complete\n")
    else:
        print(f"⚠ Migration warning: {result.stderr}\n")

    # Static files
    print("→ Collecting static files...")
    result = subprocess.run(
        [sys.executable, manage_py, 'collectstatic', '--noinput', '--clear'],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("✓ Static files collected\n")
    else:
        print(f"⚠ Static files warning: {result.stderr}\n")

    # Start Gunicorn
    print("→ Starting Gunicorn server...")
    print("=" * 60)

    subprocess.run([
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
        print("\n⚠ Shutdown requested")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
