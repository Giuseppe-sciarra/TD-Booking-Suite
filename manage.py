#!/usr/bin/env python
import os,sys
from pathlib import Path
for line in (Path(__file__).parent / '.env').read_text(encoding='utf-8').splitlines() if (Path(__file__).parent / '.env').exists() else []:
    if line and not line.startswith('#') and '=' in line:
        key,value=line.split('=',1);os.environ.setdefault(key,value)
os.environ.setdefault("DJANGO_SETTINGS_MODULE","tdbooking.settings")
from django.core.management import execute_from_command_line
execute_from_command_line(sys.argv)
