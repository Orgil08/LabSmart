#!/bin/sh
cd "$(dirname "$0")/app" || exit 1
python3 server.py --demo
