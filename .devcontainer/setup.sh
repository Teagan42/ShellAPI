#!/bin/bash
# Stop on errors
set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cp $SCRIPT_DIR/config.yaml /workspaces/pyapi/.

pip3 install --user -r $SCRIPT_DIR/requirements.txt
