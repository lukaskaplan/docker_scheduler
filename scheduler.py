#!/usr/bin/env python3
import docker
import json
import threading # for scheduler and watcher threads
import time # for endles while loop with time.sleep(1)
from datetime import datetime
from croniter import croniter, CroniterBadCronError # for checking cron syntax
# import python scheduler:
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Create and non-blocking scheduler
scheduler = BackgroundScheduler()

# Connect to Docker socket
docker_client = docker.DockerClient(base_url='unix://var/run/docker.sock')


def is_scheduler_enabled(container):
    """
    Check if the scheduler is enabled for the container.
    Returns: 
        - False whenever the label is "false" (or anything other than "true"), including if the label is missing.
        - True when label is true.
    """
    labels = container.labels or {}
    return labels.get("scheduler.enable", "").lower() == "true"


def extract_raw_jobs(labels):
    """Collect raw schedule/command pairs from scheduler.<job> labels."""
    raw_jobs = {}
    for key, value in (labels or {}).items():
        if not key.startswith("scheduler."):
            continue
        parts = key.split('.')
        if len(parts) != 3:
            continue  # skip keys like scheduler.enable
        _, job_name, prop = parts # unpacking values
            # parts[0] -> scheduler -> _ (ignore this value)
            # parts[1] -> backup -> save as job_name
            # parts[2] -> schedule -> save as prop
            # job_name = "backup"
            # prop = "schedule"
        if prop not in ("schedule", "command"):
            continue
        # Ensure a dict exists for this job_name
        if job_name not in raw_jobs:
            raw_jobs[job_name] = {}
        raw_jobs[job_name][prop] = value
    return  raw_jobs


def validate_jobs(container, raw_jobs):
    """
    Validate and build final job list:
      - must have both schedule and command
      - schedule must be valid cron expression
      - returns list of job dicts with id, container_name, container_id, schedule, command
    """
    jobs = []
    for job_name, props in raw_jobs.items():
        schedule = props.get("schedule")
        command = props.get("command")
        if not schedule or not command:
            continue  # skip incomplete jobs
        try:
            croniter(schedule)
        except (CroniterBadCronError, ValueError):
            #print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Invalid cron for {container.name}:{job_name} -> {schedule}")
            continue # skip invalid cron expressions
        cont_short_id = container.id[:12]
        job_id = f"{cont_short_id}_{job_name}"
        jobs.append({
            "id": job_id,
            "container_name": container.name,
            "container_id": cont_short_id,
            "schedule": schedule,
            "command": command
        })
    return jobs


# Helper to execute commands and capture output
def execute_job(cmd, cid):
    """Execute the command in the given container and print the output."""
    result = docker_client.containers.get(cid).exec_run(cmd, tty=True)
    output = result.output.decode('utf-8', errors='replace')
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Output for {cid}: {output}")


def sync_container(container):
    """Sync APScheduler jobs for a single container based on its labels."""
    cont_id = container.id[:12]
    prefix = f"{cont_id}_"
    # Remove existing jobs for this container
    for job in scheduler.get_jobs():
        if job.id.startswith(prefix):
            scheduler.remove_job(job.id)
            print(f"Removed job {job.id}")
    # If disabled, do nothing
    if not is_scheduler_enabled(container):
        return
    # Extract and validate raw job definitions
    raw = extract_raw_jobs(container.labels)
    jobs = validate_jobs(container, raw)
    # Inform about resync
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Resyncing jobs for container {container.name} ({cont_id})")
    # Schedule new jobs
    for job in jobs:
        trigger = CronTrigger.from_crontab(job["schedule"])
        scheduler.add_job(
            execute_job,
            trigger=trigger,
            args=[job["command"], job["container_id"]],
            id=job["id"],
            name=f"{job['container_name']}::{job['id']}"
        )
        print(f"Scheduled {job['id']}: {job['schedule']} {job['command']}")


def initial_sync():
    """Scan all running containers at startup and sync their jobs."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Performing initial sync...")
    for container in docker_client.containers.list():
        sync_container(container)


def watch_events():
    """Listen to Docker events and resync or remove jobs based on container lifecycle."""
    for event in docker_client.events(decode=True, filters={"type": "container"}):
        action = event.get("Action")
        cid = event.get("id")[:12]
        #print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Event {action} for container {cid}")
        # Attempt to fetch container; some events (destroy) may not find it
        try:
            cont = docker_client.containers.get(cid)
        except docker.errors.NotFound:
            cont = None

        # Actions that should (re)sync jobs: new start, unpause, rename, or config update
        if action in ("start", "update", "unpause", "rename") and cont:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Sync jobs for {cid} due to '{action}'")
            sync_container(cont)

        # Actions that should remove all jobs: stop, die, destroy, pause
        elif action in ("stop", "die", "destroy", "pause"):
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Removing jobs for {cid} due to '{action}'")
            prefix = f"{cid}_"
            for job in scheduler.get_jobs():
                if job.id.startswith(prefix):
                    scheduler.remove_job(job.id)
                    print(f"Removed job {job.id}")


if __name__ == '__main__':
    # Start APScheduler in its own background thread
    scheduler_thread = threading.Thread(target=scheduler.start, daemon=True)
    scheduler_thread.start()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] APScheduler background thread started")

    # Perform initial sync of existing containers    
    initial_sync()

    # Start Docker events watcher thread
    watcher_thread = threading.Thread(target=watch_events, daemon=True)
    watcher_thread.start()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Event watcher thread started")

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Scheduler service is running...")
    while True:
        time.sleep(1)

