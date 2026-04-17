#!/bin/bash

chown -R 1000:1000 /app/uploads /app/instance 2>/dev/null || true

exec python app.py
