#!/usr/bin/env bash
# ==============================================================================
# Open-RMF MQTT Fleet Adapter Launch Script
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
NAV_GRAPH_FILE="${SCRIPT_DIR}/../../backend/saved_maps/nav_graphs/0.yaml"

echo "=================================================="
echo " Starting Open-RMF MQTT Fleet Adapter"
echo " Config:    ${CONFIG_FILE}"
echo " Nav Graph: ${NAV_GRAPH_FILE}"
echo "=================================================="

# Source ROS 2 environment if available
if [ -f "/opt/ros/jazzy/setup.bash" ]; then
    source "/opt/ros/jazzy/setup.bash"
    export PYTHONPATH="/opt/ros/jazzy/lib/python/site-packages:${PYTHONPATH}"
fi

if [ -f "/root/rmf_ws/install/setup.bash" ]; then
    source "/root/rmf_ws/install/setup.bash"
fi

# Execute Python adapter
python3 "${SCRIPT_DIR}/mqtt_fleet_adapter.py" \
    --config "${CONFIG_FILE}" \
    --nav_graph "${NAV_GRAPH_FILE}" \
    "$@"
