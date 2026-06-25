name = "hibob"
title = "HiBob"
version = "1.1.3+dev"

services = {
    "ayon_sync": {"image": "ynput/ayon-hibob-sync:1.0.1"},
}

ayon_required_addons = {}
ayon_compatible_addons = {
    "ftrack": ">1.1.4",
    "planner": ">1.0.0",
}
