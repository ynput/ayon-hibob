name = "hibob"
title = "HiBob"
version = "1.0.2-dev"

services = {
    "ayon_sync": {"image": f"ynput/ayon-hibob-leecher:1.0.0"},
}

ayon_required_addons = {}
ayon_compatible_addons = {
    "ftrack": ">1.1.4",
    "planner": ">1.0.0",
}
