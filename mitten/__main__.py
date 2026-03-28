"""Support: python -m mitten and PyInstaller entry point"""
try:
    from .cli import main
except ImportError:
    from mitten.cli import main

main()
