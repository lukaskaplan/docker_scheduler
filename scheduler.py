#!/usr/bin/env python3
import docker
import json
from datetime import datetime

# Connect to Docker socket
client = docker.DockerClient(base_url='unix://var/run/docker.sock')

#
#for event in client.events(decode=True, filters={'Type': 'container'}):
#  print(event, "\n")
#
#event.close()

def is_scheduler_enabled(container):
    """Check if the scheduler is enabled for the container."""
    labels = container.labels or {}
    return labels.get("scheduler.enable", "").lower() == "true"

def get_scheduler_labels(labels):
    return {k: v for k, v in labels.items() if k.startswith("scheduler.")}

def get_scheduler_jobs(container):
    """
    Extracts jobs defined via scheduler.<job>.schedule and scheduler.<job>.command labels.
    Returns a dict: {job_id: {schedule, command}}
    Each job_id is prefixed with the container ID to ensure uniqueness.
    """
    labels = container.labels or {}
    jobs = {}
    for key, value in labels.items():
        if not key.startswith("scheduler."):
            continue
        parts = key.split('.') # -> ["scheduler", "backup", "schedule"]
        if len(parts) != 3:
            continue  # skip keys like scheduler.enable
        _, job_name, prop = parts # unpacking values
		# parts[0] -> scheduler -> _ (ignore this value)
		# parts[1] -> backup -> save as job_name
		# parts[2] -> schedule -> save as prop
		# job_name = "backup"
		# prop = "schedule"
        job_id = f"{container.id[:12]}_{job_name}"
        if job_id not in jobs:
            jobs[job_id] = {}
        jobs[job_id][prop] = value
    return jobs

def list_scheduled_jobs():
    """Find all running containers with scheduler enabled and list their jobs."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Scanning running containers...")
    for container in client.containers.list():
        if not is_scheduler_enabled(container):
            continue
        jobs = get_scheduler_jobs(container)
        if jobs:
            print(f"{container.name} ({container.id[:12]}):")
            print(json.dumps(jobs, indent=2))

if __name__ == '__main__':
    list_scheduled_jobs()
