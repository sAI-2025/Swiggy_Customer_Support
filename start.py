#!/usr/bin/env python3
import os
import sys
import subprocess


def main():
    print("=" * 60)
    print("Starting Jupiter FAQ Bot Application")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print(f"CWD: {os.getcwd()}")
    
    # Change to Django directory
    os.chdir('/app/book_analysis_project')
    print(f"Changed to: {os.getcwd()}\n")
    
    # Migrations (don't fail on error)
    print("→ Running migrations...")
    result = subprocess.run(
        [sys.executable, 'manage.py', 'migrate', '--noinput'],
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
        [sys.executable, 'manage.py', 'collectstatic', '--noinput', '--clear'],
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
    
    # Use Python module approach (more reliable)
    subprocess.run([
        sys.executable, '-m', 'gunicorn',
        'chatbot_project.wsgi:application',
        '--bind', '0.0.0.0:7860',
        '--workers', '2',
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
