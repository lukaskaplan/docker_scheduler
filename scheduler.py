#!/usr/bin/env python3
import docker

# Connect to Docker socket
client = docker.DockerClient(base_url='unix://var/run/docker.sock')

for event in client.events(decode=True, filters={'Type': 'container'}):
  print(event, "\n")

event.close()
