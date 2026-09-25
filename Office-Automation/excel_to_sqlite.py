"""Compatibility shim: the extractor now lives in the `xl2ai.extract` package.

`python excel_to_sqlite.py FILE ... [-o OUT]` keeps working exactly as before; the preferred form is
`python -m xl2ai extract FILE ... [-o OUT]`.
"""
import sys

from xl2ai.extract.pipeline import main

if __name__ == "__main__":
    sys.exit(main())
