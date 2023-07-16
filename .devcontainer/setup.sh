#!/bin/bash
# Stop on errors
set -e

cp config.yaml /workspaces/pyapi/.

pip3 install -r requirements.txt

container install
