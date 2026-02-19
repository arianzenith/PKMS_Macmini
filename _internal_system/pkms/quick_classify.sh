#!/bin/bash
# PKMS 빠른 분류 스크립트

cd "$(dirname "$0")/scripts"
python manual_classify.py --all --auto
