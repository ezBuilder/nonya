"""PyInstaller entry point — bundles the nonya CLI into a standalone binary."""
import sys

from nonya.cli import main

if __name__ == "__main__":
    sys.exit(main())
