# Use Rocky Linux 9.3 as the base image and set platform to linux/amd64
FROM --platform=linux/amd64 rockylinux:9.3
ENV DEBIAN_FRONTEND=noninteractive

# Update the system
RUN dnf -y update

# Upgrade to Rocky Linux 9.4 (optional)
RUN dnf -y install dnf-plugin-system-upgrade \
    && dnf -y system-upgrade download --releasever=9.4

# Install development tools and necessary dependencies
RUN dnf -y group install "Development Tools"
RUN dnf -y install epel-release
RUN dnf -y install cmake clang clang-devel clang-tools-extra
RUN dnf -y install sudo wget unzip python3-pip jq python3-devel

# Install QuickFIX and requests using pip
RUN pip3 install quickfix requests

WORKDIR /app

COPY . /app
