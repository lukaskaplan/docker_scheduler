#!/usr/bin/env python3
import docker
import json
import logging # for logs
import os # for docker socket check
import signal # for signal handlers SIGINT and SIGTERM
import sys # for sys.exit(0)
import threading # for scheduler and watcher threads
import time # for endles while loop with time.sleep(1)
from datetime import datetime
# import python scheduler:
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Configure logging with timestamp
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
# We don't want APscheduler INFO logs
logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
logging.getLogger('apscheduler.jobstores.default').setLevel(logging.WARNING)
logging.getLogger('apscheduler.triggers.cron').setLevel(logging.WARNING)


try:
    if not os.path.exists('/var/run/docker.sock'):
        logger.error("Docker socket not found at /var/run/docker.sock. Exiting.")
        sys.exit(1)
    # Connect to Docker socket
    docker_client = docker.DockerClient(base_url='unix://var/run/docker.sock')
    docker_client.ping()
except Exception as e:
    logger.error(f"Cannot connect to Docker daemon: {e}")
    sys.exit(1)

# Create and non-blocking scheduler
scheduler = BackgroundScheduler()

# Graceful shutdown handler
def handle_exit(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    scheduler.shutdown(wait=False)
    sys.exit(0)

# Register signal handlers for SIGINT and SIGTERM
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


def is_scheduler_enabled(container):
    """
    Check if the scheduler is enabled for the container.
    Returns: 
        - False whenever the label is "false" (or anything other than "true"), 
          including if the label is missing.
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
      - returns list of job dicts with id, container_name, container_id,
        schedule, command
    """
    jobs = []
    for job_name, props in raw_jobs.items():
        schedule = props.get("schedule")
        command = props.get("command")
        if not schedule or not command:
            logger.warning(f"Incomplete job for {container.name}:{job_name} - missing schedule or command")
            continue  # skip incomplete jobs
        try:
            CronTrigger.from_crontab(schedule)
        except ValueError:
            logger.warning(f"Invalid schedule (not cron format) for {container.name}:{job_name} -> {schedule}")
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
def execute_job(job):
    """Execute the command in the given container and print the output."""
    cmd = job['command']
    cid = job['container_id']
    job_id = job['id']
    cont_name = job['container_name']
    logger.info(f"Runnig job {job_id} in {cont_name}")
    try:
        result = docker_client.containers.get(cid).exec_run(cmd, tty=True)
        output = result.output.decode('utf-8', errors='replace')
        exit_code = result.exit_code
        if exit_code != 0:
            # Log error without traceback
            logger.error(f"Job {job_id} in {cont_name} ({cid}) exited with code {exit_code}: {output}")
            return  # Do not raise to avoid traceback logging
        #logger.info(f"Output for {cont_name} ({job_id}):\n{output}")
    except Exception as e:
        # Log the exception without full traceback
        logger.exception(f"Error running job {job_id} in {cont_name} ({cid}): {e}")
        return


def sync_container(container):
    """Sync APScheduler jobs for a single container based on its labels."""
    cont_id = container.id[:12]
    prefix = f"{cont_id}_"
    # Remove existing jobs for this container
    for job in scheduler.get_jobs():
        if job.id.startswith(prefix):
            scheduler.remove_job(job.id)
            logger.info(f"Removed job {job.id}")
    # If disabled, do nothing
    if not is_scheduler_enabled(container):
        return
    # Extract and validate raw job definitions
    raw = extract_raw_jobs(container.labels)
    jobs = validate_jobs(container, raw)
    # Inform about resync
    logger.info(f"Resyncing jobs for container {container.name} ({cont_id})")
    # Schedule new jobs
    for job in jobs:
        trigger = CronTrigger.from_crontab(job["schedule"])
        scheduler.add_job(
            execute_job,
            trigger=trigger,
            args=[job],
            id=job["id"],
            name=f"{job['container_name']}::{job['id']}"
        )
        logger.info(f"Scheduled {job['id']}: {job['schedule']} {job['command']}")


def initial_sync():
    """Scan all running containers at startup and sync their jobs."""
    logger.info(f"Performing initial sync...")
    for container in docker_client.containers.list():
        sync_container(container)


def watch_events():
    """Listen to Docker events and resync or remove jobs based on container lifecycle."""
    for event in docker_client.events(decode=True, filters={"type": "container"}):
        action = event.get("Action")
        cid = event.get("id")[:12]
        #logger.info(f"Event {action} for container {cid}")
        # Attempt to fetch container; some events (destroy) may not find it
        try:
            cont = docker_client.containers.get(cid)
        except docker.errors.NotFound:
            cont = None
        # Set container name if exists, otherwise use the short container ID (cid)
        cont_name = cont.name if cont else cid

        # Actions that should (re)sync jobs: new start, unpause, rename, or config update
        if action in ("start", "update", "unpause") and cont:
            logger.info(f"Sync jobs for {cont_name} ({cid}) due to '{action}'")
            sync_container(cont)

        # Actions that should remove all jobs: stop, die, destroy, pause
        elif action in ("stop", "die", "destroy", "pause"):
            logger.info(f"Removing jobs for {cont_name} ({cid}) due to '{action}'")
            prefix = f"{cid}_"
            for job in scheduler.get_jobs():
                if job.id.startswith(prefix):
                    scheduler.remove_job(job.id)
                    logger.info(f"Removed job {job.id}")


if __name__ == '__main__':
    # Start APScheduler in its own background thread
    scheduler_thread = threading.Thread(target=scheduler.start, daemon=True)
    scheduler_thread.start()
    logger.info(f"APScheduler background thread started")

    # Perform initial sync of existing containers    
    initial_sync()

    # Start Docker events watcher thread
    watcher_thread = threading.Thread(target=watch_events, daemon=True)
    watcher_thread.start()
    logger.info(f"Event watcher thread started")

    logger.info(f"Scheduler service is running...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle_exit(None, None)

