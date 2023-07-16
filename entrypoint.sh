#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

apt-get update && apt-get install -y python3 python3-dev python3-pip

pip install flask[async]==2.3.2 pyyaml==6.0.0

python3 "$SCRIPT_DIR/api.py"