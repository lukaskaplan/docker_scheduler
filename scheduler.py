#!/usr/bin/env python3
import docker
import json
from datetime import datetime
# for checking cron syntax:
from croniter import croniter, CroniterBadCronError

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
    Extracts and validates jobs defined via scheduler.<job>.schedule and scheduler.<job>.command labels.
    Returns a list of job dicts with the following keys:
      - id: unique job id (container_id + job_name)
      - container_name: name of the container
      - container_id: short ID of the container
      - schedule: cron expression
      - command: command to execute
    Only jobs with both schedule and command valid cron expressions are returned.
    """
    labels = container.labels or {}
    raw_jobs = {}

    # Collect only schedule and command entries
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
        if prop not in ("schedule", "command"):
            continue # ignore other scheduler.* labels

        # Ensure a dict exists for this job_name
        if job_name not in raw_jobs:
            raw_jobs[job_name] = {}
        raw_jobs[job_name][prop] = value

    jobs = []
    # Validate and build job list
    for job_name, props in raw_jobs.items():
        schedule = props.get("schedule")
        command = props.get("command")
        if not schedule or not command:
            continue  # skip incomplete jobs
        try:
            croniter(schedule)
        except (CroniterBadCronError, ValueError):
            continue # skip invalid cron expressions
        container_short_id = container.id[:12]
        job_id = f"{container_short_id}_{job_name}"
        jobs.append({
            "id": job_id,
            "container_name": container.name,
            "container_id": container_short_id,
            "schedule": schedule,
            "command": command
        })

    return jobs

def list_scheduled_jobs():
    """Find all running containers with scheduler enabled and list their jobs."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Scanning running containers...")
    for container in client.containers.list():
        if not is_scheduler_enabled(container):
            continue
        jobs = get_scheduler_jobs(container)
        if jobs:
            print(f"Container {container.name} ({container.id[:12]}) has {len(jobs)} scheduled job(s):")
            for job in jobs:
                print(f"  - {job['id']}: container_name={job['container_name']}, container_id={job['container_id']}, schedule='{job['schedule']}', command='{job['command']}'")


if __name__ == '__main__':
    list_scheduled_jobs()
