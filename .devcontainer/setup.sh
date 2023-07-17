#!/bin/bash
# Stop on errors
set -e

git config --global user.name = "Teagan Glenn"
git config --global user.email = "that@teagantotally.rocks"

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cp $SCRIPT_DIR/config.yaml $SCRIPT_DIR/../.

pip3 install --user -r $SCRIPT_DIR/requirements.txt
