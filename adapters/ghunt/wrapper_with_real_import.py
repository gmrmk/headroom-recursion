"""Fixture E (negative): `import ghunt` inside adapters/<id>/wrapper.py — exempt.

NOTE: this file lives at `adapters/ghunt/wrapper_with_real_import.py`, NOT
`wrapper.py`. The path-based exemption requires the basename to be exactly
`wrapper.py`. So this file SHOULD still trigger the lint when scanned. It
exists to assert the exemption is filename-precise, not directory-broad.
"""
import ghunt
