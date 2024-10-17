# Use official Python image as base, set to amd64 architecture
FROM --platform=linux/amd64 python:3.9-slim

# Install system dependencies and build tools
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install quickfix requests

# Set working directory
WORKDIR /app

# Copy application files
COPY . /app
