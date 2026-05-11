"""Fixture E: a comment containing 'import ghunt' should NOT be caught.

The AST does not see comments, so this file should produce zero violations.
"""
# import ghunt is fine in adapters/  -- this is a comment, not code
x = 1
