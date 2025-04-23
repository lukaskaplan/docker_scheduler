#!/usr/bin/env python3
import docker
import json

# Connect to Docker socket
client = docker.DockerClient(base_url='unix://var/run/docker.sock')

#
#for event in client.events(decode=True, filters={'Type': 'container'}):
#  print(event, "\n")
#
#event.close()

def is_scheduler_enabled(container):
    labels = container.labels or {}
    return labels.get("scheduler.enable", "").lower() == "true"

def get_scheduler_labels(labels):
    return {k: v for k, v in labels.items() if k.startswith("scheduler.")}

# walk through all running containers
for container in client.containers.list():
    if is_scheduler_enabled(container):
        sched_labels = get_scheduler_labels(container.labels)
        print(f"{container.name} ({container.id[:12]}):")
        print(json.dumps(sched_labels, indent=2))
