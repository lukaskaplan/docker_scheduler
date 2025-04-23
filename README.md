# docker_scheduler

This tool allows you to define cron-like tasks for your containers using container labels.

**⚠️ Warning:** This project is still under development and not ready for production use.  
This README describes the intended functionality once the project is complete.

---

## Installation

```bash
# Clone the repository
git clone ...
cd docker_scheduler

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install required Python packages
pip install -r requirements.txt

chmod +x ./scheduler.py
./scheduler.py
```

## Docker 

```bash
# Clone the repository
git clone ...
cd docker_scheduler

docker build . -t scheduler

docker run -it --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -e TZ=Europe/Prague scheduler
```

## Description

You can assign special scheduler labels to your containers.
The scheduler container will read these labels and execute the defined commands at the specified times.

How it works:

 1. When the scheduler starts, it reads the labels of all running containers. If any containers define scheduler labels, it registers the corresponding jobs in memory and executes them according to their schedule — similar to a traditional cron.
 2. The scheduler listens to docker events. Whenever a container is started, stopped, or updated, the scheduler re-evaluates its configuration and updates the active jobs accordingly.

## How to Run It

 - The scheduler runs as a Docker container.
 - It requires read-only access to the Docker socket.

```yaml
services:
  scheduler:
    image: scheduler
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      TZ: Europe/Prague
```

## Configuring Container Labels

 - Each container can define one or more jobs via labels.
 - Each job must have a unique name (for this one container).

```yaml
services:
  your_container:
    image: some:image
    labels:
      scheduler.enable: true
      scheduler.<job-name>.schedule: "* * * * *"
      scheduler.<job-name>.command: "uname -a"
```

For example, a container could define multiple jobs like this:

```yaml
labels:
  scheduler.enable: true
  scheduler.backup.schedule: "0 2 * * *"
  scheduler.backup.command: "pg_dumpall -U postgres > /backup/db.sql"

  scheduler.cleanup.schedule: "0 4 * * *"
  scheduler.cleanup.command: "rm -rf /tmp/*"
```
