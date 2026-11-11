#!/usr/bin/env python3
"""Print the current project's learnings queue as JSON. Helper for slash commands.

--count prints only the number of items, so commands don't need an inline
`python3 -c` (which can't be pre-approved narrowly) to count them.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import load_queue
import json

items = load_queue()
if "--count" in sys.argv[1:]:
    print(len(items))
else:
    print(json.dumps(items, indent=2))
